"""
Declaring kinds, in two steps: the vocabulary - `declare_kind()`, every kind a project has, in one module
someone reviews - and each one's behavior: `notifies()` on the model a trigger-sourced kind watches, or the
audience and policy given to `declare_kind()` for one the application raises with `raise_notice()`. The
registry they're collected in is what the `NoticeKindPolicy` mirror, the checks, fan-out and
`manage.py notices` read:

    # notices.py - the vocabulary
    INVOICE_VOIDED = declare_kind("invoice_voided")
    REMINDER = declare_kind("reminder", variants=("daily", "weekly"), audience=Subscribers, policy=Policy.DEFAULT_OFF)

    # models.py
    @notifies(
        INVOICE_VOIDED,
        on=pgtrigger.Update,
        condition=pgtrigger.Q(old__voided_at__isnull=True, new__voided_at__isnull=False),
        snapshot=["number", "customer"],
        audience=Field("customer"),
        policy=Policy.MANDATORY,
    )
    class Invoice(BaseModel): ...

Each name is a `KindName` - a string, so anything storing it stores the name - and a check holds the two
steps to each other: a declared kind nothing writes, or `notifies()` writing variants its declaration
doesn't name, is refused. A one-file project may hand `notifies()` a bare name instead, declaring the kind
there and then.

A kind's notice carries only the columns `snapshot` names, so a secret column never reaches the ledger
unless a declaration names it - and one that looks secret is named only with a reason:
`sensitive={"api_key_last4": SensitiveSnapshotField(reason="...")}`.
"""

import json
from dataclasses import dataclass, field, replace
from datetime import timedelta

import pgtrigger
from django.core.exceptions import ImproperlyConfigured
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, models, transaction
from django.db.models import Q
from django.db.models.expressions import RawSQL

from isik._internal.reasons import require_exemptions
from isik._internal.translation import gettext as _
from isik.common.utils.exemptions import Exemption
from isik.django.apps.notifications import conf, swapping
from isik.django.apps.notifications.choices import Policy, Scope, Source


class SensitiveSnapshotField(
    Exemption,
    rule="isik.notifications.sensitive-snapshot",
    why="A notice's snapshot is copied into the ledger, read by every channel, and kept after the row is gone.",
):
    """Why a kind's snapshot carries a field that looks secret - one per field, in `notifies(sensitive=...)`."""


class PushDataField(
    Exemption,
    rule="isik.notifications.push-data",
    why="A push's data passes through Apple's or Google's servers and sits on the device, outside the project.",
):
    """Why a kind's push carries a snapshot field - one per field, in `PushContent(data=...)`."""


# What a push may collapse by: one kind's pushes into one, or each subject's.
COLLAPSE = ("kind", "subject")


@dataclass(frozen=True)
class PushContent:
    """
    What a kind's push carries, on the push channel:

    - `private` - True sends its label alone, as the title: nothing of the notice reaches a lock screen,
      or Apple's or Google's servers, beyond what kind of thing happened.
    - `data` - `{snapshot field: PushDataField(reason=...)}`, the fields the app gets beside the notice's
      kind, variant and subject - each with why it may leave the project.
    - `collapse` - `"kind"` or `"subject"`: a later push of the kind (about the same subject) replaces an
      earlier one not yet seen, rather than stacking. None stacks them.
    - `ttl` - how long the service keeps trying a phone that's off, or None for its own default.
    - `badge` - True sets the app's badge to the user's unread inbox items.
    """

    private: bool = False
    data: dict = field(default_factory=dict)
    collapse: str = None
    ttl: timedelta = None
    badge: bool = False


def _push(name, push):
    """`push` (a `PushContent`, or None for the default) once its data fields each say why they're carried."""
    push = push or PushContent()
    if push.collapse not in (None, *COLLAPSE):
        raise ImproperlyConfigured(
            _("%(kind)s's push collapses by %(collapse)r - it takes kind, subject or None.")
            % {"kind": name, "collapse": push.collapse}
        )
    return replace(push, data=require_exemptions(name, "push.data", push.data, PushDataField))


