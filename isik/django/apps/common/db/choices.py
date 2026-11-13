"""
A field's choices held to how they change: `retired` values a row already holding keeps and nothing newly takes,
and `transitions`, the moves an update may make from one value to another. `with_choice_rules(field, ...)` gives
any field with choices its rules; `EnumField(Choices, ...)` is a `TextChoices` or `IntegerChoices` enum's field,
its column worked out from the enum, with the rules given beside it.

The rules hold at the column: the field's `IN (...)` CHECK admits its retired values beside its choices, and a
trigger refuses a retired value newly written and a move the transitions don't list - on every write,
`QuerySet.update()`, `bulk_create` and raw SQL included. `FullCleanOnSaveModel.full_clean()` refuses the same in
Python, each with its own code, so a save turns into a 400 rather than an `IntegrityError`.
"""

from dataclasses import dataclass

import pgtrigger
from django.core.exceptions import ValidationError
from django.db import connection, models

from isik._internal.translation import gettext as _
from isik._internal.translation import lazy_format


@dataclass(frozen=True)
class ChoiceRules:
    """A field's `retired` values and its `transitions` - `{value: values it moves to}`, or None for any move."""

    retired: tuple
    transitions: dict | None

    def clean(self, field, raw, instance):
        """`raw` cleaned by `field`, held to these rules against what `instance`'s row holds."""
        # A blank field takes an empty value uncleaned, as Model.clean_fields() has it - and null still moves.
        value = raw if field.blank and raw in field.empty_values else self._cleaned(field, raw, instance)
        stored = stored_choice(instance, field, value)
        if self.transitions is not None and not instance._state.adding and stored != value:
            moves = self.transitions.get(stored, frozenset())
            if value not in moves:
                raise ValidationError(
                    _("%(field)s moves from %(from)s to %(allowed)s, never to %(to)s.")
                    % {
                        "field": field.name,
                        "from": stored,
                        "allowed": ", ".join(map(str, _ordered(moves))) or _("nothing"),
                        "to": value,
                    },
                    code="invalid_transition",
                )
        return value

    def _cleaned(self, field, raw, instance):
        try:
            return field.clean(raw, instance)
        except ValidationError as error:
            value = field.to_python(raw)
            if error.code != "invalid_choice" or value not in self.retired:
                raise
        field.run_validators(value)
        if instance._state.adding or stored_choice(instance, field, value) != value:
            raise ValidationError(
                _("%(value)s is a retired %(field)s - only a row already holding it keeps it.")
                % {"value": value, "field": field.name},
                code="retired_choice",
            )
        return value


def stored_choice(instance, field, otherwise):
    """What `instance`'s row holds for `field`, as last loaded or saved - `otherwise` when it was never read."""
    return instance.__dict__.get("_stored_choices", {}).get(field.name, otherwise)


def _ordered(values):
    return sorted(values, key=lambda value: (value is None, str(value)))


def with_choice_rules(field, *, retired=(), transitions=None):
    """
    `field`, holding its choices to `retired` and `transitions`; returns the field.

        status = with_choice_rules(
            models.CharField(max_length=20, choices=Status.choices),
            retired=("legacy",),
            transitions={"draft": {"review"}, "review": {"published", "draft"}, "legacy": {"published"}},
        )

    `retired` are values out of the field's choices that a row holding one keeps; nothing writes one anew.
    `transitions` maps each value to the values an update moves it to - a value it leaves out moves nowhere,
    and with none given, any value moves to any. A nullable field names null as `None` in either side of it.
    An insert takes any of the field's choices. Held on a `FullCleanOnSaveModel`, in Python and at the column.
    """
    if field.choices is None:
        raise TypeError(_("with_choice_rules() takes a field with choices."))
    if not retired and transitions is None:
        raise TypeError(_("with_choice_rules() takes retired=, transitions= or both."))
    if isinstance(retired, str):
        raise TypeError(_("retired= is a tuple of values, not one value."))
    choices = {field.to_python(value) for value, _label in field.flatchoices if value is not None}
    retired = tuple(field.to_python(value) for value in retired)
    if None in retired:
        raise TypeError(_("retired= holds values, and null is the column's own - never a retired one."))
    if kept := [value for value in retired if value in choices]:
        raise TypeError(
            _("%(values)s are choices of the field still, and a retired value is out of them.")
            % {"values": ", ".join(map(str, kept))}
        )
    if transitions is not None:
        known = choices | set(retired) | ({None} if field.null else set())
        transitions = {
            field.to_python(source): frozenset(field.to_python(target) for target in targets)
            for source, targets in transitions.items()
        }
        named = set(transitions).union(*transitions.values())
        if unknown := named - known:
            raise TypeError(
                _("transitions= names %(values)s, which the field neither offers nor retires.")
                % {"values": ", ".join(map(str, _ordered(unknown)))}
            )
        if into := set().union(*transitions.values()) & set(retired):
            raise TypeError(
                _("transitions= moves rows to %(values)s, which are retired.")
                % {"values": ", ".join(map(str, _ordered(into)))}
            )
    field.choice_rules = ChoiceRules(retired=retired, transitions=transitions)
    return field


