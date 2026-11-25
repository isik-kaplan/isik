"""
The scheduled tasks over REST - `ScheduledTaskViewSet`, which a project routes:

    router.register("scheduled-tasks", ScheduledTaskViewSet, basename="scheduled-task")

    GET   scheduled-tasks/                       every task staff may view
    GET   scheduled-tasks/{name}/
    PATCH scheduled-tasks/{name}/                {"every": seconds} | {"crontab": "m h dom mon dow"},
                                                 {"on_restart": "code" | "database"}; If-Match the task's ETag
    POST  scheduled-tasks/{name}/pause/          and resume/, run/, reset/, reset-at-restart/

Each task answers with what its declaration says (`code`), what staff saved (`saved`), what the scheduler runs it as
(`effective`), and what staff may do to it (`may`). Something its declaration doesn't allow is a 403,
`not_allowed_by_declaration`, saying what it does allow. Who counts as staff is `permission_classes` - Django's
`is_staff` (DRF's `IsAdminUser`) unless the project sets its own.
"""

from datetime import timedelta

from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from isik._internal.translation import gettext as _
from isik._internal.translation import gettext_lazy
from isik.common.utils.exemptions import makes_own_exemptions
from isik.django.apps.idempotency.drf import IdempotencyMixin
from isik.django.apps.scheduling import state
from isik.django.apps.scheduling.swapping import get_task_state_model
from isik.django.drf.error_handling import refusal
from isik.django.drf.viewsets.base import BaseViewSet
from isik.django.drf.viewsets.preconditions import NoPrecondition, PreconditionMixin
from isik.django.drf.viewsets.schema_generation import none_during_schema_generation
from isik.django.drf.viewsets.swapped import SwappedModel
from isik.django.scheduling import Source, StaffMay, declared_tasks


@makes_own_exemptions
def _own_exemptions():
    # isik's own, recorded at this line and left out of a project's listing and budgets.
    return {
        "state": NoPrecondition(
            reason="sets the task to the state it names - paused, resumed, reset - so two staff racing agree"
        ),
        "run": NoPrecondition(reason="sends the task, changing nothing staff set - when it last ran is only a stamp"),
    }


_EXEMPT = _own_exemptions()


def _refused(message):
    """A 403 saying what the task's declaration allows."""
    return refusal(str(message), code=state.NotAllowed.code, status=403)


def _seconds(every):
    return None if every is None else int(every.total_seconds())


class StateSerializer(serializers.Serializer):
    every = serializers.IntegerField(
        allow_null=True, help_text=gettext_lazy("Seconds between runs - null at a crontab.")
    )
    crontab = serializers.CharField(allow_null=True, help_text=gettext_lazy("Five fields - null every so often."))
    paused = serializers.BooleanField()
    on_restart = serializers.ChoiceField(choices=[source.value for source in Source], allow_null=True)

    def to_representation(self, instance):
        return {
            "every": _seconds(instance.every),
            "crontab": instance.crontab,
            "paused": instance.paused,
            "on_restart": instance.on_restart.value if isinstance(instance.on_restart, Source) else instance.on_restart,
        }


class ScheduledTaskSerializer(serializers.Serializer):
    name = serializers.CharField(read_only=True)
    task = serializers.CharField(read_only=True, help_text=gettext_lazy("The Celery task it sends."))
    code = StateSerializer(read_only=True, help_text=gettext_lazy("What its declaration says - what reset/ sets back."))
    saved = StateSerializer(
        read_only=True, help_text=gettext_lazy("What staff saved - on_restart null for the declaration's.")
    )
    effective = StateSerializer(read_only=True, help_text=gettext_lazy("What the scheduler runs it as."))
    may = serializers.ListField(
        child=serializers.CharField(), read_only=True, help_text=gettext_lazy("What staff may do to it.")
    )
    run_by_hand_at = serializers.DateTimeField(read_only=True, allow_null=True)
    last_run_at = serializers.DateTimeField(
        read_only=True, allow_null=True, help_text=gettext_lazy("When beat last sent it on its schedule.")
    )

    def to_representation(self, row):
        task = declared_tasks()[row.name]
        return super().to_representation(
            {
                "name": row.name,
                "task": task.task,
                "code": state.declared_state(task),
                "saved": row,
                "effective": state.effective_state(task, row),
                "may": state.may(task),
                "run_by_hand_at": row.run_by_hand_at,
                "last_run_at": row.last_run_at,
            }
        )


