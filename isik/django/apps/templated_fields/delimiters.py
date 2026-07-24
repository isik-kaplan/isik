"""TemplateDelimiters - which {{ }}/{% %}/{# #} strings a template field's Jinja engine looks for.
Configurable per field, via settings.TEMPLATE_FIELDS_DELIMITERS, or Jinja's own defaults below."""

from dataclasses import asdict, dataclass

from django.conf import settings


@dataclass(frozen=True)
class TemplateDelimiters:
    """Field names match `jinja2.Environment.__init__`'s own kwargs 1:1 - see `resolve_delimiters`
    for how a field picks one of these. E.g. `TemplateDelimiters(variable_start_string="<<",
    variable_end_string=">>")` makes `<<var>>` the placeholder syntax instead of `{{ var }}`."""

    variable_start_string: str = "{{"
    variable_end_string: str = "}}"
    block_start_string: str = "{%"
    block_end_string: str = "%}"
    comment_start_string: str = "{#"
    comment_end_string: str = "#}"

    def as_environment_kwargs(self) -> dict[str, str]:
        return asdict(self)

    def deconstruct(self):
        # Hand-written, not @django.utils.deconstruct.deconstructible - see TemplatePolicy.deconstruct
        # for why (frozen dataclass __setattr__ rejects that decorator's post-init attribute set).
        path = f"{type(self).__module__}.{type(self).__qualname__}"
        return path, [], asdict(self)


def resolve_delimiters(explicit):
    """explicit -> settings.TEMPLATE_FIELDS_DELIMITERS -> Jinja's own {{ }}/{% %}/{# #} default."""
    if explicit is not None:
        return explicit
    configured = getattr(settings, "TEMPLATE_FIELDS_DELIMITERS", None)
    return configured if configured is not None else TemplateDelimiters()
