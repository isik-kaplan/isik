"""Every user-facing string isik produces is translatable - and translation actually happens."""

import ast
import gettext as stdlib_gettext
import os
import re
import shutil
import struct
import subprocess
import sys

import pytest

from isik._internal import translation
from tests import translation_catalog


EXCEPTION_NAME = re.compile(r"^[A-Z]\w*(Error|Exception|Warning|Denied|Configured|NotFound|Invalid)$")
USER_FACING_KEYWORDS = {"message", "verbose_name", "verbose_name_plural", "help_text", "label", "short_description"}
USER_FACING_ATTRIBUTES = USER_FACING_KEYWORDS | {"default_error_messages"}
# The translation module passes variables to gettext by design - that's its job.
EXEMPT = {"isik/_internal/translation.py"}


def is_raw_text(node):
    return isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, str))


def raw_texts(node):
    """Literal text inside an expression that reaches the user untranslated - not dict keys (field
    names, identifiers), not anything already passed through a translation function."""
    if is_raw_text(node):
        yield node
        return
    if isinstance(node, ast.Call) and translation_catalog.called_name(node) in translation_catalog.TRANSLATORS:
        return
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and is_raw_text(node.func.value):
        # `", ".join(names)` - the string is a separator, not text; its arguments still count
        for arg in node.args:
            yield from raw_texts(arg)
        return
    if isinstance(node, ast.Dict):
        for value in node.values:
            yield from raw_texts(value)
        return
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.keyword):  # `code="..."` and the like are identifiers, not text
            continue
        yield from raw_texts(child)


def violations():
    found = []
    for path in sorted(translation_catalog.PACKAGE.rglob("*.py")):
        where = path.relative_to(translation_catalog.ROOT)
        if str(where) in EXEMPT:
            continue
        tree = ast.parse(path.read_text())
        raised = {id(node.exc) for node in ast.walk(tree) if isinstance(node, ast.Raise) and node.exc is not None}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = translation_catalog.called_name(node) or ""
                if (
                    name in translation_catalog.TRANSLATORS
                    and node.args
                    and not (isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str))
                ):
                    found.append(f"{where}:{node.lineno} {name}() needs a constant msgid, not an expression")
                if id(node) in raised or EXCEPTION_NAME.match(name):
                    for text in (text for arg in node.args for text in raw_texts(arg)):
                        found.append(f"{where}:{text.lineno} untranslated text in {name}(...)")
                for keyword in node.keywords:
                    if keyword.arg in USER_FACING_KEYWORDS and is_raw_text(keyword.value):
                        found.append(f"{where}:{keyword.value.lineno} untranslated {keyword.arg}=")
            if isinstance(node, ast.ClassDef):
                for statement in node.body:
                    if isinstance(statement, ast.Assign) and any(
                        getattr(target, "id", None) in USER_FACING_ATTRIBUTES for target in statement.targets
                    ):
                        for text in raw_texts(statement.value):
                            found.append(f"{where}:{text.lineno} untranslated class attribute")
    return found


# mutmut runs the suite against its own rewritten copy of the source, which these read instead of
# isik's - and a check on how source is written has nothing to say about a mutant's behaviour.
@pytest.mark.skipif("MUTANT_UNDER_TEST" in os.environ, reason="reads source, which mutmut has rewritten")
class TestEverythingIsTranslatable:
    def test_no_user_facing_string_bypasses_translation(self):
        assert violations() == []

    def test_the_committed_catalog_is_current(self):
        assert translation_catalog.CATALOG.read_text() == translation_catalog.render(), (
            "isik/locale/isik.pot is stale - run `python -m tests.translation_catalog`"
        )

    def test_every_http_status_description_of_this_python_is_in_the_catalog(self):
        from http import HTTPStatus

        missing = {status.description for status in HTTPStatus if status.description} - set(
            translation_catalog.HTTP_STATUS_DESCRIPTIONS
        )
        assert not missing, f"add these to HTTP_STATUS_DESCRIPTIONS in tests/translation_catalog.py: {missing}"

    def test_the_catalog_lists_messages_from_across_the_package(self):
        # guards the extractor itself: an empty or tiny catalog would still be "current"
        assert len(translation_catalog.messages()) > 50

    @pytest.mark.skipif(shutil.which("msgfmt") is None, reason="needs GNU gettext's msgfmt")
    def test_the_catalog_is_valid_gettext(self):
        result = subprocess.run(
            ["msgfmt", "--check", "-o", "/dev/null", str(translation_catalog.CATALOG)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


class TestTheLintItself:
    def check(self, source):
        tree = ast.parse(source)
        call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))
        return list(raw_texts(call.args[0])) if call.args else []

    def test_it_sees_a_plain_string_and_an_f_string(self):
        assert self.check('raise ValueError("oops")')
        assert self.check('raise ValueError(f"bad {x}")')

    def test_it_accepts_translated_text_and_dict_keys(self):
        assert not self.check('raise ValueError(_("oops %(x)s") % {"x": x})')
        assert not self.check('raise ValidationError({"field": [_("bad")]})')

    def test_a_separator_string_is_not_text_but_its_arguments_are(self):
        assert not self.check('raise ValueError(_("in %(x)s") % {"x": ", ".join(names)})')
        assert self.check('raise ValueError(", ".join(["oops"]))')

    def test_it_sees_untranslated_values_inside_a_dict(self):
        assert self.check('raise ValidationError({"field": ["bad"]})')


