"""The parts of docs/naming.md a test can see - so a name that breaks them fails here, not in review."""

import ast
import os
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "isik"

# mutmut runs the suite from its own copy, and a check on how source is written has nothing to say
# about a mutant's behavior.
pytestmark = pytest.mark.skipif("MUTANT_UNDER_TEST" in os.environ, reason="reads source, not code")

# Rule 3: the British spellings worth catching - each matched as the start of a word, so `honour`
# catches `honours`, and `organis` catches `organisation`/`organise`.
BRITISH = ["honour", "behaviour", "colour", "favour", "flavour", "labour", "neighbour", "organis",
           "recognis", "initialis", "serialis", "normalis", "catalogue", "licence"]  # fmt: skip
BRITISH_WORD = re.compile(rf"\b({'|'.join(BRITISH)})", re.IGNORECASE)

# Rule 2: acronyms as they'd look wrongly cased inside a PascalCase class name.
ACRONYMS = ["Uuid", "Json", "Http", "Url", "Api", "Sql", "Html", "Amqp", "Drf", "Orm", "Csv", "Xml"]
# Rule 1: truncations that have turned up, or are the obvious next ones.
TRUNCATIONS = ["Str", "Gfk", "Ctx", "Cfg", "Msg"]
CAMEL_SEGMENT = re.compile(r"[A-Z][a-z0-9]*")


def sources():
    return sorted(PACKAGE.rglob("*.py"))


def texts():
    return [*sources(), *sorted((ROOT / "docs").rglob("*.md")), ROOT / "README.md", ROOT / "CHANGELOG.md"]


def class_names():
    for path in sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef):
                yield path.relative_to(ROOT), node.name


def segments(name):
    return CAMEL_SEGMENT.findall(name.lstrip("_"))


def test_spelling_is_american():
    found = [
        f"{path.relative_to(ROOT)}:{number}: {match.group(0)}"
        for path in texts()
        for number, line in enumerate(path.read_text().splitlines(), 1)
        for match in BRITISH_WORD.finditer(line)
    ]
    assert found == []


def test_class_names_keep_acronyms_in_capitals():
    found = [f"{path}: {name}" for path, name in class_names() if set(segments(name)) & set(ACRONYMS)]
    assert found == []


def test_class_names_spell_words_out():
    found = [f"{path}: {name}" for path, name in class_names() if set(segments(name)) & set(TRUNCATIONS)]
    assert found == []


def test_module_names_spell_words_out():
    found = [str(path.relative_to(ROOT)) for path in sources() if path.stem in {"gfk", "ctx", "cfg", "msg"}]
    assert found == []


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("JSONBodyCodec", ["J", "S", "O", "N", "Body", "Codec"]),
        ("DeclaredStr", ["Declared", "Str"]),
        ("_Hidden", ["Hidden"]),
    ],
)
def test_segments_split_a_class_name_where_a_word_starts(name, expected):
    # An uppercase acronym splits letter by letter, so it never matches a wrongly cased one.
    assert segments(name) == expected


def test_the_checks_would_catch_what_they_are_for():
    assert BRITISH_WORD.search("it honours the key")
    assert not BRITISH_WORD.search("it honors the key")
    assert set(segments("JsonBodyCodec")) & set(ACRONYMS)
    assert set(segments("DeclaredStr")) & set(TRUNCATIONS)
