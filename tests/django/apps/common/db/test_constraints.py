"""That a rule a validator holds also holds at the column, or says why it does not."""

import functools
import inspect
import uuid

import pytest
from django.apps import apps as django_apps
from django.core import validators as django_validators
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models.signals import class_prepared
from django.test.utils import isolate_apps

from isik.django.apps.common.db import BaseModel
from isik.django.apps.common.db.constraints import (
    DJANGO_VALIDATORS,
    DJANGO_VALIDATORS_WITHOUT_ONE,
    IDENTIFIER_LIMIT,
    _choices_condition,
    _from_validators,
    _name,
    as_condition,
    classified,
    condition_for,
    constrain,
    install,
    no_database_form,
    python_only_validator,
    teach_django_its_validators,
    unclassified_validators,
)
from isik.django.apps.common.db.models import FullCleanOnSaveModel
from tests.testapp.models import CleanedNote, Widget


pytestmark = pytest.mark.django_db

REASON = "A sentence long enough to be one, which is the whole point of the floor underneath it."


def _fresh_model(prefix, **fields):
    """Uniquely named, because pgtrigger keeps a process-wide registry keyed by table and a second
    run of one test in one process - which mutation testing does - collides with the first."""
    meta = type("Meta", (), {"app_label": "testapp"})
    # In a registry of its own, or a model built here to be reported as unanswered would be a
    # project-wide one and the check's own test would fail.
    with isolate_apps():
        return type(f"{prefix}{uuid.uuid4().hex[:8]}", (BaseModel,), {"__module__": __name__, "Meta": meta, **fields})


def test_the_project_leaves_no_validator_unanswered():
    """The check in one assertion. A validator reaching a column without saying whether it can hold
    there is the thing this whole module exists to refuse."""
    assert unclassified_validators() == []


def test_a_rule_a_validator_holds_is_refused_by_the_column_too():
    """The claim, end to end: `positive_only` runs in full_clean(), and an update that never calls
    it is refused anyway."""
    note = CleanedNote.objects.create(count=1)

    with pytest.raises(IntegrityError), transaction.atomic():
        CleanedNote.objects.filter(pk=note.pk).update(count=-1)


def test_a_rule_declined_at_the_column_can_still_be_skipped():
    """The other half, and the reason the per-instance form exists. `Widget.count` carries the same
    validator wrapped, so `skip_full_clean()` still means something - a CHECK cannot be lifted, and
    enforcing one here would have taken that away without saying so."""
    widget = Widget.objects.create(name="bolt", count=1)

    with widget.skip_full_clean():
        widget.update(count=-9)

    assert Widget.objects.get(pk=widget.pk).count == -9


def test_what_the_column_carries_already_is_not_repeated():
    """A `varchar(n)` refuses a longer value by itself, so the validator Django appends from
    `max_length` asks for nothing - otherwise every text column would grow a second rule saying what
    its type says."""
    field = Widget._meta.get_field("name")
    validator = next(
        v for v in map(inspect.unwrap, field.validators) if isinstance(v, django_validators.MaxLengthValidator)
    )

    assert condition_for(validator, field) is None


def test_a_length_floor_has_no_type_to_lean_on_so_it_becomes_a_constraint():
    """The mirror of the test above: nothing about `varchar(n)` enforces a minimum."""

    floored = _fresh_model("Floored", note=models.TextField(validators=[django_validators.MinLengthValidator(5)]))
    field = floored._meta.get_field("note")

    assert condition_for(inspect.unwrap(field.validators[0]), field) is not None


@pytest.mark.parametrize(
    "internal,limit,end",
    [("PositiveIntegerField", 0, "min"), ("PositiveIntegerField", 2147483647, "max")],
    ids=["its own floor", "its own ceiling"],
)
def test_a_numbers_own_range_is_not_repeated(internal, limit, end):
    """Django adds both from the field type, and both are held already - the top by `integer`, the
    bottom by a CHECK Django writes itself."""
    field = models.PositiveIntegerField()
    field.set_attributes_from_name("quantity")
    validator = (
        django_validators.MinValueValidator(limit) if end == "min" else django_validators.MaxValueValidator(limit)
    )

    assert condition_for(validator, field) is None


