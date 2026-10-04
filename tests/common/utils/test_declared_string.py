"""DeclaredString - a str whose value, validation and carried attributes are declared, not hand-written."""

import copy
import pickle
import re

import pytest

from isik.common.utils.declared_string import (
    MISSING,
    Declaration,
    DeclaredAttribute,
    DeclaredString,
    DeclaredText,
    attribute,
    text,
)


class Exemption(DeclaredString):
    reason = text(min_length=10)


class NoComment(DeclaredString, displays_as=""):
    reason = text(min_length=10, max_length=40)


class PermissionName(DeclaredString):
    name = text(pattern=r"[a-z_]+:[a-z_]+:[a-z_]+")
    label = attribute(str)
    delegatable = attribute(bool, default=False)


class ProjectReason(DeclaredString):
    """A project's own base - every sentinel built on it shares its rule."""

    reason = text(min_length=20)


class NoHelpText(ProjectReason, displays_as=""):
    pass


class Marker(DeclaredString, displays_as="marked"):
    note = attribute(default=None)


REASON = "the registry would reject it"


class TestWhatItIs:
    def test_a_sentinel_without_displays_as_is_its_text(self):
        exemption = Exemption(REASON)

        assert exemption == REASON
        assert type(exemption) is Exemption
        assert exemption.reason == REASON
        assert bool(exemption) is True

    def test_displays_as_decides_the_str_and_the_text_is_still_carried(self):
        unsaid = NoComment(REASON)

        assert unsaid == ""
        assert hash(unsaid) == hash("")
        assert unsaid.reason == REASON

    def test_whitespace_in_text_is_collapsed(self):
        assert Exemption("  the   registry\n would\treject it ").reason == REASON

    def test_whitespace_is_kept_when_asked(self):
        class Verbatim(DeclaredString):
            body = text(collapse_whitespace=False)

        assert Verbatim(" a  b ") == " a  b "

    def test_attributes_are_kept_and_defaults_filled_in(self):
        name = PermissionName("org:invite:member", label="Invite members")

        assert name == "org:invite:member"
        assert (name.name, name.label, name.delegatable) == ("org:invite:member", "Invite members", False)

    def test_a_class_with_no_text_is_its_displays_as(self):
        assert Marker() == "marked"
        assert Marker(note="why").note == "why"

    def test_a_projects_base_sets_the_rule_its_sentinels_share(self):
        with pytest.raises(ValueError):
            NoHelpText("too short")

        assert NoHelpText("a reason that is long enough").reason == "a reason that is long enough"

    def test_a_subclass_can_redeclare_a_rule_in_place(self):
        class Lenient(Exemption):
            reason = text()

        assert Lenient("ok").reason == "ok"
        assert Lenient.positional == "reason"


class TestValidation:
    def test_text_shorter_than_its_minimum_is_refused(self):
        with pytest.raises(ValueError) as raised:
            Exemption("n/a")

        assert str(raised.value) == "Exemption needs a reason of at least 10 characters, not 'n/a'."

    def test_text_at_its_minimum_is_enough(self):
        assert Exemption("x" * 10).reason == "x" * 10

    def test_text_longer_than_its_maximum_is_refused(self):
        with pytest.raises(ValueError) as raised:
            NoComment("x" * 41)

        assert str(raised.value) == f"NoComment needs a reason of at most 40 characters, not '{'x' * 41}'."

    def test_text_at_its_maximum_is_fine(self):
        assert NoComment("x" * 40).reason == "x" * 40

    def test_text_must_match_its_pattern_in_full(self):
        with pytest.raises(ValueError) as raised:
            PermissionName("org:invite:member!", label="x")

        assert str(raised.value) == (
            "PermissionName's name must match [a-z_]+:[a-z_]+:[a-z_]+, and 'org:invite:member!' doesn't."
        )

    def test_a_compiled_pattern_works_the_same(self):
        class Upper(DeclaredString):
            word = text(pattern=re.compile(r"[A-Z]+"))

        assert Upper("ABC") == "ABC"
        with pytest.raises(ValueError):
            Upper("abc")

    def test_text_must_be_a_str(self):
        with pytest.raises(TypeError) as raised:
            Exemption(42)

        assert str(raised.value) == "Exemption's reason must be text, not 42."

    def test_an_attribute_must_be_its_type(self):
        with pytest.raises(TypeError) as raised:
            PermissionName("a:b:c", label="x", delegatable="yes")

        assert str(raised.value) == "PermissionName's delegatable must be a bool, not 'yes'."

    def test_an_untyped_attribute_takes_anything(self):
        assert Marker(note=3).note == 3

    def test_a_required_attribute_left_out_is_refused(self):
        with pytest.raises(TypeError) as raised:
            PermissionName("a:b:c")

        assert str(raised.value) == "PermissionName needs a label."

    def test_the_text_can_be_passed_by_name(self):
        assert Exemption(reason=REASON).reason == REASON

    def test_the_text_passed_twice_is_refused(self):
        with pytest.raises(TypeError) as raised:
            Exemption(REASON, reason=REASON)

        assert str(raised.value) == "Exemption got reason twice."

    def test_an_undeclared_keyword_is_refused(self):
        with pytest.raises(TypeError) as raised:
            Exemption(REASON, why="x", also="y")

        assert str(raised.value) == "Exemption declares no also, why."

    def test_too_many_positional_arguments_are_refused(self):
        with pytest.raises(TypeError) as raised:
            Exemption(REASON, "more")

        assert str(raised.value) == "Exemption takes 1 positional argument(s), not 2."

    def test_a_class_with_no_text_takes_no_positional_argument(self):
        with pytest.raises(TypeError) as raised:
            Marker("why")

        assert str(raised.value) == "Marker takes 0 positional argument(s), not 1."

    def test_a_class_with_neither_text_nor_displays_as_is_refused(self):
        with pytest.raises(TypeError) as raised:
            type("Nothing", (DeclaredString,), {"note": attribute()})

        assert str(raised.value) == "Nothing declares no text() and no displays_as=, so it has nothing to be."


