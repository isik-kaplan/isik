"""Exemption - a str skipping one named rule, carrying a reason held to a floor, and listed by its rule."""

import copy
import functools
import importlib.util
import pickle
import sys
import types
from pathlib import Path

import pytest

from isik.common.utils import exemptions
from isik.common.utils.exemptions import (
    TEST_CODE,
    Exemption,
    assert_exemption_budget,
    declared_exemptions,
    exemption_class,
    exemption_types,
    makes_exemption,
    unimported_exemption_types,
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


@makes_exemption(Budgeted)
def budget_for(reason):
    """A decorator making a Budgeted for whatever it decorates - a project's own helper, say."""
    made = Budgeted(reason=reason)

    def mark(function):
        function.budget = made
        return function

    return mark


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


class Code:
    def __init__(self, file):
        self.co_filename = file


def frame_at(file, back=None, code=None):
    return types.SimpleNamespace(f_code=code or Code(file), f_back=back)


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


class TestMakers:
    """A function making exemptions for its caller - each is recorded where it was called."""

    def test_each_is_recorded_where_the_maker_was_called(self, monkeypatch):
        # Fresh, so nothing a previous run of this test registered for the same code counts.
        monkeypatch.setattr(exemptions, "_makers", {})

        @makes_exemption(Budgeted)
        def make_budgeted(reason):
            return Budgeted(reason=reason)

        made, line = make_budgeted("made by a helper, recorded at its caller"), sys._getframe().f_lineno

        assert (made.file, made.line) == (HERE, line)
        assert exemptions._makers[make_budgeted.__code__] == "make_budgeted"

    def test_a_maker_under_other_decorators_is_marked_all_the_way_down(self, monkeypatch):
        monkeypatch.setattr(exemptions, "_makers", {})

        def passing_through(function):
            @functools.wraps(function)
            def wrapper(*args, **kwargs):
                return function(*args, **kwargs)

            return wrapper

        @makes_exemption(Budgeted)
        @passing_through
        def wrapped_maker(reason):
            return Budgeted(reason=reason)

        made, line = wrapped_maker("made two decorators deep, still recorded here"), sys._getframe().f_lineno

        assert (made.file, made.line) == (HERE, line)
        assert exemptions._makers[wrapped_maker.__code__] == "wrapped_maker"
        assert exemptions._makers[wrapped_maker.__wrapped__.__code__] == "wrapped_maker"

    def test_a_maker_frame_is_passed_over(self, monkeypatch):
        monkeypatch.setattr(exemptions, "_ISIK", "/isik/")
        monkeypatch.setattr(exemptions, "_LIBRARIES", ("/libraries/",))
        maker_code = Code("/project/transactions.py")
        monkeypatch.setitem(exemptions._makers, maker_code, "not_atomic")
        caller = frame_at("/project/views.py")
        inner = frame_at("/isik/exemptions.py", frame_at("/project/transactions.py", caller, maker_code))

        assert exemptions._call_site(inner) == (caller, "/project/views.py")

    @pytest.mark.parametrize("value", [Exemption, str, "NotAtomic", None])
    def test_it_takes_an_exemption_type(self, value):
        with pytest.raises(TypeError) as raised:
            makes_exemption(value)

        assert str(raised.value) == f"makes_exemption() takes an exemption type, not {value!r}."


def write(path, source):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source)
    return str(path.resolve())


CALL = "NoHelpText(reason='never imported, so never made at all')\n"