def test_a_bound_the_column_does_not_carry_becomes_a_constraint():
    field = models.PositiveIntegerField()
    field.set_attributes_from_name("quantity")

    assert condition_for(django_validators.MaxValueValidator(10), field) is not None


def test_a_validator_of_your_own_can_say_it_has_no_database_form():
    @no_database_form(REASON)
    def asks_the_network(value): ...

    assert classified(asks_the_network)
    assert condition_for(asks_the_network, None) is None
    assert asks_the_network.no_database_form == REASON


def test_a_reason_too_short_to_be_one_is_refused():
    """The floor every exemption in this library is held to - "it is awkward" and "it cannot be
    done" read alike once the reason is left out."""
    with pytest.raises(ValueError):
        no_database_form("because")(lambda value: None)


def test_one_instance_can_decline_what_its_class_could_express():
    """The per-instance half. The same validator is worth holding at one column and not at another,
    and a `CheckConstraint` cannot be lifted the way `SkipFieldValidators` lifts a validator."""
    declined = python_only_validator(django_validators.MinLengthValidator(5), reason=REASON)

    assert classified(declined)
    assert condition_for(declined, None) is None


def test_the_wrapper_still_validates():
    declined = python_only_validator(django_validators.MinLengthValidator(5), reason=REASON)

    with pytest.raises(ValidationError):
        declined("abc")


def test_the_wrapper_survives_a_round_trip_through_a_migration():
    """`deconstructible` with `__eq__`, or `makemigrations` sees a new object every run and writes a
    migration for it forever."""
    made = python_only_validator(django_validators.MinLengthValidator(5), reason=REASON)
    path, args, kwargs = made.deconstruct()

    import importlib

    module, _, name = path.rpartition(".")
    rebuilt = getattr(importlib.import_module(module), name)(*args, **kwargs)

    assert rebuilt == made
    assert hash(rebuilt) == hash(made)


def test_a_choice_a_column_does_not_list_is_refused():
    """Django checks `choices` in `full_clean()` and nowhere else, so without this the column takes
    whatever an update hands it."""
    note = CleanedNote.objects.create(count=1)
    column = CleanedNote._meta.get_field("kind").column

    with pytest.raises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(f"UPDATE {CleanedNote._meta.db_table} SET {column} = %s WHERE id = %s", ["nonsense", note.pk])


def test_constraining_a_model_twice_adds_nothing_the_second_time():
    """It is reached once per model at `ready()` and again from `class_prepared` for anything built
    afterwards, so it has to be safe to repeat."""
    before = list(Widget._meta.constraints)

    constrain(Widget)

    assert Widget._meta.constraints == before


def test_a_name_too_long_for_postgres_is_hashed_rather_than_cut():
    """Postgres truncates an identifier rather than refusing it, so two constraints whose names
    differ past the limit would silently become one."""

    long_named = _fresh_model(
        "LongNamesIndeed",
        a_column_with_a_genuinely_excessive_name_for_the_limit=models.TextField(
            validators=[django_validators.MinLengthValidator(5)]
        ),
    )

    constrain(long_named)
    names = [c.name for c in long_named._meta.constraints]

    assert names
    assert all(len(name) <= 63 for name in names)


def _field(kind, name="quantity", **kwargs):
    field = kind(**kwargs)
    field.set_attributes_from_name(name)
    return field


@pytest.mark.parametrize(
    "validator,expected",
    [
        (django_validators.MinValueValidator(3), models.Q(quantity__gte=3)),
        (django_validators.MaxValueValidator(10), models.Q(quantity__lte=10)),
    ],
    ids=["a floor is gte", "a ceiling is lte"],
)
def test_a_bound_becomes_the_comparison_it_names(validator, expected):
    """Which way round matters: a floor written as a ceiling refuses everything it was meant to
    allow, and a constraint that disagrees with its validator is worse than none."""
    assert condition_for(validator, _field(models.PositiveIntegerField)) == expected


