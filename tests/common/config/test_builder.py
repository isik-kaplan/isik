import pytest

from isik.common.config.builder import Config, config
from isik.common.config.casters import boolean, comma_separated_list, integer, string
from isik.common.config.exceptions import ConfigError


def test_builds_a_flat_config_from_environment_variables(monkeypatch):
    monkeypatch.setenv("NAME", "Widget")
    monkeypatch.setenv("COUNT", "3")

    result = config({"NAME": string(), "COUNT": integer()})

    assert result.NAME == "Widget"
    assert result.COUNT == 3


def test_builds_a_nested_config_from_double_underscore_joined_keys(monkeypatch):
    monkeypatch.setenv("DATABASE__HOST", "localhost")
    monkeypatch.setenv("DATABASE__PORT", "5432")

    result = config({"DATABASE": {"HOST": string(), "PORT": string()}})

    assert isinstance(result.DATABASE, Config)
    assert result.DATABASE.HOST == "localhost"
    assert result.DATABASE.PORT == "5432"


def test_prefix_is_joined_before_the_rest_of_the_path(monkeypatch):
    monkeypatch.setenv("APP1__SECRETS__API_KEY", "topsecret")

    result = config({"SECRETS": {"API_KEY": string()}}, prefix="APP1")

    assert result.SECRETS.API_KEY == "topsecret"


def test_custom_separator_is_used_for_the_environment_key(monkeypatch):
    monkeypatch.setenv("A-B-C", "value")

    result = config({"A": {"B": {"C": string()}}}, sep="-")

    assert result.A.B.C == "value"


def test_missing_variable_without_a_default_raises_config_error(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)

    with pytest.raises(ConfigError, match="not found"):
        config({"MISSING_KEY": string()})


def test_missing_variable_uses_missing_default(monkeypatch):
    monkeypatch.delenv("MISSING_KEY", raising=False)

    result = config({"MISSING_KEY": comma_separated_list(missing_default=["a", "b"])})

    assert result.MISSING_KEY == ["a", "b"]


def test_unparseable_value_without_a_default_raises_config_error(monkeypatch):
    monkeypatch.setenv("FLAG", "not-a-bool")

    with pytest.raises(ConfigError, match="Error while parsing"):
        config({"FLAG": boolean()})


def test_unparseable_value_uses_error_default(monkeypatch):
    monkeypatch.setenv("FLAG", "not-a-bool")

    result = config({"FLAG": boolean(error_default=True)})

    assert result.FLAG is True


def test_falsy_missing_default_is_still_used(monkeypatch):
    monkeypatch.delenv("FLAG", raising=False)

    result = config({"FLAG": boolean(missing_default=False)})

    assert result.FLAG is False


def test_value_that_is_neither_a_mapping_nor_callable_raises_config_error():
    with pytest.raises(ConfigError, match="must be callables or other mappings"):
        config({"BAD": "not-callable-or-dict"})


def test_error_message_reports_the_full_nested_path_not_just_the_leaf_key():
    with pytest.raises(ConfigError, match=r"Key=DATABASE\.BAD\."):
        config({"DATABASE": {"BAD": "not-callable-or-dict"}})


def test_config_supports_both_attribute_and_item_access(monkeypatch):
    monkeypatch.setenv("NAME", "Widget")

    result = config({"NAME": string()})

    assert result.NAME == result["NAME"] == "Widget"


class TestMissingAttributeAccess:
    def test_missing_attribute_raises_attribute_error_not_key_error(self):
        result = config({})
        with pytest.raises(AttributeError):
            _ = result.MISSING

    def test_hasattr_returns_false_instead_of_raising(self):
        result = config({})
        assert hasattr(result, "MISSING") is False

    def test_getattr_with_default_returns_the_default(self):
        result = config({})
        assert getattr(result, "MISSING", "fallback") == "fallback"