class ChangeSerializer(serializers.Serializer):
    every = serializers.IntegerField(min_value=1, required=False, help_text=gettext_lazy("Seconds between runs."))
    crontab = serializers.CharField(
        required=False, help_text=gettext_lazy("Five fields: minute hour day month weekday.")
    )
    on_restart = serializers.ChoiceField(choices=[source.value for source in Source], required=False)

    def validate(self, data):
        if "every" in data and "crontab" in data:
            raise ValidationError(_("Give every or crontab, not both."))
        return data


class ScheduledTaskViewSet(IdempotencyMixin, PreconditionMixin, BaseViewSet, mixins.ListModelMixin):
    """The scheduled tasks staff may view - each with what staff may do to it."""

    model = SwappedModel(get_task_state_model)
    permission_classes = [IsAdminUser]
    serializer_class = ScheduledTaskSerializer
    lookup_field = "name"
    lookup_value_regex = r"[^/]+"
    precondition_exempt_actions = {
        "pause": _EXEMPT["state"],
        "resume": _EXEMPT["state"],
        "reset": _EXEMPT["state"],
        "reset_at_restart": _EXEMPT["state"],
        "run": _EXEMPT["run"],
    }

    @none_during_schema_generation
    def get_queryset(self):
        viewable = [name for name, task in declared_tasks().items() if StaffMay.VIEW in task.staff_may]
        return get_task_state_model()._default_manager.filter(name__in=viewable).order_by("name")

    def get_object(self):
        name = self.kwargs[self.lookup_field]
        try:
            task = state.task_of(name)
        except state.UnknownTask:
            return super().get_object()
        if StaffMay.VIEW not in task.staff_may:
            raise _refused(state._refused(task, _("view")))
        return super().get_object()

    def _changed(self, *changes):
        """Makes each of `changes` - (a function of `state`, its arguments) - to the task, all of them or none."""
        row = self.get_object()
        try:
            with transaction.atomic():
                for change, args, kwargs in changes:
                    row = change(row.name, *args, **kwargs)
        except state.NotAllowed as refused:
            raise _refused(refused) from None
        except ValueError as invalid:
            raise ValidationError({"crontab": [str(invalid)]}) from None
        return Response(self.get_serializer(row).data)

    def retrieve(self, request, *args, **kwargs):
        """One task."""
        return Response(self.get_serializer(self.get_object()).data)

    @extend_schema(request=ChangeSerializer)
    def partial_update(self, request, *args, **kwargs):
        """Reschedules the task - `every` or `crontab` - and switches its restart mode - `on_restart`."""
        given = ChangeSerializer(data=request.data)
        given.is_valid(raise_exception=True)
        data, changes = given.validated_data, []
        if "every" in data:
            changes.append((state.reschedule, (), {"every": timedelta(seconds=data["every"])}))
        if "crontab" in data:
            changes.append((state.reschedule, (), {"crontab": data["crontab"]}))
        if "on_restart" in data:
            changes.append((state.set_on_restart, (Source(data["on_restart"]),), {}))
        return self._changed(*changes)

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def pause(self, request, *args, **kwargs):
        """Pauses the task."""
        return self._changed((state.pause, (), {}))

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def resume(self, request, *args, **kwargs):
        """Resumes the task."""
        return self._changed((state.resume, (), {}))

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def run(self, request, *args, **kwargs):
        """Sends the task now, as beat sends it."""
        return self._changed((state.run_now, (), {}))

    @extend_schema(request=None)
    @action(detail=True, methods=["post"])
    def reset(self, request, *args, **kwargs):
        """Sets the task's schedule and pause back to its declaration's now - as far as staff may change them."""
        return self._changed((state.reset, (), {}))

    @extend_schema(request=None)
    @action(detail=True, methods=["post"], url_path="reset-at-restart")
    def reset_at_restart(self, request, *args, **kwargs):
        """Has the next scheduler start set the task back to its declaration's - its restart mode code."""
        return self._changed((state.reset_at_restart, (), {}))
