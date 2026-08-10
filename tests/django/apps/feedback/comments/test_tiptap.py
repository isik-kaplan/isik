import json
import os

import pytest
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import override_settings
from hypothesis import given
from hypothesis import strategies as st

from isik.django.apps.feedback.comments import tiptap


SCHEMA_PATH = settings.FEEDBACK_COMMENTS_TIPTAP_SCHEMA_PATH
MINIMAL_SCHEMA_PATH = os.path.join(os.path.dirname(SCHEMA_PATH), "tiptap_schema_minimal.json")


def doc(*content):
    return {"type": "doc", "content": list(content)}


def paragraph(*content):
    return {"type": "paragraph", "content": list(content)}


def text(value, marks=None):
    node = {"type": "text", "text": value}
    if marks:
        node["marks"] = [{"type": m} for m in marks]
    return node


class TestParseAndCheckSuccessCases:
    def test_a_minimal_valid_document(self):
        tiptap.parse_and_check(doc(paragraph(text("hi"))), schema_path=SCHEMA_PATH)

    def test_every_node_type_in_the_schema_can_be_combined_validly(self):
        value = doc(
            {"type": "heading", "attrs": {"level": 2}, "content": [text("Title")]},
            paragraph(text("hello ", marks=["bold"]), text("world", marks=["italic"])),
            {
                "type": "bulletList",
                "content": [
                    {"type": "listItem", "content": [paragraph(text("one"))]},
                    {"type": "listItem", "content": [paragraph(text("two"))]},
                ],
            },
            paragraph(text("cc "), {"type": "mention", "attrs": {"id": "user:42", "label": "isik"}}),
        )
        node = tiptap.parse_and_check(value, schema_path=SCHEMA_PATH)
        assert "Title" in node.text_content

    def test_hard_break_between_two_text_runs(self):
        value = doc(paragraph(text("line one"), {"type": "hardBreak"}, text("line two")))
        tiptap.parse_and_check(value, schema_path=SCHEMA_PATH)

    def test_a_paragraph_with_no_content_at_all_is_valid(self):
        tiptap.parse_and_check(doc(paragraph()), schema_path=SCHEMA_PATH)


class TestParseAndCheckErrorCases:
    def test_not_a_dict_at_all(self):
        with pytest.raises(ValidationError) as exc_info:
            tiptap.parse_and_check("just a string", schema_path=SCHEMA_PATH)
        assert exc_info.value.messages == ["Tiptap document must be a JSON object."]

    def test_missing_top_level_type(self):
        with pytest.raises(ValidationError) as exc_info:
            tiptap.parse_and_check({"content": []}, schema_path=SCHEMA_PATH)
        assert exc_info.value.messages == ["Tiptap document must have a top-level type of 'doc'."]

    def test_top_level_type_is_not_doc(self):
        with pytest.raises(ValidationError) as exc_info:
            tiptap.parse_and_check(paragraph(text("x")), schema_path=SCHEMA_PATH)
        assert exc_info.value.messages == ["Tiptap document must have a top-level type of 'doc'."]

    def test_unknown_node_type(self):
        value = doc({"type": "table", "content": []})
        with pytest.raises(ValidationError, match="Invalid Tiptap document"):
            tiptap.parse_and_check(value, schema_path=SCHEMA_PATH)

    def test_unknown_mark_type(self):
        value = doc(paragraph(text("x", marks=["underline"])))
        with pytest.raises(ValidationError, match="Invalid Tiptap document"):
            tiptap.parse_and_check(value, schema_path=SCHEMA_PATH)

    def test_mention_missing_its_required_id_attr(self):
        value = doc(paragraph({"type": "mention", "attrs": {}}))
        with pytest.raises(ValidationError, match="Invalid Tiptap document"):
            tiptap.parse_and_check(value, schema_path=SCHEMA_PATH)

    def test_invalid_nesting_a_list_item_may_only_contain_paragraphs(self):
        """Node.from_json alone deserializes invalid nesting without complaint - only .check()
        catches it."""
        heading = {"type": "heading", "attrs": {"level": 1}, "content": []}
        value = doc({"type": "bulletList", "content": [{"type": "listItem", "content": [heading]}]})
        with pytest.raises(ValidationError, match="Invalid Tiptap document"):
            tiptap.parse_and_check(value, schema_path=SCHEMA_PATH)

    def test_empty_doc_content_is_invalid_doc_requires_at_least_one_block(self):
        with pytest.raises(ValidationError, match="Invalid Tiptap document"):
            tiptap.parse_and_check(doc(), schema_path=SCHEMA_PATH)

    def test_content_that_is_not_a_list(self):
        value = {"type": "doc", "content": {"type": "paragraph"}}
        with pytest.raises(ValidationError, match="Invalid Tiptap document"):
            tiptap.parse_and_check(value, schema_path=SCHEMA_PATH)

    def test_no_schema_configured_at_all(self):
        with override_settings(FEEDBACK_COMMENTS_TIPTAP_SCHEMA_PATH=None):
            with pytest.raises(ValidationError) as exc_info:
                tiptap.parse_and_check(doc(paragraph(text("x"))))
        assert exc_info.value.messages == [
            "No Tiptap schema configured - set FEEDBACK_COMMENTS_TIPTAP_SCHEMA_PATH or pass schema_path= explicitly."
        ]

    def test_the_setting_not_being_defined_at_all_is_treated_the_same_as_none(self, settings):
        # getattr(settings, "...", None)'s own default has to be an empty/falsy fallback, not
        # missing entirely - a settings module that never defines this at all (as opposed to
        # defining it as None) must still raise ValidationError, not crash with AttributeError.
        del settings.FEEDBACK_COMMENTS_TIPTAP_SCHEMA_PATH
        with pytest.raises(ValidationError, match="No Tiptap schema configured"):
            tiptap.parse_and_check(doc(paragraph(text("x"))))

    def test_schema_file_does_not_exist(self):
        with pytest.raises(FileNotFoundError):
            tiptap.parse_and_check(doc(paragraph(text("x"))), schema_path="/no/such/file.json")


