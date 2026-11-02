"""The settings naming isik's own grant and kind histories - swapped out with the models they record."""

import pytest
from django.conf import settings

from isik.django.apps.features import swapping as features
from isik.django.apps.notifications import swapping as notifications


@pytest.mark.parametrize(
    ("module", "model", "history", "own"),
    [
        (features, "GRANT_MODEL", "GRANT_HISTORY_MODEL", "isik_features.FeatureGrantEvent"),
        (notifications, "KIND_POLICY_MODEL", "KIND_POLICY_HISTORY_MODEL", "isik_notifications.NoticeKindPolicyEvent"),
    ],
)
class TestTheHistorySetting:
    def test_isiks_own_while_its_model_is(self, module, model, history, own):
        module.set_defaults()

        assert getattr(settings, getattr(module, history)) == own

    def test_the_projects_model_once_its_swapped(self, settings, module, model, history, own):
        setattr(settings, getattr(module, model), "owned.Thing")

        module.set_defaults()

        assert getattr(settings, getattr(module, history)) == "owned.Thing"
        setattr(settings, getattr(module, model), module.DEFAULTS[getattr(module, model)])
        module.set_defaults()
        assert getattr(settings, getattr(module, history)) == own

    def test_isiks_history_is_swapped_out_with_it(self, settings, module, model, history, own):
        from django.apps import apps

        event = apps.get_model(own)
        assert event._meta.swappable == getattr(module, history)
        assert event._meta.swapped is None
