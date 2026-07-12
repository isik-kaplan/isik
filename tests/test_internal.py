import pytest

from isik._internal import check_extra


def test_check_extra_passes_silently_when_the_package_is_installed():
    check_extra("common", "os")


def test_check_extra_raises_a_helpful_message_when_missing():
    with pytest.raises(ImportError, match=r"pip install isik\[fake_extra\]"):
        check_extra("fake_extra", "this_package_does_not_exist_anywhere")


def test_check_extra_chains_the_original_import_error():
    with pytest.raises(ImportError) as exc_info:
        check_extra("fake_extra", "this_package_does_not_exist_anywhere")
    assert isinstance(exc_info.value.__cause__, ImportError)
