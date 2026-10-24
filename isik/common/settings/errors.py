from isik.common.config.exceptions import ConfigError


class SettingsError(ConfigError):
    """A value that can't be used - named the way it was given (`--port`, `APP_PORT`), never shown if secret."""


class SettingsAborted(Exception):
    """The person running the command declined to go on - at a soft limit's confirmation, say."""
