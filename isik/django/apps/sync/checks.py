"""
`isik.E027` - a sync setting or `syncs()` declaration that can't work: see `sync_problems()`.
"""

from datetime import timedelta

from django.conf import settings
from django.core.checks import Error, Tags
from django.core.exceptions import FieldDoesNotExist
from rest_framework.serializers import BaseSerializer
from rest_framework.settings import api_settings

from isik._internal.translation import gettext as _
from isik.django.apps.common.checks import Check, isik_check
from isik.django.apps.common.db.concurrency import (
    PARTS,
    concurrency_field_name,
    concurrency_relations,
    concurrency_strategy,
)
from isik.django.apps.sync import conf
from isik.django.apps.sync.declarations import IDENTIFIER, PARENT_LOGS, USER_KIND, synced_models
from isik.django.apps.sync.drf import ClientPrimaryKeyMixin
from isik.django.apps.sync.push import PushBackend
from isik.django.apps.sync.reading import view_for, viewset_of
from isik.django.conf import django_setting


AREA = "ISIK['DJANGO']['SYNC']"


def _is_period(value):
    return value is None or (isinstance(value, timedelta) and value > timedelta(0))


def _is_count(value):
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 1)


def setting_problems():
    """What's wrong with `ISIK['DJANGO']['SYNC']`, one sentence each."""
    problems = []
    unknown = sorted(set(conf.configured()) - set(conf.DEFAULTS))
    if unknown:
        problems.append(_("%(area)s has keys it doesn't know: %(keys)s.") % {"area": AREA, "keys": ", ".join(unknown)})
    for name in ("RETENTION", "OFFLINE_PERIOD", "LAG_WARNING"):
        if not _is_period(conf.setting(name)):
            problems.append(
                _("%(area)s['%(name)s'] is a timedelta longer than none, or None.") % {"area": AREA, "name": name}
            )
    if not _is_count(conf.setting("PAGE_LIMIT")):
        problems.append(_("%(area)s['PAGE_LIMIT'] is a whole number of at least 1, or None.") % {"area": AREA})
    problems += _push_problems()
    problems += _offline_problems()
    return problems


def _push_problems():
    push = conf.setting("PUSH")
    if not isinstance(push, dict) or set(push) - set(conf.PUSH_KEYS):
        return [_("%(area)s['PUSH'] is a dict of BACKEND and OPTIONS.") % {"area": AREA}]
    try:
        backend = conf.push_backend_class()
    except ImportError as error:
        return [_("%(area)s['PUSH']['BACKEND'] can't be loaded: %(error)s") % {"area": AREA, "error": error}]
    if not (isinstance(backend, type) and issubclass(backend, PushBackend)):
        return [_("%(area)s['PUSH']['BACKEND'] is a PushBackend subclass, or its dotted path.") % {"area": AREA}]
    options = push.get("OPTIONS") or {}
    problems = [
        _("%(area)s['PUSH']['OPTIONS']['%(name)s'] is a whole number of at least 1, or None.")
        % {"area": AREA, "name": name}
        for name in ("MAX_ENTRIES", "MAX_BYTES")
        if not _is_count(options.get(name))
    ]
    most, upload = options.get("MAX_BYTES"), settings.DATA_UPLOAD_MAX_MEMORY_SIZE
    if not problems and most is not None and upload is not None and most > upload:
        problems.append(
            _(
                "%(area)s['PUSH']['OPTIONS']['MAX_BYTES'] is %(most)s, and DATA_UPLOAD_MAX_MEMORY_SIZE refuses a "
                "body over %(upload)s before a push sees it - make it at most that."
            )
            % {"area": AREA, "most": most, "upload": upload}
        )
    scope = options.get("THROTTLE_SCOPE")
    if scope is not None and scope not in api_settings.DEFAULT_THROTTLE_RATES:
        problems.append(
            _("%(area)s['PUSH']['OPTIONS']['THROTTLE_SCOPE'] is %(scope)r, which DEFAULT_THROTTLE_RATES gives no rate.")
            % {"area": AREA, "scope": scope}
        )
    return problems


def _offline_problems():
    offline = conf.setting("OFFLINE_PERIOD")
    if not isinstance(offline, timedelta):
        return []
    problems = []
    retention = conf.setting("RETENTION")
    if isinstance(retention, timedelta) and retention < offline:
        problems.append(
            _(
                "%(area)s['RETENTION'] is shorter than its OFFLINE_PERIOD: a client offline that long comes back to a "
                "cursor pruning passed, and pulls everything again."
            )
            % {"area": AREA}
        )
    claims = django_setting("IDEMPOTENCY", "RETENTION")
    if isinstance(claims, timedelta) and claims <= offline:
        problems.append(
            _(
                "ISIK['DJANGO']['IDEMPOTENCY']['RETENTION'] is no longer than %(area)s['OFFLINE_PERIOD']: a client "
                "offline that long pushes keys whose claims are gone, and a write whose answer it lost runs twice."
            )
            % {"area": AREA}
        )
    return problems