class TestWhatTheScanSkips:
    def test_test_code_is_skipped_by_default(self, tmp_path):
        for skipped in ("tests/views.py", "app/tests/deep/models.py", "test_views.py", "views_test.py", "conftest.py"):
            write(tmp_path / skipped, CALL)
        app = write(tmp_path / "app" / "views.py", CALL)

        assert TEST_CODE == ("tests", "test_*.py", "*_test.py", "conftest.py")
        assert unseen_exemption_calls([tmp_path]) == [(app, 1, "NoHelpText")]

    def test_exclude_says_what_else_to_skip(self, tmp_path):
        tests = write(tmp_path / "tests" / "views.py", CALL)
        app = write(tmp_path / "app.py", CALL)

        assert unseen_exemption_calls([tmp_path], exclude=()) == [(app, 1, "NoHelpText"), (tests, 1, "NoHelpText")]
        assert unseen_exemption_calls([tmp_path], exclude=("app.py",)) == [(tests, 1, "NoHelpText")]

    def test_only_the_path_under_the_root_is_matched(self, tmp_path):
        app = write(tmp_path / "tests" / "app" / "views.py", CALL)

        assert unseen_exemption_calls([tmp_path / "tests" / "app"]) == [(app, 1, "NoHelpText")]


DECLARATIONS = """\
from isik.common.utils.exemptions import Exemption, exemption_class as make, makes_exemption as marks


class Local(Exemption, rule="tmp.local", why="Declared, never imported."):
    pass


class Unrelated(dict):
    pass


Made = make("Made", rule="tmp.made", why="Made in one line, never imported.")
holder.Unpacked = make("Unpacked", rule="tmp.unpacked", why="Assigned to an attribute, so not a name.")


@marks(Local)
def not_atomic(reason):
    return Local(reason=reason)


@staticmethod
def plain():
    pass
"""

SUBCLASSES = """\
from declarations import Local as Renamed, Made


class FromMade(Made):
    pass


class Deeper(FromMade, min_length=60):
    pass


class Stricter(Renamed, min_length=60):
    pass
"""

CALLS = """\
from declarations import Local as Renamed, not_atomic


Renamed(reason="through an alias, never run")
Deeper(reason="a type declared two files and two classes away")
Renamed("positional, so not a call that makes one")
not_atomic("a maker called with its reason first")
not_atomic(reason="a maker called with reason=")
not_atomic()


@not_atomic("a maker used as a decorator")
def view():
    pass
"""


class TestTypesAndMakersReadFromSource:
    def files(self, tmp_path):
        return (
            write(tmp_path / "declarations.py", DECLARATIONS),
            write(tmp_path / "subclasses.py", SUBCLASSES),
            write(tmp_path / "calls.py", CALLS),
        )

    def test_types_declared_but_never_imported_are_found(self, tmp_path):
        declarations, subclasses, _ = self.files(tmp_path)

        assert unimported_exemption_types([tmp_path]) == [
            (declarations, 4, "Local"),
            (declarations, 12, "Made"),
            (subclasses, 4, "FromMade"),
            (subclasses, 8, "Deeper"),
            (subclasses, 12, "Stricter"),
        ]

    def test_calls_to_them_and_to_makers_are_unseen(self, tmp_path):
        declarations, _, calls = self.files(tmp_path)

        assert unseen_exemption_calls([tmp_path]) == [
            (calls, 4, "Local"),
            (calls, 5, "Deeper"),
            (calls, 7, "not_atomic"),
            (calls, 8, "not_atomic"),
            (calls, 12, "not_atomic"),
        ]

    def test_a_type_is_unimported_unless_its_own_module_defined_it(self, tmp_path, monkeypatch):
        source = (
            "from isik.common.utils.exemptions import Exemption\n\n\n"
            "class Imported(Exemption, rule='tmp.imported', why='x'):\n"
            "    pass\n"
        )
        imported = write(tmp_path / "imported.py", source)
        # Named like a type defined elsewhere - a different class, which nothing imported.
        elsewhere = write(tmp_path / "elsewhere.py", source.replace("tmp.imported", "tmp.elsewhere"))
        spec = importlib.util.spec_from_file_location("imported", imported)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, "imported", module)
        spec.loader.exec_module(module)

        assert unimported_exemption_types([tmp_path]) == [(elsewhere, 4, "Imported")]

    def test_unaliased_declarations_count_too(self, tmp_path):
        plain = write(
            tmp_path / "plain.py",
            "from isik.common.utils.exemptions import exemption_class, makes_exemption\n"
            "\n"
            "Plain = exemption_class('Plain', rule='tmp.plain', why='Declared under its own name.')\n"
            "\n"
            "\n"
            "@makes_exemption(Plain)\n"
            "def plain_maker(reason):\n"
            "    pass\n"
            "\n"
            "\n"
            "plain_maker('called by its own name, never run')\n",
        )

        assert unimported_exemption_types([tmp_path]) == [(plain, 3, "Plain")]
        assert unseen_exemption_calls([tmp_path]) == [(plain, 11, "plain_maker")]

    def test_types_in_test_code_are_skipped_unless_told_otherwise(self, tmp_path):
        source = write(
            tmp_path / "tests" / "types.py", "class InATest(Exemption, rule='tmp.in-a-test', why='x'):\n    pass\n"
        )

        assert unimported_exemption_types([tmp_path]) == []
        assert unimported_exemption_types([tmp_path], exclude=()) == [(source, 1, "InATest")]

    def test_a_module_no_longer_loaded_has_no_file(self):
        assert exemptions._module_file("no.such.module") is None
        assert exemptions._module_file(exemptions.__name__) == str(Path(exemptions.__file__).resolve())