@dataclass(frozen=True)
class KindText:
    """
    What a kind says to people, each lazy-translated text resolved in the request's language:

    - `label` - its name, as a settings page lists it ("Parcel reminders").
    - `description` - a sentence on what it's about.
    - `message` / `title` - an inbox item's sentence and heading: text formatted with the item's data
      (`"A token named %(name)s was created."`), or a callable taking the data and returning the text.
    """

    label: object = None
    description: object = None
    message: object = None
    title: object = None

    def merged_over(self, other):
        """This text, any part it leaves out taken from `other`."""
        return KindText(**{name: getattr(self, name) or getattr(other, name) for name in TEXT_PARTS})


TEXT_PARTS = ("label", "description", "message", "title")


def rendered(text, data, variant):
    """
    `text` (a `message` or `title`) for an item of `variant` carrying `data` - a callable is given both, a `%`-format
    the data. None when there's none, or it can't be filled.
    """
    if text is None:
        return None
    if callable(text):
        return str(text(data, variant))
    try:
        return str(text) % data
    except (KeyError, TypeError, ValueError):
        # An item older than the text - its data lacks what the text names - reads as the text unfilled.
        return None


class KindName(str):
    """A declared kind's name - what `declare_kind()` returns - carrying the variants and text it declares."""

    def __new__(cls, name, variants=(), text=None):
        declared_name = super().__new__(cls, name)
        declared_name.variants = tuple(variants)
        declared_name.text = text or KindText()
        return declared_name


@dataclass(frozen=True)
class Kind:
    """One declared kind. Built by `notifies()` or `declare_kind()`, not by hand."""

    name: str
    audience: object
    policy: str
    scope: str = Scope.SINGLE_PARTITION
    source: str = Source.TRIGGER
    # {variant: the condition it's written on, or None for always} - a tuple for an application kind.
    variants: object = ()
    channels: tuple = None
    model: type = None
    on: object = None
    condition: object = None
    snapshot: tuple = ()
    previous: bool = False
    partition: object = None
    # {field: SensitiveSnapshotField} - the snapshot fields that look secret, each with why it's carried.
    sensitive: dict = field(default_factory=dict)
    text: KindText = field(default_factory=KindText)
    push: PushContent = field(default_factory=PushContent)
    declared_in: str = field(default="", compare=False)

    @property
    def trigger_name(self):
        return f"notify_{self.name}"

    @property
    def enabled_channels(self):
        """The channels it's sent on: its own, else every configured one."""
        return tuple(self.channels) if self.channels is not None else tuple(conf.setting("CHANNELS"))


# Every declaration, in the order made - a name declared twice is in here twice, for the check to report.
declared = []
# Every KindName declare_kind() gave, in the order given - the vocabulary.
vocabulary = []


def kinds():
    """{name: Kind} - the last declaration of each name."""
    return {kind.name: kind for kind in declared}


def get_kind(name):
    """The declared kind called `name`, or None."""
    return kinds().get(name)


def text_of(name):
    """What the kind called `name` says to people: its declaration's text, over its vocabulary entry's."""
    in_vocabulary = next((entry.text for entry in vocabulary if entry == name), KindText())
    kind = get_kind(name)
    return kind.text.merged_over(in_vocabulary) if kind is not None else in_vocabulary


def _declare(kind):
    declared.append(kind)
    return kind


def _audience(audience):
    # An audience taking no arguments may be given as its class: audience=Owners.
    return audience() if isinstance(audience, type) else audience


