"""Exemption - a str skipping one named rule, carrying a reason held to a floor, and listed by its rule."""

import copy
import importlib.util
import pickle
import sys
import types
from pathlib import Path

import pytest

from isik.common.utils import exemptions
from isik.common.utils.exemptions import (
    Exemption,
    assert_exemption_budget,
    declared_exemptions,
    exemption_class,
    exemption_types,
    unseen_exemption_calls,
)


# Where this module's code says it is - under mutmut, a copied test's cached bytecode can name the
# original file rather than __file__.
HERE = str(Path(sys._getframe().f_code.co_filename).resolve())
REASON = "the labels of its choices already say what it holds"

NoHelpText = exemption_class(
    "NoHelpText",
    rule="tests.help-text",
    why="Every field should say what it holds in help_text.",
    shows_as="",
)


class NotAtomic(Exemption, rule="tests.atomic-requests", why="Every request runs in a transaction.", min_length=30):
    pass


class Documented(Exemption, rule="tests.documented", why="Says why."):
    """Its own docstring, kept."""


class Stricter(NotAtomic, min_length=50):
    pass


class TestMaking:
    def test_a_reason_over_the_floor_is_accepted_and_shows_as_declared(self):
        made = NoHelpText(reason=REASON)

        assert made == ""
        assert not made
        assert made.reason == REASON

    def test_without_shows_as_it_is_its_reason(self):
        made = NotAtomic(reason="streams to storage the whole time")

        assert made == "streams to storage the whole time"
        assert made

    def test_whitespace_runs_collapse_before_counting(self):
        assert (
            NotAtomic(reason="  streams   to storage\nthe whole   time ").reason == "streams to storage the whole time"
        )

    def test_a_short_reason_is_refused_with_the_rule_it_skips(self):
        with pytest.raises(ValueError) as raised:
            NoHelpText(reason="it is obvious")

        assert str(raised.value) == (
            "NoHelpText needs a reason of at least 40 characters. Every field should say what it holds in "
            "help_text. Got 'it is obvious'."
        )

    def test_whitespace_padding_does_not_reach_the_floor(self):
        with pytest.raises(ValueError, match="at least 30"):
            NotAtomic(reason="short" + " " * 40)

    def test_a_positional_reason_is_refused(self):
        with pytest.raises(TypeError, match=r"^NoHelpText takes its reason as reason=, not positionally\.$"):
            NoHelpText(REASON)

    def test_an_unknown_keyword_is_refused(self):
        with pytest.raises(TypeError, match=r"^NoHelpText takes only reason=, not label, why\.$"):
            NoHelpText(reason=REASON, why="x", label="y")

    def test_a_missing_reason_is_refused(self):
        with pytest.raises(TypeError, match=r"^NoHelpText needs reason=\.$"):
            NoHelpText()

    def test_a_reason_must_be_text(self):
        with pytest.raises(TypeError, match=r"^NoHelpText's reason must be text, not 42\.$"):
            NoHelpText(reason=42)

    def test_the_base_is_not_an_exemption_itself(self):
        with pytest.raises(TypeError, match=r"^Exemption is the base to make exemption types from"):
            Exemption(reason=REASON)