class TestSchemaPathResolution:
    def test_falls_back_to_the_django_setting_when_no_explicit_path_is_given(self):
        with override_settings(FEEDBACK_COMMENTS_TIPTAP_SCHEMA_PATH=SCHEMA_PATH):
            tiptap._load_schema.cache_clear()
            tiptap.parse_and_check(doc(paragraph(text("hi"))))
        tiptap._load_schema.cache_clear()

    def test_a_per_call_schema_path_overrides_the_setting(self):
        """Article uses tiptap for real; the minimal schema doesn't know about `mention`, so a
        document valid under the full schema fails validation under the narrower one."""
        value = doc(paragraph(text("cc "), {"type": "mention", "attrs": {"id": "user:1"}}))
        tiptap.parse_and_check(value, schema_path=SCHEMA_PATH)
        with pytest.raises(ValidationError, match="Invalid Tiptap document"):
            tiptap.parse_and_check(value, schema_path=MINIMAL_SCHEMA_PATH)


class TestGetMentions:
    def test_finds_a_single_mention(self):
        value = doc(paragraph(text("hi "), {"type": "mention", "attrs": {"id": "user:1"}}))
        assert tiptap.get_mentions(value) == ["user:1"]

    def test_finds_multiple_mentions_across_nested_content(self):
        value = doc(
            paragraph({"type": "mention", "attrs": {"id": "user:1"}}),
            {
                "type": "bulletList",
                "content": [
                    {"type": "listItem", "content": [paragraph({"type": "mention", "attrs": {"id": "user:2"}})]},
                ],
            },
        )
        assert tiptap.get_mentions(value) == ["user:1", "user:2"]

    def test_no_mentions_returns_an_empty_list(self):
        assert tiptap.get_mentions(doc(paragraph(text("no mentions here")))) == []

    def test_does_not_require_the_document_to_be_otherwise_valid(self):
        value = doc({"type": "not-a-real-node", "content": [{"type": "mention", "attrs": {"id": "user:9"}}]})
        assert tiptap.get_mentions(value) == ["user:9"]

    def test_a_non_dict_child_in_content_is_skipped_instead_of_raising(self):
        value = doc(paragraph("not a node", {"type": "mention", "attrs": {"id": "user:1"}}))
        assert tiptap.get_mentions(value) == ["user:1"]

    def test_a_mention_node_with_no_attrs_key_at_all_is_skipped_instead_of_raising(self):
        value = doc(paragraph({"type": "mention"}, {"type": "mention", "attrs": {"id": "user:1"}}))
        assert tiptap.get_mentions(value) == ["user:1"]

    def test_a_mention_missing_its_id_attr_contributes_nothing(self):
        value = doc(paragraph({"type": "mention", "attrs": {}}, {"type": "mention", "attrs": {"id": "user:1"}}))
        assert tiptap.get_mentions(value) == ["user:1"]