@pytest.mark.parametrize(
    "kind,validator,expected",
    [
        (models.TextField, django_validators.MinLengthValidator(5), models.Q(note__length__gte=5)),
        (models.TextField, django_validators.MaxLengthValidator(5), models.Q(note__length__lte=5)),
    ],
    ids=["a floor on length", "a ceiling on a column that carries none"],
)
def test_a_length_becomes_the_comparison_it_names(kind, validator, expected):
    assert condition_for(validator, _field(kind, "note")) == expected


def test_two_bounds_on_one_column_become_one_constraint():
    """Combined rather than one apiece, and the loop that combines them has to reach past the
    second - two validators look the same as three until something has three."""
    bounded = _fresh_model(
        "Bounded",
        quantity=models.IntegerField(
            validators=[
                django_validators.MinValueValidator(1),
                django_validators.MaxValueValidator(9),
                django_validators.MinValueValidator(2),
            ]
        ),
    )

    constraint = _from_validators(bounded._meta.get_field("quantity"))

    assert constraint.condition == (models.Q(quantity__gte=1) & models.Q(quantity__lte=9) & models.Q(quantity__gte=2))


def test_a_column_asking_nothing_of_itself_grows_no_constraint():
    plain = _fresh_model("Plain", quantity=models.IntegerField())

    assert _from_validators(plain._meta.get_field("quantity")) is None


def test_a_name_exactly_at_the_limit_is_left_alone():
    """The boundary, because `<` and `<=` differ by one character and one of them hashes a name that
    did not need it."""
    field = _field(models.TextField, "n" * (63 - len("testapp_x") - len("choices") - 2))
    field.model = type("X", (), {"_meta": type("M", (), {"db_table": "testapp_x"})})

    assert len(_name(field, "choices")) == 63
    assert "choices" in _name(field, "choices")


def test_a_name_past_the_limit_keeps_its_front_and_hashes_its_tail():
    field = _field(models.TextField, "n" * 80)
    field.model = type("X", (), {"_meta": type("M", (), {"db_table": "testapp_x"})})

    name = _name(field, "choices")

    assert len(name) == 63
    assert name.startswith("testapp_x_nnn")
    assert name == _name(field, "choices")


def test_two_names_differing_only_past_the_limit_do_not_collide():
    """What the hash is for. Postgres truncates rather than refusing, so without it these would be
    one constraint wearing two meanings."""
    long_names = []
    for suffix in ("a", "b"):
        field = _field(models.TextField, "n" * 80 + suffix)
        field.model = type("X", (), {"_meta": type("M", (), {"db_table": "testapp_x"})})
        long_names.append(_name(field, "choices"))

    assert long_names[0] != long_names[1]


class TestWhatConstrainAddsAndWhatItLeavesAlone:
    def test_it_adds_both_kinds_at_once(self):
        both = _fresh_model(
            "Both",
            kind=models.CharField(max_length=8, choices=[("a", "A"), ("b", "B")]),
            quantity=models.IntegerField(validators=[django_validators.MinValueValidator(1)]),
        )

        constrain(both)
        names = [c.name for c in both._meta.constraints]

        assert any(name.endswith("_kind_choices") for name in names)
        assert any(name.endswith("_quantity_validators") for name in names)

    def test_it_tells_makemigrations_where_to_look(self):
        """Only a literal `class Meta` fills `original_attrs`, so a runtime append is invisible to
        `makemigrations` without this - the constraints exist and no migration ever mentions them."""
        model = _fresh_model("Seen", kind=models.CharField(max_length=8, choices=[("a", "A")]))

        constrain(model)

        assert model._meta.original_attrs["constraints"] == model._meta.constraints

    def test_a_model_with_nothing_to_say_is_left_untouched(self):
        model = _fresh_model("Quiet", note=models.TextField())
        before = list(model._meta.constraints)

        constrain(model)

        assert model._meta.constraints == before
        assert "constraints" not in model._meta.original_attrs

    def test_an_abstract_model_is_not_constrained(self):
        """Its table is its children's, and each of them is reached on its own."""
        meta = type("Meta", (), {"app_label": "testapp", "abstract": True})
        abstract = type(
            f"Abstract{uuid.uuid4().hex[:8]}",
            (BaseModel,),
            {"__module__": __name__, "Meta": meta, "kind": models.CharField(max_length=8, choices=[("a", "A")])},
        )

        constrain(abstract)

        assert abstract._meta.constraints == []

    def test_a_model_this_library_does_not_own_is_not_constrained(self):
        """A constraint on a table whose migrations are somebody else's is a migration they will
        never write."""
        theirs = type(
            f"Theirs{uuid.uuid4().hex[:8]}",
            (models.Model,),
            {
                "__module__": __name__,
                "Meta": type("Meta", (), {"app_label": "testapp"}),
                "kind": models.CharField(max_length=8, choices=[("a", "A")]),
            },
        )

        constrain(theirs)

        assert theirs._meta.constraints == []


