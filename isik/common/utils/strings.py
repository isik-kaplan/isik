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


def words_to_pascal(text):
    """
    Convert any run of words to PascalCase, splitting on every non-alphanumeric character and
    keeping each word's own inner capitals - so it also takes names that are already PascalCase,
    which `snake_to_pascal` would flatten ("IsSuperUser" -> "Issuperuser").

        words_to_pascal("issued_by")             # "IssuedBy"
        words_to_pascal("profile.organization")  # "ProfileOrganization"
        words_to_pascal("IsSuperUser")           # "IsSuperUser"
        words_to_pascal("<lambda>")              # "Lambda"
    """
    return "".join(word[:1].upper() + word[1:] for word in re.split(r"[^0-9A-Za-z]+", text))


def snake_to_human(name):
    """
    Convert snake_case to Human Readable.
    """
    return name.replace("_", " ").title()