class TestTiptapValidator:
    def test_raises_below_min_length(self):
        validator = tiptap.TiptapValidator(min_length=10, schema_path=SCHEMA_PATH)
        with pytest.raises(ValidationError, match="at least 10"):
            validator(doc(paragraph(text("short"))))

    def test_raises_above_max_length(self):
        validator = tiptap.TiptapValidator(max_length=3, schema_path=SCHEMA_PATH)
        with pytest.raises(ValidationError, match="at most 3"):
            validator(doc(paragraph(text("this is too long"))))

    def test_passes_within_bounds(self):
        validator = tiptap.TiptapValidator(min_length=1, max_length=100, schema_path=SCHEMA_PATH)
        validator(doc(paragraph(text("just right"))))

    def test_boundary_lengths_are_inclusive(self):
        validator = tiptap.TiptapValidator(min_length=5, max_length=5, schema_path=SCHEMA_PATH)
        validator(doc(paragraph(text("12345"))))

    def test_is_deconstructible_for_migrations(self):
        validator = tiptap.TiptapValidator(min_length=1, max_length=10, schema_path=SCHEMA_PATH)
        path, args, kwargs = validator.deconstruct()
        assert kwargs == {"min_length": 1, "max_length": 10, "schema_path": SCHEMA_PATH}

    def test_equality_is_based_on_config_not_identity(self):
        a = tiptap.TiptapValidator(min_length=1, max_length=10, schema_path=SCHEMA_PATH)
        b = tiptap.TiptapValidator(min_length=1, max_length=10, schema_path=SCHEMA_PATH)
        c = tiptap.TiptapValidator(min_length=2, max_length=10, schema_path=SCHEMA_PATH)
        assert a == b
        assert a != c

    def test_is_never_equal_to_an_unrelated_type(self):
        validator = tiptap.TiptapValidator(min_length=1, max_length=10, schema_path=SCHEMA_PATH)
        assert validator != "not a validator"
        assert validator != object()


@st.composite
def _paragraph_text_docs(draw):
    # ProseMirror rejects empty text nodes outright (not a schema rule, a hard model invariant),
    # so non-empty text is what's meaningful to round-trip here.
    body = draw(
        st.text(alphabet=st.characters(blacklist_categories=("Cs",), max_codepoint=0x2FFF), min_size=1, max_size=50)
    )
    return doc(paragraph(text(body))), body


class TestPropertyBased:
    @given(_paragraph_text_docs())
    def test_any_single_paragraph_of_plain_text_is_valid_and_round_trips_through_text_content(self, generated):
        value, body = generated
        node = tiptap.parse_and_check(value, schema_path=SCHEMA_PATH)
        assert node.text_content == body

    @given(st.lists(st.text(min_size=1, max_size=10), min_size=0, max_size=5))
    def test_get_mentions_always_returns_exactly_the_ids_present(self, ids):
        value = doc(paragraph(*[{"type": "mention", "attrs": {"id": i}} for i in ids]))
        assert tiptap.get_mentions(value) == ids

    @given(st.integers(min_value=2, max_value=200))
    def test_min_length_boundary_is_exact(self, length):
        validator = tiptap.TiptapValidator(min_length=length, schema_path=SCHEMA_PATH)
        body = "a" * length
        validator(doc(paragraph(text(body))))  # exactly at the boundary: must pass
        with pytest.raises(ValidationError):
            validator(doc(paragraph(text(body[:-1]))))  # one short: must fail


def test_fixture_schema_files_are_valid_json():
    for path in (SCHEMA_PATH, MINIMAL_SCHEMA_PATH):
        with open(path) as f:
            json.load(f)