class TestWhatTheCheckReports:
    def test_it_names_the_model_the_field_and_the_validator(self):
        def unanswered(value): ...

        model = _fresh_model("Unanswered", note=models.TextField(validators=[unanswered]))

        assert unclassified_validators(over=[model]) == [f"{model._meta.label}.note (unanswered)"]

    def test_a_validator_django_added_itself_is_not_reported(self):
        """`_validators` is what a model passed; Django appends its own from `max_length`, and
        nobody can wrap what they did not write."""
        model = _fresh_model("Appended", note=models.CharField(max_length=10))

        assert unclassified_validators(over=[model]) == []

    def test_a_class_based_validator_is_reported_by_its_class(self):
        class AsksTheNetwork:
            def __call__(self, value): ...

        model = _fresh_model("Classy", note=models.TextField(validators=[AsksTheNetwork()]))

        assert unclassified_validators(over=[model]) == [f"{model._meta.label}.note (AsksTheNetwork)"]


class TestTheWrapperKeepsWhatItWraps:
    def test_it_answers_to_the_name_of_the_validator_inside_it(self):
        """`make_skippable` reads `__name__` to register a validator, so without this
        `SkipNamedValidators` silently stops finding anything wrapped."""

        def positive(value): ...

        assert python_only_validator(positive, reason=REASON).__name__ == "positive"

    def test_a_class_based_validator_lends_its_class_name(self):
        wrapped = python_only_validator(django_validators.MinLengthValidator(5), reason=REASON)

        assert wrapped.__name__ == "MinLengthValidator"

    def test_two_wrappers_around_different_validators_are_not_equal(self):
        first = python_only_validator(django_validators.MinLengthValidator(5), reason=REASON)
        second = python_only_validator(django_validators.MinLengthValidator(9), reason=REASON)

        assert first != second

    def test_two_wrappers_with_different_reasons_are_not_equal(self):
        first = python_only_validator(django_validators.MinLengthValidator(5), reason=REASON)
        second = python_only_validator(django_validators.MinLengthValidator(5), reason=REASON + " And more.")

        assert first != second

    def test_two_wrappers_declining_for_different_reasons_hash_apart(self):
        """Hashable at all because `deconstructible` needs it, and apart because a dict of them
        keyed by one reason would otherwise answer for another."""
        first = python_only_validator(django_validators.MinLengthValidator(5), reason=REASON)
        second = python_only_validator(django_validators.MinLengthValidator(5), reason=REASON + " And more.")

        assert hash(first) != hash(second)

    def test_it_is_not_equal_to_the_validator_it_wraps(self):
        inner = django_validators.MinLengthValidator(5)

        assert python_only_validator(inner, reason=REASON) != inner


def test_a_validator_that_declined_asks_nothing_of_the_column_even_with_a_condition():
    """The decline wins. A wrapper around something that knows its SQL still wants no constraint,
    which is the whole point of wrapping it."""
    declined = python_only_validator(django_validators.MinLengthValidator(5), reason=REASON)
    declined.as_condition = lambda field, validator: models.Q(note__length__gte=5)

    assert condition_for(declined, _field(models.TextField, "note")) is None


def test_a_validator_with_no_answer_at_all_asks_nothing():
    assert condition_for(lambda value: None, _field(models.TextField, "note")) is None


