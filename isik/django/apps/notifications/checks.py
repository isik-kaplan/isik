"""
`isik.E014` - a notifications setting, or a kind's declaration, that can't work as written. Every problem
is found at startup rather than at the first notice: `notification_problems()` lists them. `isik.W004`
reports a declared kind the database retired, `isik.W005` a snapshot field that looks secret, `isik.W006`
nothing isik can see starting a fan-out, `isik.W007` a webhook sent inside the request that wrote it, and
`isik.W008` endpoints keyed to a user with no `ENDPOINT_OWNER_FIELD`, and `isik.W009` a declared kind the
database has no policy row for.
"""

import ipaddress
import re

import pgtrigger
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.checks import Error, Tags, Warning
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.template import TemplateDoesNotExist
from django.template.loader import get_template

from isik._internal.reasons import require_exemption
from isik._internal.translation import gettext as _
from isik.django.apps.common.checks import Check, CheckOption, isik_check
from isik.django.apps.notifications import bases, conf, exemptions, hooks, swapping
from isik.django.apps.notifications import kinds as declarations
from isik.django.apps.notifications.audience.bases import AbstractForceableSubscription
from isik.django.apps.notifications.audience.swapping import DEFAULTS as AUDIENCE_DEFAULTS
from isik.django.apps.notifications.audience.swapping import (
    ENDPOINT_KIND_MODEL,
    INBOX_ITEM_MODEL,
    SUBSCRIPTION_MODEL,
    get_endpoint_model,
    get_subscription_model,
)
from isik.django.apps.notifications.channels import get_channel, registered
from isik.django.apps.notifications.channels.email import send_from_mjml_templates, send_from_templates
from isik.django.apps.notifications.choices import Policy, Scope, Source
from isik.django.apps.notifications.partitions import PartitionSource, _concrete
from isik.django.apps.notifications.swapping import DEFAULTS as LEDGER_DEFAULTS
from isik.django.apps.notifications.swapping import KIND_POLICY_MODEL