def _serializer_problems(synced, viewset):
    label = synced.model._meta.label
    problems = []
    retrieve = view_for(viewset, None, "retrieve").get_serializer_class()
    fields = retrieve().fields
    nested = sorted(name for name, field in fields.items() if isinstance(field, BaseSerializer))
    if nested:
        problems.append(
            _("%(model)s is synced, and its REST representation nests %(fields)s - keep keys as ids, nothing nested.")
            % {"model": label, "fields": ", ".join(nested)}
        )
    missing = [name for name in (synced.model._meta.pk.name, synced.scope) if name not in fields]
    if missing:
        problems.append(
            _(
                "%(model)s is synced, and its REST representation leaves out %(fields)s - a client needs its key and "
                "its scope."
            )
            % {"model": label, "fields": ", ".join(missing)}
        )
    create = view_for(viewset, None, "create").get_serializer_class()
    if not issubclass(create, ClientPrimaryKeyMixin):
        problems.append(
            _(
                "%(model)s is synced, and %(serializer)s creates its rows under ids of its own - give it "
                "ClientPrimaryKeyMixin, so a client offline can make a row's id."
            )
            % {"model": label, "serializer": create.__name__}
        )
    return problems


def _membership_problems(synced):
    memberships = synced.memberships
    label = synced.model._meta.label
    try:
        model = memberships.resolve()
    except (LookupError, ValueError):
        return [
            _("%(model)s names memberships in %(memberships)s, which isn't a model.")
            % {"model": label, "memberships": memberships.label}
        ]
    problems = []
    for name in (memberships.user, memberships.scope):
        try:
            model._meta.get_field(name)
        except FieldDoesNotExist:
            problems.append(
                _("%(model)s names memberships by %(memberships)s.%(field)s, which isn't a field.")
                % {"model": label, "memberships": memberships.label, "field": name}
            )
    return problems


def model_problems():
    """What's wrong with each `syncs()` declaration, one sentence each."""
    problems = []
    synced = synced_models()
    kinds = {}
    for each in synced.values():
        label = each.model._meta.label
        try:
            each.model._meta.get_field(each.scope)
        except FieldDoesNotExist:
            problems.append(
                _("%(model)s is scoped by %(field)r, which isn't a field.") % {"model": label, "field": each.scope}
            )
            continue
        kind = each.kind
        if kind is None:
            problems.append(
                _("%(model)s is scoped by %(field)s, which isn't a key - say its kind: syncs(kind=...).")
                % {"model": label, "field": each.scope}
            )
            continue
        if not IDENTIFIER.match(kind):
            problems.append(
                _("%(model)s's scope kind %(kind)r isn't a plain lowercase name.") % {"model": label, "kind": kind}
            )
        if kind == USER_KIND and each.memberships is not None:
            problems.append(
                _("%(model)s names memberships for the kind user - a person is in their own user scope alone.")
                % {"model": label}
            )
        if each.memberships is not None:
            problems += _membership_problems(each)
            kinds.setdefault(kind, set()).add(each.memberships)
        else:
            kinds.setdefault(kind, set())
        if concurrency_strategy(each.model) == PARTS:
            problems.append(
                _(
                    "%(model)s is synced, and its ETag reads concurrency_parts() - what they read changes with no "
                    "change the feed hears of."
                )
                % {"model": label}
            )
        if concurrency_field_name(each.model) is None:
            problems.append(
                _("%(model)s is synced, and has no concurrency token - the feed sends each row's ETag.")
                % {"model": label}
            )
        unlogged = [name for name in concurrency_relations(each.model) if (each.name, name) not in PARENT_LOGS]
        if unlogged:
            problems.append(
                _(
                    "%(model)s is synced, and its version covers %(relations)s, whose rows log no change of it - a "
                    "change to one moves its ETag with no change the feed hears of."
                )
                % {"model": label, "relations": ", ".join(unlogged)}
            )
        viewset = viewset_of(each)
        if viewset is None:
            problems.append(
                _("%(model)s is synced, and no viewset serves it - register one, or name it: syncs(viewset=...).")
                % {"model": label}
            )
        else:
            problems += _serializer_problems(each, viewset)
    for kind, declared in sorted(kinds.items()):
        if kind != USER_KIND and not declared:
            problems.append(
                _("Models are scoped by the kind %(kind)s, and none names its memberships: syncs(memberships=...).")
                % {"kind": kind}
            )
        if len(declared) > 1:
            problems.append(_("Models scoped by the kind %(kind)s name different memberships.") % {"kind": kind})
    return problems


def sync_problems():
    """Every sync setting or declaration that can't work, one sentence each."""
    return setting_problems() + model_problems()


@isik_check(Check.SYNC, Tags.models)
def sync_can_work(options, **kwargs):
    """A sync setting or declaration that can't work - see `sync_problems()`."""
    hint = _("See isik.django.apps.sync.")
    return [Error(problem, hint=hint, id="isik.E027") for problem in sync_problems()]
