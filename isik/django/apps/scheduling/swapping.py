"""
The task state model's swappable setting, and the getters reading it - `get_task_state_model()`. The setting names an
`"app_label.Model"`; unset, it names the model isik ships. A project owning the table subclasses
`AbstractScheduledTaskState` (`bases.py`), tracks its history, and names it in
`ISIK['DJANGO']['SCHEDULING']['TASK_MODEL']`.
"""

from django.apps import apps
from django.conf import settings

from isik.django.conf import give_swappable_settings


TASK_MODEL = "ISIK_SCHEDULING_TASK_MODEL"
# Not a project's to set: the history of isik's model is swapped out with it, so this names isik's own while
# ISIK_SCHEDULING_TASK_MODEL does, and the project's model once it's swapped - whose history is the project's.
TASK_HISTORY_MODEL = "ISIK_SCHEDULING_TASK_HISTORY_MODEL"

DEFAULT = "isik_scheduling.ScheduledTaskState"


def set_defaults():
    """Gives each swappable setting what `ISIK['DJANGO']['SCHEDULING']` names, else its default."""
    give_swappable_settings("SCHEDULING", (TASK_MODEL,))
    if not hasattr(settings, TASK_MODEL):
        setattr(settings, TASK_MODEL, DEFAULT)
    model = getattr(settings, TASK_MODEL)
    setattr(settings, TASK_HISTORY_MODEL, "isik_scheduling.ScheduledTaskStateEvent" if model == DEFAULT else model)


def get_task_state_model():
    """The task state model: `ISIK_SCHEDULING_TASK_MODEL`'s, else isik's."""
    return apps.get_model(getattr(settings, TASK_MODEL))