class TestWhichModelsAndValidatorsTheCheckReads:
    def test_left_to_itself_it_reads_the_whole_project(self):
        assert unclassified_validators() == unclassified_validators(over=django_apps.get_models())

    def test_it_reports_in_a_settled_order(self):
        """A check whose message reorders between runs is a diff in every review that touches it."""

        def unanswered(value): ...

        first = _fresh_model("Zeta", note=models.TextField(validators=[unanswered]))
        second = _fresh_model("Alpha", note=models.TextField(validators=[unanswered]))

        assert unclassified_validators(over=[first, second]) == sorted(unclassified_validators(over=[first, second]))
        assert unclassified_validators(over=[second, first]) == unclassified_validators(over=[first, second])

    def test_a_model_this_library_does_not_own_is_not_read(self):
        """It never calls `full_clean()` on save, so a validator on it is not a rule this is about."""

        def unanswered(value): ...

        with isolate_apps():
            theirs = type(
                f"Theirs{uuid.uuid4().hex[:8]}",
                (models.Model,),
                {
                    "__module__": __name__,
                    "Meta": type("Meta", (), {"app_label": "testapp"}),
                    "note": models.TextField(validators=[unanswered]),
                },
            )

        assert unclassified_validators(over=[theirs]) == []

    def test_an_abstract_model_is_not_read(self):
        """Its fields reach the check again on every child, under the name of a real table."""

        def unanswered(value): ...

        with isolate_apps():
            abstract = type(
                f"Abstract{uuid.uuid4().hex[:8]}",
                (BaseModel,),
                {
                    "__module__": __name__,
                    "Meta": type("Meta", (), {"app_label": "testapp", "abstract": True}),
                    "note": models.TextField(validators=[unanswered]),
                },
            )

        assert unclassified_validators(over=[abstract]) == []

    def test_it_looks_through_the_wrapper_that_makes_a_validator_skippable(self):
        """`make_skippable` wraps with `functools.wraps`, so the field holds the wrapper and the
        answer is on what is inside it."""

        @no_database_form(REASON)
        def answered(value): ...

        wrapped = functools.wraps(answered)(lambda value: answered(value))
        model = _fresh_model("Wrapped", note=models.TextField(validators=[wrapped]))

        assert inspect.unwrap(model._meta.get_field("note")._validators[0]) is answered
        assert unclassified_validators(over=[model]) == []

    def test_one_validator_on_two_fields_is_two_lines(self):
        def unanswered(value): ...

        model = _fresh_model(
            "Twice",
            note=models.TextField(validators=[unanswered]),
            other=models.TextField(validators=[unanswered]),
        )

        assert unclassified_validators(over=[model]) == [
            f"{model._meta.label}.note (unanswered)",
            f"{model._meta.label}.other (unanswered)",
        ]


class TestConstrainingTwiceAndWhatWasAlreadyThere:
    def test_a_second_pass_adds_nothing(self):
        """It is reached twice - once per model at `ready()`, again from `class_prepared` - and a
        duplicate name is an error `makemigrations` raises, not one this discovers."""
        model = _fresh_model("Again", kind=models.CharField(max_length=8, choices=[("a", "A")]))

        constrain(model)
        once = list(model._meta.constraints)
        constrain(model)

        assert model._meta.constraints == once

    def test_a_constraint_written_by_hand_is_kept_and_stays_first(self):
        model = _fresh_model("Handwritten", kind=models.CharField(max_length=8, choices=[("a", "A")]))
        mine = models.CheckConstraint(condition=models.Q(kind__isnull=False), name="mine_by_hand")
        model._meta.constraints = [mine]

        constrain(model)

        assert model._meta.constraints[0] is mine
        assert len(model._meta.constraints) == 2

    def test_a_name_already_taken_by_hand_is_left_to_its_owner(self):
        model = _fresh_model("Taken", kind=models.CharField(max_length=8, choices=[("a", "A")]))
        mine = models.CheckConstraint(
            condition=models.Q(kind="a"), name=_name(model._meta.get_field("kind"), "choices")
        )
        model._meta.constraints = [mine]

        constrain(model)

        assert model._meta.constraints == [mine]

    def test_a_proxy_is_not_constrained(self):
        """It is another name for a table its concrete model already answered for, so constraining
        it writes the same CHECK twice under two names."""
        # Off `FullCleanOnSaveModel` rather than `BaseModel`: a proxy inherits its parent's triggers
        # and pgtrigger keys its registry on the table the two of them share.
        with isolate_apps():
            concrete = type(
                f"Concrete{uuid.uuid4().hex[:8]}",
                (FullCleanOnSaveModel,),
                {
                    "__module__": __name__,
                    "Meta": type("Meta", (), {"app_label": "testapp"}),
                    "kind": models.CharField(max_length=8, choices=[("a", "A")]),
                },
            )
            proxy = type(
                f"Proxy{uuid.uuid4().hex[:8]}",
                (concrete,),
                {
                    "__module__": __name__,
                    "Meta": type("Meta", (), {"app_label": "testapp", "proxy": True}),
                },
            )

        constrain(concrete)
        assert [c.name for c in concrete._meta.constraints]

        constrain(proxy)

        assert proxy._meta.constraints == []

    def test_a_field_whose_validators_ask_for_nothing_adds_no_constraint(self):
        """`_from_validators` answers None there, and a None in the list is a constraint without a
        name that `makemigrations` would choke on."""
        model = _fresh_model("Nothing", note=models.CharField(max_length=10))

        constrain(model)

        assert model._meta.constraints == []


