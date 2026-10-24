import pytest

from isik.common.config.environment import read_file, read_variable
from isik.common.config.exceptions import ConfigError


def test_an_unset_variable_is_none():
    assert read_variable({}, "TOKEN") is None


def test_a_set_variable_is_its_text():
    assert read_variable({"TOKEN": "abc"}, "TOKEN") == ("abc", False)


def test_the_file_variable_reads_the_file_it_names(tmp_path):
    secret = tmp_path / "token"
    secret.write_text("abc\n")

    assert read_variable({"TOKEN_FILE": str(secret)}, "TOKEN") == ("abc", True)


def test_without_files_the_file_variable_is_ignored(tmp_path):
    assert read_variable({"TOKEN_FILE": str(tmp_path / "token")}, "TOKEN", files=False) is None


def test_both_set_is_refused_rather_than_one_winning(tmp_path):
    with pytest.raises(ConfigError, match=r"^TOKEN and TOKEN_FILE are both set - set only one of them\.$"):
        read_variable({"TOKEN": "a", "TOKEN_FILE": str(tmp_path)}, "TOKEN")


def test_both_set_is_fine_without_files(tmp_path):
    assert read_variable({"TOKEN": "a", "TOKEN_FILE": "x"}, "TOKEN", files=False) == ("a", False)


@pytest.mark.parametrize(
    ("text", "value"),
    [("abc\r\n", "abc"), ("abc\n\n", "abc\n"), ("a\rb", "a\rb"), ("a b", "a b"), ("", "")],
    # Plain ids: a line break in a test's id is more than some runners can select it by.
    ids=["crlf", "two-newlines", "inner-carriage-return", "none", "empty"],
)
def test_only_the_final_line_break_is_dropped(tmp_path, text, value):
    path = tmp_path / "value"
    path.write_bytes(text.encode())

    assert read_file(path, name="X_FILE") == value


def test_an_unreadable_file_names_the_variable(tmp_path):
    with pytest.raises(ConfigError, match=r"^X_FILE names .*missing, which can't be read: No such file"):
        read_file(tmp_path / "missing", name="X_FILE")