def notifies(
    name,
    *,
    audience,
    policy,
    on,
    condition=None,
    variants=None,
    snapshot=(),
    previous=False,
    scope=Scope.SINGLE_PARTITION,
    channels=None,
    partition=None,
    sensitive=None,
    label=None,
    description=None,
    message=None,
    title=None,
    push=None,
):
    """
    Declares the trigger-sourced kind `name` on the decorated model: a trigger writing a notice whenever
    an `on` write (`pgtrigger.Insert`, `Update`, `Delete`, or several or'd) meets `condition` - one per
    variant (`{variant: condition}`) whose own condition holds, when the kind has variants. `on` has no
    default: a kind writing on every save of its row is one to choose, not to fall into.

    - `snapshot`: the model's fields the notice carries, as they were after the write (`previous=True`
      keeps them as they were before an update too). Nothing else of the row reaches the notice.
    - `name`: the kind's `KindName`, from `declare_kind()` - or a bare name, declaring it here.
    - `audience`: who it's for - anything with `recipients(notice)` (see `audiences.py`), or a class of
      one taking no arguments.
    - `policy`: a `Policy` - whether a subscription can turn it off, or has to turn it on.
    - `scope`: `Scope.CROSS_PARTITION` when its audience spans partitions.
    - `channels`: the channel names it's sent on, else every one `CHANNELS` turns on.
    - `partition`: where the row's partition is read from - a resolver from `partitions.py`, else
      `DEFAULT_PARTITION`. A project with no partitions leaves it out.
    - `sensitive`: `{field: SensitiveSnapshotField(reason=...)}`, for each snapshot field that looks secret.
    - `label`, `description`, `message`, `title`: what it says to people (see `KindText`) - over what its
      `declare_kind()` said.
    - `push`: what its push carries - a `PushContent`.
    """
    sensitive = require_exemptions(name, "sensitive", sensitive or {}, SensitiveSnapshotField)
    text = KindText(label=label, description=description, message=message, title=title)
    push = _push(name, push)

    def declare(model):
        kind = Kind(
            name=str(name),
            audience=_audience(audience),
            policy=policy,
            scope=scope,
            variants=dict(variants or {}),
            channels=tuple(channels) if channels is not None else None,
            model=model,
            on=on,
            condition=condition,
            snapshot=tuple(snapshot),
            previous=previous,
            partition=partition,
            sensitive=sensitive,
            text=text,
            push=push,
            declared_in=model._meta.label,
        )
        from isik.django.apps.notifications.triggers import NoticeTrigger

        pgtrigger.register(NoticeTrigger(kind=_declare(kind)))(model)
        return model

    return declare


def declare_kind(
    name,
    *,
    variants=(),
    audience=None,
    policy=None,
    scope=Scope.SINGLE_PARTITION,
    channels=None,
    source=Source.APPLICATION,
    label=None,
    description=None,
    message=None,
    title=None,
    push=None,
):
    """
    Declares the kind `name`, and the `variants` it may have - its place in the vocabulary - and returns its
    `KindName`. A kind written by a trigger takes its behavior from `notifies()`, given this name. One the
    application raises with `raise_notice()` takes it here: its `audience` and `policy`, with `scope` and
    `channels` as `notifies()` takes them.

    `source=Source.RECORDED` declares a kind of email its caller sends itself and `record_email()` records -
    mandatory, on email alone, with no audience: it takes none of those.

    `label`, `description`, `message` and `title` are what it says to people - see `KindText`; `push`, what
    its push carries - a `PushContent`.
    """
    text = KindText(label=label, description=description, message=message, title=title)
    declared_name = KindName(name, variants, text)
    # Built before anything is registered, so a declaration that fails leaves the registry as it was.
    if source == Source.RECORDED:
        if audience is not None or policy is not None or channels is not None or push is not None:
            raise ImproperlyConfigured(
                _("%(kind)s is recorded - mandatory, on email alone, and for whoever its caller sent it to.")
                % {"kind": name}
            )
        behavior = Kind(
            name=name,
            audience=None,
            policy=Policy.MANDATORY,
            source=Source.RECORDED,
            variants=tuple(variants),
            channels=("email",),
        )
    elif audience is not None or policy is not None:
        behavior = Kind(
            name=name,
            audience=_audience(audience),
            policy=policy,
            scope=scope,
            source=Source.APPLICATION,
            variants=tuple(variants),
            channels=tuple(channels) if channels is not None else None,
            push=_push(name, push),
        )
    elif push is not None:
        raise ImproperlyConfigured(
            _("%(kind)s's push= goes on the notifies() writing it, beside its other behavior.") % {"kind": name}
        )
    else:
        behavior = None
    vocabulary.append(declared_name)
    if behavior is not None:
        _declare(behavior)
    return declared_name


