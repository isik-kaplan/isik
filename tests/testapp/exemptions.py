"""A project's own exemption types, one made each way - used in testapp's models, so its migrations hold them."""

from isik.common.utils.exemptions import Exemption, exemption_class


NoHelpText = exemption_class(
    "NoHelpText",
    rule="testapp.help-text",
    why="Every field should say what it holds in help_text.",
    shows_as="",
)


class NoComment(Exemption, rule="testapp.db-comment", why="Every column should say what it holds.", shows_as=""):
    pass
