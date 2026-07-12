import re

import pytest
from hypothesis import given
from hypothesis import strategies as st

from isik.common.utils.strings import camel_to_snake, snake_to_human, snake_to_pascal


class TestCamelToSnake:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("CamelCase", "camel_case"),
            ("camelCase", "camel_case"),
            ("already_snake", "already_snake"),
            ("HTTPResponse", "http_response"),
            ("A", "a"),
        ],
    )
    def test_known_cases(self, name, expected):
        assert camel_to_snake(name) == expected

    @given(st.from_regex(r"\A[A-Za-z][A-Za-z0-9]{0,20}\Z", fullmatch=True))
    def test_output_is_always_lowercase(self, name):
        assert camel_to_snake(name) == camel_to_snake(name).lower()


class TestSnakeToPascal:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("snake_case", "SnakeCase"),
            ("already", "Already"),
            ("a_b_c", "ABC"),
        ],
    )
    def test_known_cases(self, name, expected):
        assert snake_to_pascal(name) == expected

    @given(st.from_regex(r"\A[a-z][a-z_]{0,20}[a-z]\Z", fullmatch=True))
    def test_output_has_no_underscores(self, name):
        assert "_" not in snake_to_pascal(name)


class TestSnakeToHuman:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("snake_case", "Snake Case"),
            ("already", "Already"),
        ],
    )
    def test_known_cases(self, name, expected):
        assert snake_to_human(name) == expected

    @given(st.from_regex(r"\A[a-z][a-z_]{0,20}[a-z]\Z", fullmatch=True))
    def test_output_has_no_underscores(self, name):
        assert "_" not in snake_to_human(name)


def test_camel_to_snake_matches_the_documented_regex_behavior():
    # Sanity check against the pattern the implementation itself is built on.
    name = "HTTPResponseCode"
    step1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    expected = re.sub("([a-z0-9])([A-Z])", r"\1_\2", step1).lower()
    assert camel_to_snake(name) == expected
