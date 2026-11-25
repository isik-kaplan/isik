"""A project with no Django, making an exemption as it loads - what `isik exemptions --import` lists."""

from isik.common.utils.exemptions import Exemption


class NoAudit(Exemption, rule="exempting-project.audit", why="Every export is audited, so a reader can trace it."):
    """Why an export isn't audited."""


NIGHTLY = NoAudit(reason="The nightly export writes only counts, which name no one.")