class TestRunMakersAreSeen:
    def test_a_decorator_use_that_ran_is_seen_and_one_that_did_not_is_not(self, tmp_path):
        source = write(
            tmp_path / "views.py",
            "from tests.common.utils.test_exemptions import budget_for\n"
            "\n"
            "\n"
            "@budget_for('a decorator use that ran when the module loaded')\n"
            "def view():\n"
            "    pass\n"
            "\n"
            "\n"
            "def later():\n"
            "    return budget_for('a call that only runs once later() does')\n",
        )
        spec = importlib.util.spec_from_file_location("views", source)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))

        assert (declared_exemptions("tests.budgeted")[-1].file, declared_exemptions("tests.budgeted")[-1].line) == (
            source,
            4,
        )
        assert unseen_exemption_calls([tmp_path]) == [(source, 10, "budget_for")]


class TestMakerBodies:
    """What a maker makes is recorded at its caller, so the calls in its own body are never unseen."""

    def test_calls_inside_a_maker_are_not_unseen_and_calls_to_it_are(self, tmp_path):
        source = write(
            tmp_path / "transactions.py",
            "from isik.common.utils.exemptions import makes_exemption\n"
            "\n"
            "\n"
            "@makes_exemption(NotAtomic)\n"
            "def not_atomic(reason):\n"
            "    made = NotAtomic(reason=reason)\n"
            "\n"
            "    def nested():\n"
            "        return NoHelpText(reason='inside a function inside the maker')\n"
            "\n"
            "    return made\n"
            "\n"
            "\n"
            "@makes_exemption(NotAtomic)\n"
            "async def not_atomic_either(reason):\n"
            "    return not_atomic(reason)\n"
            "\n"
            "\n"
            "def plain(reason):\n"
            "    return NotAtomic(reason=reason)\n"
            "\n"
            "\n"
            "not_atomic('never run, so never made')\n",
        )

        assert unseen_exemption_calls([tmp_path]) == [(source, 20, "NotAtomic"), (source, 23, "not_atomic")]

    def test_a_maker_registered_without_the_decorator_syntax_counts_too(self, tmp_path, monkeypatch):
        monkeypatch.setitem(exemptions._makers, Code("elsewhere.py"), "registered_maker")
        source = write(
            tmp_path / "helpers.py",
            "def registered_maker(reason):\n    return NoHelpText(reason=reason)\n\n\nregistered_maker('never run')\n",
        )

        assert unseen_exemption_calls([tmp_path]) == [(source, 5, "registered_maker")]