class TestTypes:
    def test_exemption_class_makes_a_class_in_the_callers_module(self):
        assert (NoHelpText.__module__, NoHelpText.__qualname__) == (__name__, "NoHelpText")
        assert issubclass(NoHelpText, Exemption)
        assert NoHelpText.rule == "tests.help-text"

    def test_why_is_the_docstring_unless_it_has_one(self):
        assert NoHelpText.__doc__ == "Every field should say what it holds in help_text."
        assert Documented.__doc__ == "Its own docstring, kept."
        assert Documented.why == "Says why."

    def test_the_default_floor_is_forty(self):
        assert (NoHelpText.min_length, NotAtomic.min_length) == (40, 30)

    def test_a_subclass_keeps_its_parents_rule_and_why_and_raises_the_floor(self):
        assert (Stricter.rule, Stricter.why, Stricter.min_length) == (NotAtomic.rule, NotAtomic.why, 50)
        assert exemption_types()["tests.atomic-requests"] is NotAtomic
        with pytest.raises(ValueError, match="at least 50"):
            Stricter(reason="streams to storage the whole time")

    def test_every_type_is_listed_by_its_rule(self):
        types = exemption_types()

        assert types["tests.help-text"] is NoHelpText
        assert list(types) == sorted(types)

    def test_a_type_without_a_rule_is_refused(self):
        with pytest.raises(TypeError, match=r"^Ruleless needs rule= - the dotted name of the rule it exempts from\.$"):
            type("Ruleless", (Exemption,), {}, why="x")

    @pytest.mark.parametrize("rule", ["Schema.Help", "schema docs", "schema..docs", ".schema", 3])
    def test_a_malformed_rule_is_refused(self, rule):
        with pytest.raises(TypeError) as raised:
            type("Malformed", (Exemption,), {}, rule=rule, why="x")

        assert str(raised.value) == (
            f"Malformed's rule must be a dotted slug like 'schema-docs.help-text', not {rule!r}."
        )

    def test_a_rule_another_type_claims_is_refused_naming_both(self):
        with pytest.raises(TypeError) as raised:
            exemption_class("Again", rule="tests.help-text", why="x")

        assert str(raised.value) == (
            f"Again claims the rule 'tests.help-text', which {__name__}.NoHelpText already exempts from - name another."
        )

    def test_the_same_class_defined_again_replaces_itself(self):
        first = exemption_class("Redefined", rule="tests.redefined", why="x")
        second = exemption_class("Redefined", rule="tests.redefined", why="x")

        assert first is not second
        assert exemption_types()["tests.redefined"] is second

    @pytest.mark.parametrize("why", [None, "", "  "])
    def test_a_type_without_why_is_refused(self, why):
        with pytest.raises(TypeError, match=r"^Unexplained needs why= - what the rule asks for, and why it matters\.$"):
            type("Unexplained", (Exemption,), {}, rule="tests.unexplained", why=why)

    @pytest.mark.parametrize("min_length", [0, -1, 2.5, True, "40"])
    def test_a_floor_must_be_a_whole_number_of_at_least_one(self, min_length):
        with pytest.raises(TypeError) as raised:
            type("Floored", (Exemption,), {}, rule="tests.floored", why="x", min_length=min_length)

        assert str(raised.value) == (f"Floored's min_length must be a whole number of at least 1, not {min_length!r}.")

    def test_shows_as_must_be_a_str(self):
        with pytest.raises(TypeError, match=r"^Shown's shows_as must be a str, not 0\.$"):
            type("Shown", (Exemption,), {}, rule="tests.shown", why="x", shows_as=0)

    def test_a_refused_type_claims_no_rule(self):
        with pytest.raises(TypeError):
            type("Unexplained", (Exemption,), {}, rule="tests.never-claimed", why="")

        assert "tests.never-claimed" not in exemption_types()


class TestATypeMadeNow:
    """Classes made while a test runs - a module's own are made at import, before any test can watch."""

    def test_its_keywords_are_its_attributes(self):
        made = type("MadeNow", (Exemption,), {}, rule="tests.made-now", why="Made now.", min_length=1, shows_as="-")

        assert (made.rule, made.why, made.__doc__, made.min_length, made.shows_as) == (
            "tests.made-now",
            "Made now.",
            "Made now.",
            1,
            "-",
        )
        assert exemption_types()["tests.made-now"] is made
        assert made(reason="a") == "-"

    def test_left_out_keywords_keep_the_defaults(self):
        made = type("Defaulted", (Exemption,), {}, rule="tests.defaulted", why="Defaults.")

        assert (made.min_length, made.shows_as) == (40, None)
        assert made(reason="x" * 40) == "x" * 40

    def test_a_reason_exactly_at_the_floor_is_accepted(self):
        made = type("Floor", (Exemption,), {}, rule="tests.floor", why="x", min_length=5)

        assert made(reason="abcde").reason == "abcde"
        with pytest.raises(ValueError):
            made(reason="abcd")

    def test_a_subclass_made_now_keeps_its_parents_rule_why_and_shows_as(self):
        parent = type("Parent", (Exemption,), {}, rule="tests.parent", why="The parent's.", shows_as="")
        child = type("Child", (parent,), {}, min_length=60)

        assert (child.rule, child.why, child.__doc__, child.shows_as, child.min_length) == (
            "tests.parent",
            "The parent's.",
            "The parent's.",
            "",
            60,
        )
        assert exemption_types()["tests.parent"] is parent

    def test_a_docstring_of_its_own_is_kept(self):
        made = type("Own", (Exemption,), {"__doc__": "Its own."}, rule="tests.own", why="Why.")

        assert made.__doc__ == "Its own."

    def test_exemption_class_passes_every_keyword_on(self):
        made = exemption_class("OneLine", rule="tests.one-line", why="One line.", min_length=3, shows_as="")

        assert (made.__name__, made.__qualname__, made.__module__) == ("OneLine", "OneLine", __name__)
        assert (made.rule, made.why, made.min_length, made.shows_as) == ("tests.one-line", "One line.", 3, "")
        assert made(reason="abc") == ""