class TestAsItself:
    @pytest.mark.parametrize(
        "value",
        [
            Exemption(REASON),
            NoComment(REASON),
            PermissionName("a:b:c", label="x", delegatable=True),
            Marker(note="why"),
        ],
        ids=repr,
    )
    def test_survives_copy_deepcopy_and_pickle(self, value):
        for again in (copy.copy(value), copy.deepcopy(value), pickle.loads(pickle.dumps(value))):
            assert type(again) is type(value)
            assert again == value
            assert again.__dict__ == value.__dict__

    def test_deconstructs_as_itself_for_migrations(self):
        assert NoComment(REASON).deconstruct() == (
            "tests.common.utils.test_declared_string.NoComment",
            (REASON,),
            {},
        )

    def test_deconstructs_only_attributes_that_differ_from_their_default(self):
        assert PermissionName("a:b:c", label="x").deconstruct()[1:] == (("a:b:c",), {"label": "x"})
        assert PermissionName("a:b:c", label="x", delegatable=True).deconstruct()[2] == {
            "label": "x",
            "delegatable": True,
        }

    def test_repr_reads_as_the_call_that_made_it(self):
        assert repr(PermissionName("a:b:c", label="x")) == "PermissionName('a:b:c', label='x')"
        assert repr(Marker()) == "Marker()"

    def test_a_django_migration_writes_it_as_itself(self):
        from django.db.migrations.writer import MigrationWriter

        written, imports = MigrationWriter.serialize(NoComment(REASON))

        assert written == f"tests.common.utils.test_declared_string.NoComment({REASON!r})"
        assert imports == {"import tests.common.utils.test_declared_string"}


class TestTheDeclarations:
    def test_text_collapses_whitespace_unless_told_not_to(self):
        # Declared here rather than at module level: a declaration made while the module is imported
        # is made before any test runs.
        class Collapsed(DeclaredString):
            body = text()

        class Spelled(DeclaredString):
            body = DeclaredText()

        assert Collapsed(" a  b ") == Spelled(" a  b ") == "a b"
        assert (text().collapse_whitespace, DeclaredText().collapse_whitespace) == (True, True)

    def test_displays_as_is_what_the_class_says(self):
        class Unsaid(DeclaredString, displays_as="-"):
            reason = text()

        assert (Unsaid("why"), Unsaid.displays_as) == ("-", "-")

    def test_text_and_attribute_are_spellings_of_the_classes(self):
        declared = text(min_length=1, max_length=2, pattern="a", collapse_whitespace=False)

        assert isinstance(declared, DeclaredText)
        assert (declared.min_length, declared.max_length, declared.pattern.pattern, declared.collapse_whitespace) == (
            1,
            2,
            "a",
            False,
        )
        assert isinstance(attribute(int, default=3), DeclaredAttribute)
        assert (attribute(int, default=3).type, attribute(int, default=3).default) == (int, 3)

    def test_a_declaration_has_no_default_unless_given(self):
        assert text().default is MISSING
        assert attribute().default is MISSING

    def test_declarations_are_collected_in_order_through_the_bases(self):
        assert list(PermissionName.declarations) == ["name", "label", "delegatable"]
        assert list(NoHelpText.declarations) == ["reason"]

    def test_a_bare_declaration_says_what_to_implement(self):
        with pytest.raises(NotImplementedError):
            Declaration().clean(Exemption, "reason", "x")
