import re


def camel_to_snake(name):
    """
    Convert PascalCase and camelCase to snake_case.
    """
    name = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", name).lower()


def snake_to_pascal(name):
    """
    Convert snake_case to PascalCase.
    """
    return "".join(word.capitalize() for word in name.split("_"))


def snake_to_human(name):
    """
    Convert snake_case to Human Readable.
    """
    return name.replace("_", " ").title()