class TestInstalling:
    def test_it_answers_for_djangos_own_validators(self):
        """Attached in place rather than by subclass, so the instances Django has already built on
        fields declared before this ran are answered too."""
        teach_django_its_validators()

        for validator in (*DJANGO_VALIDATORS, *DJANGO_VALIDATORS_WITHOUT_ONE):
            assert classified(validator), validator

        assert all(v.as_condition is not None for v in DJANGO_VALIDATORS)
        assert all(v.no_database_form.reason for v in DJANGO_VALIDATORS_WITHOUT_ONE)

    def test_it_reaches_a_model_built_after_it_ran(self):
        """`ready()` is the first moment every model is final, and a test building one with
        `isolate_apps` prepares it long after."""
        model = _fresh_model("Later", kind=models.CharField(max_length=8, choices=[("a", "A")]))

        assert [c.name for c in model._meta.constraints] == [_name(model._meta.get_field("kind"), "choices")]

    def test_it_connects_once_however_often_it_runs(self):
        """Django keys a connection on the receiver's identity, so a second `ready()` finds the same
        function - and two receivers would mean the second finds every name taken."""
        before = len(class_prepared.receivers)

        install()
        install()

        assert len(class_prepared.receivers) == before


class TestNamingAConstraint:
    def test_it_is_the_table_the_column_and_the_kind(self):
        field = _fresh_model("Named", kind=models.CharField(max_length=8))._meta.get_field("kind")

        assert _name(field, "choices") == f"{field.model._meta.db_table}_kind_choices"

    def test_a_name_that_fits_is_left_alone(self):
        model = _fresh_model("Fits", kind=models.CharField(max_length=8))
        name = _name(model._meta.get_field("kind"), "choices")

        assert len(name) <= IDENTIFIER_LIMIT
        assert name == f"{model._meta.db_table}_kind_choices"

    def test_a_long_one_is_cut_to_the_limit_and_ends_in_its_hash(self):
        """Postgres truncates at 63 rather than refusing, and a truncated tail is where two names
        stop differing - so the hash goes where nothing can cut it off."""
        long = "x" * 60
        model = _fresh_model("Long", **{long: models.CharField(max_length=8)})
        field = model._meta.get_field(long)

        name = _name(field, "choices")

        assert len(name) == IDENTIFIER_LIMIT
        assert name.startswith(f"{model._meta.db_table}_")

    def test_two_names_differing_only_past_the_limit_are_different(self):
        first = _fresh_model("Pair", **{"a" * 60: models.CharField(max_length=8)})
        second = _fresh_model("Pair", **{"a" * 59 + "b": models.CharField(max_length=8)})

        assert _name(first._meta.get_field("a" * 60), "choices") != _name(
            second._meta.get_field("a" * 59 + "b"), "choices"
        )

    def test_the_same_field_names_the_same_constraint_every_run(self):
        """A name that moves between runs is a migration on every `makemigrations`."""
        field = _fresh_model("Stable", **{"y" * 60: models.CharField(max_length=8)})._meta.get_field("y" * 60)

        assert _name(field, "choices") == _name(field, "choices")


