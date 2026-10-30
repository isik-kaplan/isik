from isik.django.apps.notifications.swapping import get_kind_policy_model


def kind_named(name):
    """The policy row of the kind called `name` - what a row keying a kind is given."""
    return get_kind_policy_model().objects.get(name=name)