class TestAsItself:
    def test_equal_to_and_hashed_as_what_it_shows_as(self):
        made = NotAtomic(reason="streams to storage the whole time")

        assert made == "streams to storage the whole time"
        assert hash(made) == hash("streams to storage the whole time")
        assert NoHelpText(reason=REASON) == NoHelpText(reason=REASON + " too")

    def test_copies_are_itself(self):
        made = NoHelpText(reason=REASON)

        assert copy.copy(made) is made
        assert copy.deepcopy(made) is made

    def test_pickles_as_itself(self):
        restored = pickle.loads(pickle.dumps(NoHelpText(reason=REASON)))

        assert type(restored) is NoHelpText
        assert restored.reason == REASON
        assert restored == ""

    def test_an_unpickled_one_is_not_a_new_declaration(self):
        made = NoHelpText(reason=REASON)
        count = len(declared_exemptions("tests.help-text"))

        pickle.loads(pickle.dumps(made))

        assert len(declared_exemptions("tests.help-text")) == count

    def test_deconstructs_as_the_call_that_made_it(self):
        assert NoHelpText(reason=REASON).deconstruct() == (f"{__name__}.NoHelpText", (), {"reason": REASON})

    def test_repr_reads_as_the_call(self):
        assert repr(NoHelpText(reason=REASON)) == f"NoHelpText(reason={REASON!r})"

    def test_django_writes_it_into_a_migration_as_the_call(self):
        from django.db.migrations.writer import MigrationWriter

        written, imports = MigrationWriter.serialize(NoHelpText(reason=REASON))

        assert written == f"{__name__}.NoHelpText(reason={REASON!r})"
        assert imports == {f"import {__name__}"}


class Budgeted(Exemption, rule="tests.budgeted", why="Kept to a budget."):
    pass


class TestListing:
    def test_each_records_where_it_was_made(self):
        made, line = NoHelpText(reason=REASON), sys._getframe().f_lineno

        assert (made.file, made.line) == (HERE, line)

    def test_declared_exemptions_by_rule(self):
        made = NotAtomic(reason="streams to storage the whole time")

        assert declared_exemptions("tests.atomic-requests")[-1] is made
        assert made in declared_exemptions()
        assert all(each.rule == "tests.atomic-requests" for each in declared_exemptions("tests.atomic-requests"))

    def test_one_made_in_a_migration_is_replayed_not_declared(self, tmp_path):
        count = len(declared_exemptions("tests.help-text"))
        code = compile(f"made = NoHelpText(reason={REASON!r})", str(tmp_path / "0001_initial.py"), "exec")
        namespace = {"__name__": "app.migrations.0001_initial", "NoHelpText": NoHelpText}

        exec(code, namespace)

        assert namespace["made"].reason == REASON
        assert len(declared_exemptions("tests.help-text")) == count

    def test_a_projects_own_types_are_declared_where_its_models_made_them(self):
        from tests.testapp import models

        [declared] = declared_exemptions("testapp.help-text")

        assert declared.file == str(Path(models.__file__).resolve())

    def test_a_budget_kept(self):
        Budgeted(reason="one exemption, inside the budget this test gives")

        assert_exemption_budget("tests.budgeted", at_most=len(declared_exemptions("tests.budgeted")))

    def test_a_budget_exceeded_names_every_exemption(self):
        made = Budgeted(reason="one exemption, inside the budget this test gives")
        count = len(declared_exemptions("tests.budgeted"))

        with pytest.raises(AssertionError) as raised:
            assert_exemption_budget("tests.budgeted", at_most=count - 1)

        where = "\n".join(f"  {each.file}:{each.line} {each.reason}" for each in declared_exemptions("tests.budgeted"))
        assert str(raised.value) == f"tests.budgeted has {count} exemptions, over its budget of {count - 1}:\n{where}"
        assert f"  {made.file}:{made.line} {made.reason}" in where

    def test_a_budget_for_a_rule_nothing_exempts_from_is_refused(self):
        with pytest.raises(LookupError, match=r"^No exemption type exempts from 'tests.nothing'\.$"):
            assert_exemption_budget("tests.nothing", at_most=0)