class TestRefresh:
    def test_refresh_picks_up_a_changed_environment_variable(self, monkeypatch):
        monkeypatch.setenv("NAME", "before")
        result = config({"NAME": string()})
        assert result.NAME == "before"

        monkeypatch.setenv("NAME", "after")
        result.refresh()

        assert result.NAME == "after"

    def test_refresh_returns_self(self, monkeypatch):
        monkeypatch.setenv("NAME", "value")
        result = config({"NAME": string()})
        assert result.refresh() is result

    def test_refreshing_root_updates_a_previously_grabbed_nested_reference(self, monkeypatch):
        monkeypatch.setenv("DATABASE__HOST", "before")
        result = config({"DATABASE": {"HOST": string()}})
        database = result.DATABASE

        monkeypatch.setenv("DATABASE__HOST", "after")
        result.refresh()

        assert database is result.DATABASE
        assert database.HOST == "after"

    def test_refreshing_a_nested_config_only_touches_its_own_subtree(self, monkeypatch):
        monkeypatch.setenv("A__X", "a-before")
        monkeypatch.setenv("B__Y", "b-before")
        result = config({"A": {"X": string()}, "B": {"Y": string()}})

        monkeypatch.setenv("A__X", "a-after")
        monkeypatch.setenv("B__Y", "b-after")
        result.A.refresh()

        assert result.A.X == "a-after"
        assert result.B.Y == "b-before"

    def test_refresh_respects_prefix_and_separator(self, monkeypatch):
        monkeypatch.setenv("APP1-SECRETS-API_KEY", "before")
        result = config({"SECRETS": {"API_KEY": string()}}, prefix="APP1", sep="-")

        monkeypatch.setenv("APP1-SECRETS-API_KEY", "after")
        result.refresh()

        assert result.SECRETS.API_KEY == "after"

    def test_refresh_with_a_path_targets_a_single_leaf_from_the_root(self, monkeypatch):
        monkeypatch.setenv("DATABASE__HOST", "before")
        monkeypatch.setenv("DATABASE__PORT", "before")
        result = config({"DATABASE": {"HOST": string(), "PORT": string()}})

        monkeypatch.setenv("DATABASE__HOST", "after")
        monkeypatch.setenv("DATABASE__PORT", "after")
        result.refresh("DATABASE", "HOST")

        assert result.DATABASE.HOST == "after"
        assert result.DATABASE.PORT == "before"

    def test_refresh_with_a_single_name_targets_a_leaf_from_a_nested_node(self, monkeypatch):
        monkeypatch.setenv("DATABASE__HOST", "before")
        monkeypatch.setenv("DATABASE__PORT", "before")
        result = config({"DATABASE": {"HOST": string(), "PORT": string()}})

        monkeypatch.setenv("DATABASE__HOST", "after")
        monkeypatch.setenv("DATABASE__PORT", "after")
        result.DATABASE.refresh("HOST")

        assert result.DATABASE.HOST == "after"
        assert result.DATABASE.PORT == "before"

    def test_refresh_with_a_path_ending_on_a_subtree_refreshes_the_whole_subtree(self, monkeypatch):
        monkeypatch.setenv("DATABASE__HOST", "before")
        monkeypatch.setenv("DATABASE__PORT", "before")
        result = config({"DATABASE": {"HOST": string(), "PORT": string()}})

        monkeypatch.setenv("DATABASE__HOST", "after")
        monkeypatch.setenv("DATABASE__PORT", "after")
        result.refresh("DATABASE")

        assert result.DATABASE.HOST == "after"
        assert result.DATABASE.PORT == "after"

    def test_refresh_with_an_unknown_key_raises_config_error(self, monkeypatch):
        monkeypatch.setenv("NAME", "value")
        result = config({"NAME": string()})
        with pytest.raises(ConfigError, match="not a key in this config"):
            result.refresh("MISSING")

    def test_refresh_with_an_unknown_nested_key_raises_config_error(self, monkeypatch):
        monkeypatch.setenv("DATABASE__HOST", "value")
        result = config({"DATABASE": {"HOST": string()}})
        with pytest.raises(ConfigError, match="not a key in this config"):
            result.refresh("DATABASE", "MISSING")