def write_mo(path, catalog):
    """A minimal GNU .mo file, so the stdlib path is tested without depending on msgfmt."""
    keys = sorted(catalog)
    ids = b"".join(key.encode() + b"\0" for key in keys)
    strs = b"".join(catalog[key].encode() + b"\0" for key in keys)
    header_size = 7 * 4
    ids_start = header_size + 16 * len(keys)
    strs_start = ids_start + len(ids)
    offsets, id_offset, str_offset = [], 0, 0
    for key in keys:
        offsets.append((len(key.encode()), ids_start + id_offset, len(catalog[key].encode()), strs_start + str_offset))
        id_offset += len(key.encode()) + 1
        str_offset += len(catalog[key].encode()) + 1
    output = struct.pack("Iiiiiii", 0x950412DE, 0, len(keys), header_size, header_size + 8 * len(keys), 0, 0)
    output += b"".join(struct.pack("ii", length, start) for length, start, _, _ in offsets)
    output += b"".join(struct.pack("ii", length, start) for _, _, length, start in offsets)
    path.write_bytes(output + ids + strs)


class TestTranslationRuntime:
    def test_django_catalogs_translate_it(self, monkeypatch):
        monkeypatch.setattr("django.utils.translation.gettext", lambda message: message.upper())
        assert translation.gettext("hello") == "HELLO"

    def test_before_django_is_ready_it_falls_back_instead_of_raising(self, monkeypatch):
        from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured

        for error in (AppRegistryNotReady, ImproperlyConfigured):

            def not_ready(message, error=error):
                raise error("not yet")

            monkeypatch.setattr("django.utils.translation.gettext", not_ready)
            assert translation.gettext("hello") == "hello"

    def test_without_django_the_isik_domain_is_used(self, monkeypatch, tmp_path):
        import django.utils

        # `from django.utils import translation` reads the package attribute first, so both have to go
        monkeypatch.delattr(django.utils, "translation")
        monkeypatch.setitem(sys.modules, "django.utils.translation", None)
        (tmp_path / "tr" / "LC_MESSAGES").mkdir(parents=True)
        write_mo(tmp_path / "tr" / "LC_MESSAGES" / "isik.mo", {"hello": "merhaba"})
        monkeypatch.setenv("LANGUAGE", "tr")
        stdlib_gettext.bindtextdomain(translation.DOMAIN, str(tmp_path))
        try:
            assert translation.gettext("hello") == "merhaba"
            assert translation.gettext("unknown") == "unknown"
        finally:
            stdlib_gettext.bindtextdomain(translation.DOMAIN, None)

    def test_a_lazy_message_translates_when_rendered(self, monkeypatch):
        language = {"current": "en"}
        monkeypatch.setattr(
            "django.utils.translation.gettext",
            lambda message: message.upper() if language["current"] == "shout" else message,
        )
        message = translation.gettext_lazy("hello")
        assert str(message) == "hello"
        language["current"] = "shout"
        assert str(message) == "HELLO"

    def test_lazy_format_fills_placeholders_after_translating(self, monkeypatch):
        monkeypatch.setattr("django.utils.translation.gettext", lambda message: message.replace("needs", "requires"))
        assert str(translation.lazy_format("%(who)s needs %(what)s", who="alice", what="tea")) == "alice requires tea"

    def test_a_lazy_message_behaves_as_a_string(self):
        # DRF calls str methods on lazy messages - default_error_messages go through .format()
        assert translation.gettext_lazy("hello {name}").format(name="alice") == "hello alice"
        assert translation.lazy_format("%(n)s done", n=3).upper() == "3 DONE"

    def test_a_message_without_placeholders_keeps_a_literal_percent(self):
        assert str(translation.gettext_lazy("100% done")) == "100% done"

    def test_without_django_lazy_translates_straight_away(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "django.utils.functional", None)
        message = translation.lazy_format("%(n)s items", n=3)
        assert message == "3 items"
        assert type(message) is str