class TestUnseenCalls:
    def test_a_call_that_never_ran_is_found_and_one_that_ran_is_not(self, tmp_path):
        source = tmp_path / "declarations.py"
        source.write_text(
            "from tests.common.utils.test_exemptions import Budgeted, NoHelpText\n"
            "\n"
            "ran = Budgeted(reason='made at import, so the import pass sees it')\n"
            "\n"
            "\n"
            "def later():\n"
            "    return NoHelpText(\n"
            "        reason='made only once later() runs, which it never does',\n"
            "    )\n"
            "\n"
            "\n"
            "not_one = dict(reason='a call to something that is not an exemption type')\n"
            "made_by = [dict][0](reason='a call through a subscript names nothing')\n"
        )
        (tmp_path / "migrations").mkdir()
        (tmp_path / "migrations" / "0001_initial.py").write_text("NoHelpText(reason='replayed')\n")
        (tmp_path / "broken.py").write_text("def (:\n")
        # After migrations/ in the walk, so a migration ends only its own file's scan.
        (tmp_path / "zz.py").write_text("NoHelpText(reason='never imported, so never made either')\n")
        spec = importlib.util.spec_from_file_location("declarations", source)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))

        assert unseen_exemption_calls([tmp_path]) == [
            (str(source.resolve()), 7, "NoHelpText"),
            (str((tmp_path / "zz.py").resolve()), 1, "NoHelpText"),
        ]

    def test_an_attribute_call_counts(self, tmp_path):
        source = tmp_path / "attribute.py"
        source.write_text("import exemptions\n\nexemptions.NoHelpText(reason='never imported, so never made')\n")

        assert unseen_exemption_calls([tmp_path]) == [(str(source.resolve()), 3, "NoHelpText")]


def frame_at(file, back=None):
    return types.SimpleNamespace(f_code=types.SimpleNamespace(co_filename=file), f_back=back)


class TestCallSite:
    def test_with_only_libraries_outside_isik_the_nearest_of_them_is_the_site(self, monkeypatch):
        monkeypatch.setattr(exemptions, "_ISIK", "/isik/")
        monkeypatch.setattr(exemptions, "_LIBRARIES", ("/libraries/",))
        outer = frame_at("/libraries/outer.py")
        library = frame_at("/libraries/django.py", frame_at("/libraries/pytest.py", outer))
        inner = frame_at("/isik/reasons.py", library)

        assert exemptions._call_site(inner) == (library, "/libraries/django.py")

    def test_the_nearest_frame_outside_both_wins(self, monkeypatch):
        monkeypatch.setattr(exemptions, "_ISIK", "/isik/")
        monkeypatch.setattr(exemptions, "_LIBRARIES", ("/libraries/",))
        project = frame_at("/project/models.py")
        inner = frame_at("/isik/reasons.py", frame_at("/libraries/django.py", project))

        assert exemptions._call_site(inner) == (project, "/project/models.py")

    def test_with_nothing_outside_isik_it_has_no_site_and_is_not_declared(self, monkeypatch):
        monkeypatch.setattr(exemptions, "_ISIK", "/")
        count = len(declared_exemptions("tests.help-text"))

        made = NoHelpText(reason=REASON)

        assert (made.file, made.line) == (None, None)
        assert len(declared_exemptions("tests.help-text")) == count

    def test_with_nothing_outside_isik_a_class_needs_its_module_named(self, monkeypatch):
        monkeypatch.setattr(exemptions, "_ISIK", "/")

        with pytest.raises(TypeError, match=r"^exemption_class\(\) can't tell which module Siteless is in - pass"):
            exemption_class("Siteless", rule="tests.siteless", why="x")

    def test_a_named_module_is_used_as_given(self):
        assert exemption_class("Placed", rule="tests.placed", why="x", module="app.exemptions").__module__ == (
            "app.exemptions"
        )
