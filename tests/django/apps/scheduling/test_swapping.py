"""The task state model's swappable setting - isik's model unless the project names its own."""

import pytest

from isik.django.apps.scheduling import swapping


@pytest.fixture(autouse=True)
def restored(settings):
    """set_defaults() writes Django's settings itself - set through the fixture first, each is put back after."""
    settings.ISIK_SCHEDULING_TASK_MODEL = settings.ISIK_SCHEDULING_TASK_MODEL
    settings.ISIK_SCHEDULING_TASK_HISTORY_MODEL = settings.ISIK_SCHEDULING_TASK_HISTORY_MODEL


def test_isiks_own_and_its_history(settings):
    del settings.ISIK_SCHEDULING_TASK_MODEL

    swapping.set_defaults()

    assert settings.ISIK_SCHEDULING_TASK_MODEL == "isik_scheduling.ScheduledTaskState"
    assert settings.ISIK_SCHEDULING_TASK_HISTORY_MODEL == "isik_scheduling.ScheduledTaskStateEvent"


def test_a_projects_own_keeps_its_history_too(settings):
    settings.ISIK_SCHEDULING_TASK_MODEL = "ops.ScheduledTaskState"

    swapping.set_defaults()

    assert settings.ISIK_SCHEDULING_TASK_MODEL == "ops.ScheduledTaskState"
    assert settings.ISIK_SCHEDULING_TASK_HISTORY_MODEL == "ops.ScheduledTaskState"


def test_named_in_isik(settings):
    del settings.ISIK_SCHEDULING_TASK_MODEL
    settings.ISIK = {**settings.ISIK, "DJANGO": {**settings.ISIK["DJANGO"], "SCHEDULING": {"TASK_MODEL": "ops.Mine"}}}

    swapping.set_defaults()

    assert settings.ISIK_SCHEDULING_TASK_MODEL == "ops.Mine"