LEDGER_APP = "isik.django.apps.notifications"
# A model of any of these is the ledger's - isik's own, or a project's owning it.
LEDGER_BASES = (
    bases.AbstractNoticeKindPolicy,
    bases.AbstractNotice,
    bases.AbstractFanOutShard,
    bases.AbstractDelivery,
    bases.AbstractWebhookDelivery,
    bases.AbstractEmailDelivery,
    bases.AbstractInboxDelivery,
    bases.AbstractDeliveryAttempt,
)
AUDIENCE_APP = "isik.django.apps.notifications.audience"
RECORDINGS = ("all", "failures", "none")
# RFC 9110's token - what a header name may hold.
TOKEN = re.compile(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+")
# What a field's name says when it holds a secret - the user model's password among them.
SECRET_LOOKING = re.compile(r"password|secret|token", re.IGNORECASE)
DJANGO_TEMPLATES = "django.template.backends.django.DjangoTemplates"


def _sent_on(channel):
    return any(channel in kind.enabled_channels for kind in declarations.kinds().values())


def setting_problems():
    """The problems with `ISIK_NOTIFICATIONS` and the apps installed."""
    problems = []
    unknown = sorted(set(conf.configured()) - set(conf.DEFAULTS))
    if unknown:
        problems.append(_("ISIK_NOTIFICATIONS has keys it doesn't know: %(keys)s.") % {"keys": ", ".join(unknown)})
    if conf.setting("RECORD_ATTEMPTS") not in RECORDINGS:
        problems.append(_("ISIK_NOTIFICATIONS' RECORD_ATTEMPTS is one of all, failures or none."))
    if conf.setting("MAX_ATTEMPTS") < 1:
        problems.append(_("ISIK_NOTIFICATIONS' MAX_ATTEMPTS is at least 1."))
    if not conf.setting("BACKOFF"):
        problems.append(_("ISIK_NOTIFICATIONS' BACKOFF names at least one delay."))
    if (conf.setting("PARTITIONS") is None) != (conf.setting("IN_PARTITION") is None):
        problems.append(_("ISIK_NOTIFICATIONS sets one of PARTITIONS and IN_PARTITION - set both, or neither."))
    unregistered = [name for name in conf.setting("CHANNELS") if name not in registered]
    if unregistered:
        problems.append(
            _("ISIK_NOTIFICATIONS' CHANNELS names channels nothing registered: %(names)s.")
            % {"names": ", ".join(unregistered)}
        )
    for name in conf.setting("CHANNELS"):
        if name in registered:
            try:
                get_channel(name)
            except ImportError as missing:
                # Found now, not at the first fan-out - which would set every notice aside, its other channels too.
                problems.append(
                    _("ISIK_NOTIFICATIONS' CHANNELS turns on %(name)s, which can't be loaded: %(error)s")
                    % {"name": name, "error": missing}
                )
    if _sent_on("webhook"):
        name = conf.setting("PROJECT_NAME")
        if conf.signature_header() is None:
            problems.append(
                _(
                    "The webhook channel signs with X-<PROJECT_NAME>-Signature - set ISIK_NOTIFICATIONS' "
                    "PROJECT_NAME, or name the header in SIGNATURE['header']."
                )
            )
        elif name and not TOKEN.fullmatch(name):
            problems.append(
                _(
                    "ISIK_NOTIFICATIONS' PROJECT_NAME %(name)r can't be part of a header name - use letters, "
                    "digits and -."
                )
                % {"name": name}
            )
    if apps.is_installed(AUDIENCE_APP) != apps.is_installed(LEDGER_APP):
        alone, missing = (AUDIENCE_APP, LEDGER_APP) if apps.is_installed(AUDIENCE_APP) else (LEDGER_APP, AUDIENCE_APP)
        problems.append(_("%(alone)s is installed without %(missing)s.") % {"alone": alone, "missing": missing})
    problems += _kept_guard_problems()
    problems += _untracked_force_problems()
    allowed = conf.setting("ALLOW_PRIVATE_TARGETS")
    if allowed not in (True, False):
        try:
            for network in allowed:
                ipaddress.ip_network(network)
        except (TypeError, ValueError):
            problems.append(
                _('ISIK_NOTIFICATIONS\' ALLOW_PRIVATE_TARGETS is True, False, or networks such as "10.1.0.0/16".')
            )
    default = conf.setting("DEFAULT_PARTITION")
    if default is not None and not isinstance(default, PartitionSource):
        problems.append(
            _("ISIK_NOTIFICATIONS' DEFAULT_PARTITION is a resolver from isik.django.apps.notifications.partitions.")
        )
    if not isinstance(conf.setting("ACTOR_CONTEXT_KEY"), (str, type(None))):
        problems.append(_("ISIK_NOTIFICATIONS' ACTOR_CONTEXT_KEY is a history context key, or None."))
    keys = conf.setting("NOTICE_CONTEXT_KEYS")
    if isinstance(keys, str) or not all(isinstance(key, str) for key in keys):
        problems.append(_('ISIK_NOTIFICATIONS\' NOTICE_CONTEXT_KEYS is history context keys, such as ("user",).'))
    for name, exemption_type in (
        ("FAN_OUT_BY", exemptions.ScheduledElsewhere),
        ("SENDS_INLINE", exemptions.SendsInline),
        ("ENDPOINTS_SHARED", exemptions.SharedEndpoints),
    ):
        try:
            require_exemption("ISIK_NOTIFICATIONS", name, conf.setting(name), exemption_type)
        except ImproperlyConfigured as refused:
            problems.append(str(refused))
    problems += _template_problems()
    return problems


def _untracked_force_problems():
    """A forceable subscription model with no history - who forced it, when and why would be lost on release."""
    if not apps.is_installed(AUDIENCE_APP):
        return []
    model = get_subscription_model()
    if not issubclass(model, AbstractForceableSubscription) or "pgh_event_model" in dir(model):
        return []
    return [
        _(
            "%(model)s can be forced, and has no history - who forced it, when and why is gone once it's released. "
            "Track it with @track_events()."
        )
        % {"model": model._meta.label}
    ]


def _kept_guard_problems():
    """
    isik's own guarded audience models, kept beside a policy model of the project's: their shipped migration
    guards them against isik's policy table, which the project then never makes.
    """
    if not apps.is_installed(AUDIENCE_APP) or not apps.get_model(LEDGER_DEFAULTS[KIND_POLICY_MODEL])._meta.swapped:
        return []
    kept = [
        setting
        for setting in (SUBSCRIPTION_MODEL, INBOX_ITEM_MODEL, ENDPOINT_KIND_MODEL)
        if not apps.get_model(AUDIENCE_DEFAULTS[setting])._meta.swapped
    ]
    if not kept:
        return []
    return [
        _(
            "%(policy)s is the project's, and %(kept)s still isik's - whose shipped guard reads isik's policy "
            "table. Own those too."
        )
        % {"policy": KIND_POLICY_MODEL, "kept": ", ".join(kept)}
    ]


def _template_problems():
    """While a sender here renders the emails: Django's template backend, and each email kind's templates."""
    if conf.setting("EMAIL_SENDER") not in (send_from_templates, send_from_mjml_templates):
        return []
    backends = [backend["BACKEND"] for backend in settings.TEMPLATES]
    if DJANGO_TEMPLATES not in backends:
        return [
            _(
                "The default EMAIL_SENDER renders its templates with Django's template backend, which TEMPLATES "
                "doesn't include - add it, or set EMAIL_SENDER to a sender of your own."
            )
        ]
    problems = []
    for kind in declarations.kinds().values():
        # A recorded kind's email is rendered and sent by its caller.
        if "email" not in kind.enabled_channels or kind.source == Source.RECORDED:
            continue
        for name in ("subject.txt", "body.txt"):
            try:
                get_template(f"notifications/{kind.name}/{name}")
            except TemplateDoesNotExist:
                problems.append(
                    _("%(kind)s is sent by email, and there's no template notifications/%(kind)s/%(name)s.")
                    % {"kind": kind.name, "name": name}
                )
    return problems


def _is_event_model(model):
    from pghistory.models import Event

    return issubclass(model, Event)


def _trigger_problems(kind):
    model, problems = kind.model, []
    if issubclass(model, LEDGER_BASES) or _is_event_model(model):
        problems.append(
            _(
                "%(kind)s is declared on %(model)s - a ledger or event model, which records changes rather than "
                "making them."
            )
            % {"kind": kind.name, "model": model._meta.label}
        )
    for name in kind.snapshot:
        if _concrete(model, name) is None:
            problems.append(
                _("%(kind)s's snapshot names %(field)s, which %(model)s has no column for.")
                % {"kind": kind.name, "field": name, "model": model._meta.label}
            )
    for name in sorted(set(kind.sensitive) - set(kind.snapshot)):
        problems.append(
            _("%(kind)s says why its snapshot carries %(field)s, which it doesn't carry.")
            % {"kind": kind.name, "field": name}
        )
    source = kind.partition or conf.setting("DEFAULT_PARTITION")
    if kind.partition is not None and not isinstance(kind.partition, PartitionSource):
        problems.append(
            _("%(kind)s's partition= is a resolver from isik.django.apps.notifications.partitions.")
            % {"kind": kind.name}
        )
    elif source is not None:
        problems += source.check(model, kind)
    elif conf.partitioned():
        problems.append(
            _(
                "%(kind)s says nowhere which partition its row belongs to - give it partition=, or set "
                "ISIK_NOTIFICATIONS' DEFAULT_PARTITION."
            )
            % {"kind": kind.name}
        )
    uncovered = [name for name in kind.audience.snapshot_fields() if name not in kind.snapshot]
    if uncovered:
        problems.append(
            _(
                "%(kind)s's audience reads %(fields)s from its snapshot, which doesn't carry them - add them to "
                "snapshot, or read the live row with at=LIVE."
            )
            % {"kind": kind.name, "fields": ", ".join(uncovered)}
        )
    if (model, kind.trigger_name) not in {(model, trigger.name) for model, trigger in pgtrigger.registered()}:
        problems.append(_("%(kind)s has no trigger registered.") % {"kind": kind.name})
    return problems


def _vocabulary_problems():
    """Where the vocabulary and the behavior declared for it disagree."""
    problems, behaviors = [], declarations.kinds()
    names = [str(name) for name in declarations.vocabulary]
    for name in sorted({name for name in names if names.count(name) > 1}):
        problems.append(_("%(kind)s is declared in the vocabulary more than once.") % {"kind": name})
    for name in declarations.vocabulary:
        kind = behaviors.get(str(name))
        if kind is None:
            problems.append(
                _(
                    "%(kind)s is declared, and nothing writes it - give a model @notifies(%(kind)s, ...), or "
                    "declare_kind() its audience and policy."
                )
                % {"kind": name}
            )
        elif sorted(kind.variants) != sorted(name.variants):
            problems.append(
                _("%(kind)s writes the variants %(written)s, and declares %(declared)s.")
                % {
                    "kind": name,
                    "written": ", ".join(sorted(kind.variants)) or "none",
                    "declared": ", ".join(sorted(name.variants)) or "none",
                }
            )
    return problems


def _undeclared_problems():
    """With `REQUIRE_DECLARED_KINDS`, each trigger-sourced kind given a bare name instead of a `KindName`."""
    if not conf.setting("REQUIRE_DECLARED_KINDS"):
        return []
    named = {str(name) for name in declarations.vocabulary}
    return [
        _("%(kind)s is a bare name - declare it with declare_kind() in your kinds module, and pass that.")
        % {"kind": kind.name}
        for kind in declarations.kinds().values()
        if kind.name not in named
    ]


def kind_problems():
    """The problems with each declared kind."""
    problems = _vocabulary_problems() + _undeclared_problems()
    names = [kind.name for kind in declarations.declared]
    for name in sorted({name for name in names if names.count(name) > 1}):
        problems.append(_("%(kind)s is declared more than once.") % {"kind": name})
    for kind in declarations.kinds().values():
        # Recorded, a kind is sent by its caller: no audience, scope or channel of isik's is in it.
        if kind.source == Source.RECORDED:
            continue
        if kind.policy not in Policy.values:
            problems.append(
                _("%(kind)s's policy is a Policy, not %(policy)r.") % {"kind": kind.name, "policy": kind.policy}
            )
        if kind.scope == Scope.SINGLE_PARTITION and kind.audience.spans_partitions:
            problems.append(
                _("%(kind)s is single-partition, and its audience %(audience)s spans partitions.")
                % {"kind": kind.name, "audience": kind.audience}
            )
        if kind.scope == Scope.CROSS_PARTITION and not conf.partitioned():
            problems.append(
                _("%(kind)s is cross-partition, and ISIK_NOTIFICATIONS declares no partitions.") % {"kind": kind.name}
            )
        disabled = [name for name in kind.enabled_channels if name not in conf.setting("CHANNELS")]
        if disabled:
            problems.append(
                _("%(kind)s is sent on %(channels)s, which ISIK_NOTIFICATIONS' CHANNELS doesn't turn on.")
                % {"kind": kind.name, "channels": ", ".join(disabled)}
            )
        if kind.source == Source.TRIGGER:
            problems += _trigger_problems(kind)
    return problems


def _mirrored(databases):
    """{kind: retired_at} of the declared kinds' policy rows, in each database given that has its table."""
    rows = []
    for alias in databases or ():
        try:
            rows.append(
                dict(
                    swapping.get_kind_policy_model()
                    .objects.using(alias)
                    .filter(kind__in=list(declarations.kinds()))
                    .values_list("kind", "retired_at")
                )
            )
        except DatabaseError:
            # Not migrated yet - there are no rows to read.
            continue
    return rows


def retired_problems(databases):
    """The declared kinds the database has retired - read only when the check is given databases."""
    retired = {kind for rows in _mirrored(databases) for kind, retired_at in rows.items() if retired_at is not None}
    return [
        _("%(kind)s is declared, and retired - its trigger writes nothing. Remove its declaration.") % {"kind": name}
        for name in sorted(retired)
    ]


def unmirrored_problems(databases):
    """The declared kinds a database has no policy row for - their notices aren't written until it has one."""
    missing = {kind for rows in _mirrored(databases) for kind in declarations.kinds() if kind not in rows}
    return [
        _(
            "%(kind)s is declared, and the database has no policy row for it - nothing writes its notices until "
            "migrate writes one, as the migrate this is shown by will."
        )
        % {"kind": name}
        for name in sorted(missing)
    ]


def notification_problems():
    """Every problem with the notifications' settings and declared kinds."""
    return setting_problems() + kind_problems()


def sensitive_snapshot_warnings():
    """Each trigger-sourced kind's snapshot field that looks secret, and says no reason why it's carried."""
    return [
        _(
            "%(kind)s's snapshot carries %(field)s, which looks secret - the snapshot is copied into the ledger and "
            "every channel. Leave it out, or say why with sensitive={%(field)r: SensitiveSnapshotField(reason=...)}."
        )
        % {"kind": kind.name, "field": name}
        for kind in declarations.kinds().values()
        if kind.source == Source.TRIGGER
        for name in kind.snapshot
        if name not in kind.sensitive and SECRET_LOOKING.search(name)
    ]


def fan_out_warnings():
    """Nothing isik can see starts a fan-out, and `FAN_OUT_BY` doesn't say what does."""
    if hooks.started_after_writes() or hooks.fan_out_scheduled() or conf.setting("FAN_OUT_BY"):
        return []
    return [
        _(
            "Nothing starts a fan-out, so no notice is ever sent: merge beat_schedule() into the Celery app's beat, "
            "add FanOutAfterWritesMiddleware, set FAN_OUT_AFTER_TASKS, or - when a cron or django-celery-beat "
            "runs it - say so with FAN_OUT_BY=ScheduledElsewhere(reason=...)."
        )
    ]


def inline_send_warnings():
    """Tasks run inline and a fan-out follows a write, so a webhook is sent inside the writing request."""
    if conf.setting("SENDS_INLINE") or not (hooks.runs_inline() and hooks.started_after_writes()):
        return []
    if not _sent_on("webhook"):
        return []
    return [
        _(
            "Celery runs tasks inline (task_always_eager) and a fan-out follows each write, so a webhook is sent "
            "inside the request or task that wrote its notice - an endpoint can hold it for ENDPOINT_TIMEOUT. Run "
            "a worker, or say why that's fine with SENDS_INLINE=SendsInline(reason=...)."
        )
    ]


def shared_endpoint_warnings():
    """The endpoint model keyed to the user model while `ENDPOINT_OWNER_FIELD` is unset and no reason given."""
    if not apps.is_installed(AUDIENCE_APP) or conf.setting("ENDPOINT_OWNER_FIELD") or conf.setting("ENDPOINTS_SHARED"):
        return []
    endpoint = get_endpoint_model()
    keys = [
        field.name
        for field in endpoint._meta.get_fields()
        if field.concrete and field.related_model is get_user_model()
    ]
    if not keys:
        return []
    return [
        _(
            "%(model)s is keyed to the user model by %(keys)s, and ISIK_NOTIFICATIONS' ENDPOINT_OWNER_FIELD is "
            "unset - so every endpoint is sent every user's notices of its kinds. Set it to the owner's key, or "
            "say why they're the deployment's with ENDPOINTS_SHARED=SharedEndpoints(reason=...)."
        )
        % {"model": endpoint._meta.label, "keys": ", ".join(keys)}
    ]


def kind_text_problems():
    """
    Each kind that can't say what it is to people: one without a label - which a client listing kinds would
    show as its bare name - and one sent to the inbox without a message, which its items would show as data.
    """
    names = dict.fromkeys([*map(str, declarations.vocabulary), *declarations.kinds()])
    problems = []
    for name in names:
        text = declarations.text_of(name)
        kind = declarations.get_kind(name)
        if text.label is None:
            problems.append(
                _("%(kind)s has no label - a client listing kinds can only show its name. Give it label=.")
                % {"kind": name}
            )
        if kind is not None and "inbox" in kind.enabled_channels and text.message is None:
            problems.append(
                _("%(kind)s is sent to the inbox with no message - its items read as data. Give it message=.")
                % {"kind": name}
            )
    return problems


def endpoint_owner_problems(urlconf=None):
    """
    Each routed endpoint or delivery view scoping by an `owner_field` that isn't `ENDPOINT_OWNER_FIELD` -
    its callers would see only their endpoints while the channel sends each one other owners' notices.
    """
    if not apps.is_installed("rest_framework"):
        return []
    from isik.django.apps.notifications.drf import OwnedByCaller
    from isik.django.drf.coverage import routed_actions

    sending = conf.setting("ENDPOINT_OWNER_FIELD")
    views = dict.fromkeys(routed.view for routed in routed_actions(urlconf) if issubclass(routed.view, OwnedByCaller))
    return [
        _(
            "%(view)s scopes endpoints by %(field)r, and ISIK_NOTIFICATIONS' ENDPOINT_OWNER_FIELD is %(sending)r - "
            "set it to %(field)r, or each endpoint is sent notices its owner isn't told of."
        )
        % {"view": view.__name__, "field": view.owner_field, "sending": sending}
        for view in views
        if view.owner_field is not None and view.owner_field != sending
    ]


@isik_check(Check.NOTIFICATIONS, Tags.models)
def notifications_can_work(options, databases=None, **kwargs):
    """A notifications setting or kind declaration that can't work - see `notification_problems()`."""
    hint = _("See isik.django.apps.notifications.")
    return (
        [Error(problem, hint=hint, id="isik.E014") for problem in notification_problems()]
        + [
            Error(problem, hint=hint, id="isik.E014")
            for problem in endpoint_owner_problems(options[CheckOption.URLCONFS])
        ]
        + [Warning(problem, hint=hint, id="isik.W004") for problem in retired_problems(databases)]
        + [Warning(problem, hint=hint, id="isik.W005") for problem in sensitive_snapshot_warnings()]
        + [Warning(problem, hint=hint, id="isik.W006") for problem in fan_out_warnings()]
        + [Warning(problem, hint=hint, id="isik.W007") for problem in inline_send_warnings()]
        + [Warning(problem, hint=hint, id="isik.W008") for problem in shared_endpoint_warnings()]
        + [Warning(problem, hint=hint, id="isik.W009") for problem in unmirrored_problems(databases)]
    )