def raise_notice(kind, *, variant=None, subject=None, snapshot=None, partition=None):
    """
    Writes a notice of the application-sourced `kind` (a name or a `Kind`) in the current transaction,
    so it's written only if the transaction commits. `subject` is the row it's about, if any; `partition`
    the partition that row belongs to. Returns the notice.
    """
    name = checked_kind(kind, Source.APPLICATION, variant)
    with transaction.atomic():
        if _retired(name):
            raise ImproperlyConfigured(_("%(kind)s is retired.") % {"kind": name})
        return write_notice(name, variant=variant, subject=subject, snapshot=snapshot, partition=partition)


def checked_kind(kind, source, variant):
    """`kind`'s name, once it's a declared kind of `source` declaring `variant` - refused otherwise."""
    name = str(kind)
    declaration = get_kind(name)
    if declaration is None or declaration.source != source:
        raise ImproperlyConfigured(
            _("%(kind)s is not a declared kind whose source is %(source)s.") % {"kind": name, "source": source}
        )
    if variant is not None and variant not in declaration.variants:
        raise ValueError(_("%(kind)s declares no variant %(variant)r.") % {"kind": name, "variant": variant})
    return name


def write_notice(name, *, variant, subject, snapshot, partition, **fields):
    """Writes a notice of the kind `name`, about `subject` if any, and returns it as written."""
    from isik.django.apps.notifications.triggers import actor, context

    policy = swapping.get_kind_policy_model()._default_manager.filter(name=name)
    notice = swapping.get_notice_model()._default_manager.create(
        # The kind's key, read in the INSERT itself - no query of its own.
        kind_id=models.Subquery(policy.values("pk")),
        variant=variant,
        subject_label=subject._meta.label if subject is not None else None,
        subject_id=str(subject.pk) if subject is not None else None,
        subject_partition=partition,
        # As JSON carries it - a key or a time as text - which is how a trigger's snapshot holds them too.
        snapshot=json.loads(json.dumps(snapshot or {}, cls=DjangoJSONEncoder)),
        actor_id=RawSQL(actor(), []),
        context=RawSQL(context(), []),
        # The context a trigger-written notice would carry - the one the change's event rows carry.
        pgh_context_id=models.Func(function="""_pgh_attach_context"""),
        **fields,
    )
    notice.refresh_from_db()
    return notice


def _retired(name):
    """
    Whether the kind is retired, read under FOR SHARE: a retire waits for the transaction raising the notice,
    while every other raiser of the kind reads alongside it rather than queueing - FOR UPDATE would make
    each request raising it wait for the one before to commit.
    """
    policy = swapping.get_kind_policy_model()
    table, kind, retired_at = (
        connection.ops.quote_name(identifier)
        for identifier in (
            policy._meta.db_table,
            policy._meta.get_field("name").column,
            policy._meta.get_field("retired_at").column,
        )
    )
    with connection.cursor() as cursor:
        cursor.execute(f"""SELECT {retired_at} FROM {table} WHERE {kind} = %s FOR SHARE""", [name])
        row = cursor.fetchone()
    return row is not None and row[0] is not None


def matching_variant(queryset, variant):
    """`queryset`'s rows covering `variant`: those naming no variant, which cover every one, and those naming it."""
    return queryset.filter(Q(variant__isnull=True) | Q(variant=variant))


__all__ = [
    "Kind",
    "KindName",
    "KindText",
    "Policy",
    "PushContent",
    "PushDataField",
    "SensitiveSnapshotField",
    "Scope",
    "Source",
    "declare_kind",
    "get_kind",
    "kinds",
    "matching_variant",
    "notifies",
    "raise_notice",
    "rendered",
    "text_of",
]