class TestTheConditionChoicesAskFor:
    def test_it_is_the_declared_values(self):
        field = _fresh_model(
            "Plain", kind=models.CharField(max_length=8, choices=[("a", "A"), ("b", "B")])
        )._meta.get_field("kind")

        assert _choices_condition(field) == models.Q(kind__in=["a", "b"])

    def test_a_nullable_column_holds_null_outside_the_list_by_definition(self):
        field = _fresh_model(
            "Nullable", kind=models.CharField(max_length=8, null=True, choices=[("a", "A")])
        )._meta.get_field("kind")

        assert _choices_condition(field) == models.Q(kind__in=["a"]) | models.Q(kind__isnull=True)

    def test_a_blank_text_column_may_hold_the_empty_string(self):
        """`blank=True` on a text field means storable and not a choice, and a constraint refusing it
        refuses what the field was declared to allow."""
        field = _fresh_model(
            "Blank", kind=models.CharField(max_length=8, blank=True, choices=[("a", "A")])
        )._meta.get_field("kind")

        assert _choices_condition(field) == models.Q(kind__in=["a"]) | models.Q(kind="")

    def test_a_blank_number_may_not(self):
        """`blank` on a number is a form-level claim, and the column stores no empty string."""
        field = _fresh_model("BlankNumber", kind=models.IntegerField(blank=True, choices=[(1, "One")]))._meta.get_field(
            "kind"
        )

        assert _choices_condition(field) == models.Q(kind__in=[1])

    def test_an_empty_string_that_is_already_a_choice_is_not_added_twice(self):
        field = _fresh_model(
            "EmptyChoice", kind=models.CharField(max_length=8, blank=True, choices=[("", "None"), ("a", "A")])
        )._meta.get_field("kind")

        assert _choices_condition(field) == models.Q(kind__in=["", "a"])

    def test_a_null_among_the_choices_is_left_to_the_null_branch(self):
        """A declared None is not a value `IN (...)` can hold - SQL's null compares to nothing - so
        whether null is allowed is the column's own question."""
        field = _fresh_model(
            "NullChoice", kind=models.CharField(max_length=8, null=True, choices=[(None, "Any"), ("a", "A")])
        )._meta.get_field("kind")

        assert _choices_condition(field) == models.Q(kind__in=["a"]) | models.Q(kind__isnull=True)

    def test_a_nullable_blank_column_allows_both(self):
        field = _fresh_model(
            "Both2", kind=models.CharField(max_length=8, null=True, blank=True, choices=[("a", "A")])
        )._meta.get_field("kind")

        assert _choices_condition(field) == (models.Q(kind__in=["a"]) | models.Q(kind__isnull=True) | models.Q(kind=""))


def test_a_validator_taught_its_sql_here_and_now_gets_a_constraint():
    """Declared in the body rather than at import, because a mutation run forks from a parent that
    already imported this module and never re-runs a decorator at module level."""

    @as_condition(lambda field, validator: models.Q(**{f"{field.name}__gte": 0}))
    def positive(value): ...

    model = _fresh_model("Taught", count=models.IntegerField(validators=[positive]))

    assert classified(positive)
    assert condition_for(positive, model._meta.get_field("count")) == models.Q(count__gte=0)
    assert [c.condition for c in model._meta.constraints] == [models.Q(count__gte=0)]


def test_a_long_name_ends_in_a_digest_wide_enough_to_separate_two():
    """Four bytes. Narrower and two names that already agree for 54 characters start colliding for
    real, which is the thing the hash is there to stop."""
    field = _fresh_model("Digest", **{"z" * 60: models.CharField(max_length=8)})._meta.get_field("z" * 60)

    assert len(_name(field, "choices").rsplit("_", 1)[1]) == 8


def test_a_class_marked_validator_leaves_its_column_unconstrained():
    """`CleanedNote.seen_at` carries `not_in_the_future`, which asks the clock - so the field is
    validated, the check is satisfied, and no constraint is written."""
    names = [constraint.name for constraint in CleanedNote._meta.constraints]

    assert not [name for name in names if "seen_at" in name]
    assert unclassified_validators(over=[CleanedNote]) == []
