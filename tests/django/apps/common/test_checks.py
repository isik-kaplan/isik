"""isik's own system checks."""

from isik.django.apps.common.checks import every_validator_says_whether_it_can_hold_at_the_column
from isik.django.apps.common.db import constraints


def test_a_project_that_answers_for_every_validator_reports_nothing():
    assert every_validator_says_whether_it_can_hold_at_the_column(app_configs=None) == []


def test_an_unanswered_validator_is_an_error_naming_it(monkeypatch):
    monkeypatch.setattr(constraints, "unclassified_validators", lambda: ["app.Thing.note (unanswered)"])

    (error,) = every_validator_says_whether_it_can_hold_at_the_column(app_configs=None)

    assert error.id == "isik.E001"
    assert error.msg == ("Validators do not say whether they can hold at the column: app.Thing.note (unanswered)")
    assert error.hint == (
        "Give the class an `as_condition`, decorate it with `no_database_form(reason)`, or "
        "wrap the instance in `python_only_validator(validator, reason=...)`."
    )


def test_every_unanswered_validator_is_named(monkeypatch):
    """One line per validator, because the first is rarely the only one and a check that names one
    of five is four more runs."""
    monkeypatch.setattr(constraints, "unclassified_validators", lambda: ["a.B.c (one)", "a.B.d (two)"])

    (error,) = every_validator_says_whether_it_can_hold_at_the_column(app_configs=None)

    assert error.msg.endswith("a.B.c (one), a.B.d (two)")