def EnumField(enum, *, retired=(), transitions=None, **kwargs):  # noqa: N802 - it's declared as a field is
    """
    A `TextChoices` or `IntegerChoices` enum's field: a `CharField` as long as its longest value, unless given
    `max_length`, or an `IntegerField`, with the enum's choices and `with_choice_rules()`'s `retired` and
    `transitions` when given. Its migration is the plain field's.

        status = EnumField(Status, default=Status.DRAFT, transitions={Status.DRAFT: {Status.REVIEW}})
    """
    if isinstance(enum, type) and issubclass(enum, models.TextChoices):
        longest = max(len(value) for value in enum.values if value is not None)
        field = models.CharField(choices=enum.choices, max_length=kwargs.pop("max_length", longest), **kwargs)
    elif isinstance(enum, type) and issubclass(enum, models.IntegerChoices):
        field = models.IntegerField(choices=enum.choices, **kwargs)
    else:
        raise TypeError(_("EnumField() takes a TextChoices or IntegerChoices enum."))
    if retired or transitions is not None:
        with_choice_rules(field, retired=retired, transitions=transitions)
    return field


def _literal(value):
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


class ChoiceRulesTrigger(pgtrigger.Trigger):
    """
    The column's half of a field's `ChoiceRules`: before an insert or an update changing the field, it refuses
    a retired value, and on an update, a move `transitions` doesn't list. Holds its values itself: a migration
    renders it against a model of its own state, whose fields carry no rules.
    """

    when = pgtrigger.Before
    operation = pgtrigger.Insert | pgtrigger.Update

    def __init__(self, *, name, field, rules):
        self.field, self.rules = field, rules
        super().__init__(name=name)

    def get_func(self, model):
        quoted = connection.ops.quote_name(model._meta.get_field(self.field).column)
        column, old = f"NEW.{quoted}", f"OLD.{quoted}"
        refusals = []
        if self.rules.retired:
            values = ", ".join(_literal(value) for value in self.rules.retired)
            message = _literal(f"%L is a retired {self.field} - only a row already holding it keeps it.")
            refusals.append(
                f"IF {column} IN ({values}) THEN "
                f"RAISE EXCEPTION USING ERRCODE = 'check_violation', MESSAGE = format({message}, {column}); END IF;"
            )
        if self.rules.transitions is not None:
            moves = " OR ".join(
                f"({old} IS NOT DISTINCT FROM {_literal(source)} AND {column} IS NOT DISTINCT FROM {_literal(target)})"
                for source in _ordered(self.rules.transitions)
                for target in _ordered(self.rules.transitions[source])
            )
            message = _literal(f"{self.field} never moves from %L to %L.")
            refusals.append(
                f"IF TG_OP = 'UPDATE' AND NOT ({moves or 'FALSE'}) THEN RAISE EXCEPTION USING "
                f"ERRCODE = 'check_violation', MESSAGE = format({message}, {old}, {column}); END IF;"
            )
        return f"IF TG_OP = 'INSERT' OR {column} IS DISTINCT FROM {old} THEN {' '.join(refusals)} END IF; RETURN NEW;"


def choice_rules_trigger(field):
    """The trigger holding `field`'s rules at its column."""
    return ChoiceRulesTrigger(name=f"{field.name}_choice_rules", field=field.name, rules=field.choice_rules)


def choice_rule_problems(app_configs):
    """A field with choice rules on a model that can't hold them: a managed, concrete `FullCleanOnSaveModel`."""
    from isik.django.apps.common.db.models import FullCleanOnSaveModel

    return [
        lazy_format(
            "%(model)s.%(field)s has choice rules, which only a managed FullCleanOnSaveModel holds.",
            model=model._meta.label,
            field=field.name,
        )
        for config in app_configs
        for model in config.get_models()
        if not (issubclass(model, FullCleanOnSaveModel) and model._meta.managed)
        for field in model._meta.local_concrete_fields
        if getattr(field, "choice_rules", None) is not None
    ]
