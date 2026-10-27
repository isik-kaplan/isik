"""isik's own system checks."""

import pytest
from django.apps import apps
from django.core.checks import Error
from django.core.checks import Warning as CheckWarning

from isik.common.utils.exemptions import Exemption
from isik.django.apps.common import checks, exemptions
from isik.django.apps.common.atomic import NotAtomic
from isik.django.apps.common.checks import (
    Check,
    CheckOption,
    Severity,
    every_validator_says_whether_it_can_hold_at_the_column,
)
from isik.django.apps.common.db import conditions, constraints, documentation
from isik.django.apps.common.db import history as db_history
from isik.django.apps.common.project import project_app_configs
from isik.django.drf.serializers import flattened_singleton_related
from isik.django.drf.viewsets import ViewSetRegistryExemption, preconditions


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


URLS = "tests.django.apps.common.check_urls"


class TestTheSettings:
    def test_every_check_is_on_by_default(self, settings):
        settings.ISIK_CHECKS = None

        assert checks.check_options(Check.IDEMPOTENCY) == {
            CheckOption.ENABLED: True,
            CheckOption.SEVERITY: None,
            CheckOption.URLCONFS: None,
            CheckOption.APPS: None,
            CheckOption.EXEMPTION_TYPES: None,
            CheckOption.INHERITED_FROM_LIBRARIES: True,
        }

    def test_a_checks_entry_overrides_the_defaults(self, settings):
        settings.ISIK_CHECKS = {Check.IDEMPOTENCY: {CheckOption.URLCONFS: [URLS]}}

        assert checks.check_options(Check.IDEMPOTENCY)[CheckOption.URLCONFS] == [URLS]
        assert checks.check_options(Check.IDEMPOTENCY)[CheckOption.ENABLED] is True

    def test_the_defaults_are_never_handed_out_to_be_changed(self, settings):
        settings.ISIK_CHECKS = None

        checks.check_options(Check.IDEMPOTENCY)[CheckOption.ENABLED] = False

        assert checks.check_options(Check.IDEMPOTENCY)[CheckOption.ENABLED] is True

    def test_an_entry_that_isnt_a_dict_is_ignored_here_and_reported(self, settings):
        settings.ISIK_CHECKS = {Check.IDEMPOTENCY: False}

        assert checks.check_options(Check.IDEMPOTENCY)[CheckOption.ENABLED] is True

    def test_a_string_key_is_ignored_here_and_reported(self, settings):
        settings.ISIK_CHECKS = {"idempotency": {CheckOption.ENABLED: False}}

        assert checks.check_options(Check.IDEMPOTENCY)[CheckOption.ENABLED] is True

    def test_turned_off_it_reports_nothing(self, settings, monkeypatch):
        monkeypatch.setattr(constraints, "unclassified_validators", lambda: ["a.B.c (one)"])
        settings.ISIK_CHECKS = {Check.VALIDATORS: {CheckOption.ENABLED: False}}

        assert every_validator_says_whether_it_can_hold_at_the_column(app_configs=None) == []

    def test_a_severity_changes_the_messages_kind_and_keeps_the_rest(self, settings, monkeypatch):
        monkeypatch.setattr(constraints, "unclassified_validators", lambda: ["a.B.c (one)"])
        settings.ISIK_CHECKS = {Check.VALIDATORS: {CheckOption.SEVERITY: Severity.WARNING}}

        (message,) = every_validator_says_whether_it_can_hold_at_the_column(app_configs=None)

        assert type(message) is CheckWarning
        assert (message.id, message.msg) == (
            "isik.E001",
            "Validators do not say whether they can hold at the column: a.B.c (one)",
        )
        assert message.hint.startswith("Give the class")

    def test_an_error_severity_makes_a_warning_an_error(self, settings, monkeypatch):
        settings.ISIK_CHECKS = {Check.PRECONDITION_OPTIONS: {CheckOption.SEVERITY: Severity.ERROR}}
        monkeypatch.setattr(preconditions, "precondition_option_problems", lambda urlconf: [])
        monkeypatch.setattr(preconditions, "precondition_option_warnings", lambda urlconf: ["undone"])

        (message,) = checks.every_precondition_view_has_options_that_make_sense(app_configs=None)

        assert (type(message), message.id) == (Error, "isik.W001")

    def test_a_severity_written_as_a_string_leaves_the_checks_own(self, settings, monkeypatch):
        monkeypatch.setattr(constraints, "unclassified_validators", lambda: ["a.B.c (one)"])
        settings.ISIK_CHECKS = {Check.VALIDATORS: {CheckOption.SEVERITY: "warning"}}

        (message,) = every_validator_says_whether_it_can_hold_at_the_column(app_configs=None)

        assert type(message) is Error

    def test_the_enums_values(self):
        assert [check.value for check in Check] == [
            "validators",
            "concurrency_tokens",
            "precondition_options",
            "relation_locks",
            "version_reads",
            "idempotency",
            "singletons",
            "preconditions",
            "history_indexes",
            "exemptions",
            "documentation",
            "not_atomic",
            "kept_field_conditions",
        ]
        assert [option.value for option in CheckOption] == [
            "enabled",
            "severity",
            "urlconfs",
            "apps",
            "exemption_types",
            "inherited_from_libraries",
        ]
        assert [severity.value for severity in Severity] == ["error", "warning"]

    def test_a_string_is_no_member(self):
        assert "idempotency" != Check.IDEMPOTENCY
        assert "warning" != Severity.WARNING


class TestTheSettingsCheck:
    HINT = "See isik.django.apps.common.checks."

    def messages(self):
        return [(type(message), message.id, message.msg) for message in checks.isik_checks_are_configured(None)]

    def errors(self):
        found = checks.isik_checks_are_configured(None)
        assert {message.hint for message in found if message.id == "isik.E012"} <= {self.HINT}
        return [message.msg for message in found if message.id == "isik.E012"]

    def test_none_is_fine(self, settings):
        settings.ISIK_CHECKS = None

        assert self.messages() == []

    def test_every_option_of_every_check_is_fine(self, settings):
        settings.ROOT_URLCONF = URLS
        settings.ISIK_CHECKS = {
            Check.IDEMPOTENCY: {
                CheckOption.ENABLED: True,
                CheckOption.SEVERITY: Severity.WARNING,
                CheckOption.URLCONFS: [URLS],
            },
            Check.DOCUMENTATION: {CheckOption.APPS: ["testapp"], CheckOption.EXEMPTION_TYPES: []},
        }

        assert self.messages() == []

    def test_not_a_dict(self, settings):
        settings.ISIK_CHECKS = [Check.IDEMPOTENCY]

        assert self.messages() == [
            (Error, "isik.E012", "ISIK_CHECKS is [<Check.IDEMPOTENCY: 'idempotency'>], not a dict.")
        ]

    @pytest.mark.parametrize("written", ["idempotency", "IDEMPOTENCY"])
    def test_a_check_named_by_a_string(self, settings, written):
        settings.ISIK_CHECKS = {written: {}}

        assert self.errors() == [f"ISIK_CHECKS names {written!r} as a string - write Check.IDEMPOTENCY."]

    def test_something_that_isnt_a_check(self, settings):
        settings.ISIK_CHECKS = {"idempotence": {}}

        assert self.errors() == [
            "ISIK_CHECKS names 'idempotence', which isn't a Check: Check.VALIDATORS, Check.CONCURRENCY_TOKENS, "
            "Check.PRECONDITION_OPTIONS, Check.RELATION_LOCKS, Check.VERSION_READS, Check.IDEMPOTENCY, "
            "Check.SINGLETONS, Check.PRECONDITIONS, Check.HISTORY_INDEXES, Check.EXEMPTIONS, Check.DOCUMENTATION, "
            "Check.NOT_ATOMIC, Check.KEPT_FIELD_CONDITIONS."
        ]

    def test_an_entry_not_a_dict(self, settings):
        settings.ISIK_CHECKS = {Check.IDEMPOTENCY: False}

        assert self.errors() == ["ISIK_CHECKS[Check.IDEMPOTENCY] is False, not a dict."]

    def test_an_option_named_by_a_string(self, settings):
        settings.ISIK_CHECKS = {Check.IDEMPOTENCY: {"urlconfs": [URLS], 3: True}}

        assert self.errors() == [
            "ISIK_CHECKS[Check.IDEMPOTENCY] names 'urlconfs' as a string - write CheckOption.URLCONFS.",
            "ISIK_CHECKS[Check.IDEMPOTENCY] names 3, which isn't a CheckOption: CheckOption.ENABLED, "
            "CheckOption.SEVERITY, CheckOption.URLCONFS, CheckOption.APPS, CheckOption.EXEMPTION_TYPES, "
            "CheckOption.INHERITED_FROM_LIBRARIES.",
        ]

    def test_an_option_the_check_doesnt_take(self, settings):
        settings.ISIK_CHECKS = {
            Check.VALIDATORS: {CheckOption.URLCONFS: [URLS], CheckOption.APPS: []},
            Check.IDEMPOTENCY: {CheckOption.APPS: []},
        }

        assert self.errors() == [
            "ISIK_CHECKS[Check.VALIDATORS] has CheckOption.URLCONFS - it takes CheckOption.ENABLED, "
            "CheckOption.SEVERITY.",
            "ISIK_CHECKS[Check.VALIDATORS] has CheckOption.APPS - it takes CheckOption.ENABLED, CheckOption.SEVERITY.",
            "ISIK_CHECKS[Check.IDEMPOTENCY] has CheckOption.APPS - it takes CheckOption.ENABLED, "
            "CheckOption.SEVERITY, CheckOption.URLCONFS.",
        ]

    @pytest.mark.parametrize("written", ["warning", "WARNING"])
    def test_a_severity_written_as_a_string(self, settings, written):
        settings.ISIK_CHECKS = {Check.VALIDATORS: {CheckOption.SEVERITY: written}}

        assert self.errors() == [f"ISIK_CHECKS[Check.VALIDATORS]'s severity is {written!r} - write Severity.WARNING."]

    def test_a_severity_there_isnt(self, settings):
        settings.ISIK_CHECKS = {Check.VALIDATORS: {CheckOption.SEVERITY: "loud"}}

        assert self.errors() == [
            "ISIK_CHECKS[Check.VALIDATORS]'s severity is 'loud' - write Severity.ERROR, Severity.WARNING."
        ]

    def test_a_severity_of_another_type(self, settings):
        settings.ISIK_CHECKS = {Check.VALIDATORS: {CheckOption.SEVERITY: 2}}

        assert self.errors() == [
            "ISIK_CHECKS[Check.VALIDATORS]'s severity is 2 - write Severity.ERROR, Severity.WARNING."
        ]

    def test_urlconfs_leaving_a_served_one_out_is_a_warning(self, settings):
        settings.ROOT_URLCONF = URLS
        settings.ISIK_CHECKS = {Check.IDEMPOTENCY: {CheckOption.URLCONFS: ["tests.django.drf.preconditions.urls"]}}

        ((kind, id, msg),) = self.messages()

        assert (kind, id) == (CheckWarning, "isik.W003")
        assert msg == f"ISIK_CHECKS[Check.IDEMPOTENCY] leaves served urlconfs unchecked: {URLS}."
        assert checks.isik_checks_are_configured(None)[0].hint == (
            "Leave out CheckOption.URLCONFS to check every urlconf the project serves."
        )

    def test_a_check_turned_off_leaves_nothing_unchecked(self, settings):
        settings.ROOT_URLCONF = URLS
        settings.ISIK_CHECKS = {
            Check.IDEMPOTENCY: {
                CheckOption.ENABLED: False,
                CheckOption.URLCONFS: ["tests.django.drf.preconditions.urls"],
            }
        }

        assert self.messages() == []

    def test_errors_come_before_warnings(self, settings):
        settings.ROOT_URLCONF = URLS
        settings.ISIK_CHECKS = {
            Check.IDEMPOTENCY: {CheckOption.URLCONFS: ["tests.django.drf.preconditions.urls"]},
            Check.VALIDATORS: {CheckOption.SEVERITY: "loud"},
        }

        assert [id for _kind, id, _msg in self.messages()] == ["isik.E012", "isik.W003"]


class TestTheCoverageChecks:
    @pytest.fixture(autouse=True)
    def routed(self, settings):
        settings.ROOT_URLCONF = URLS

    def test_each_write_not_honoring_an_idempotency_key_once(self):
        errors = checks.every_routed_write_honors_an_idempotency_key(app_configs=None)

        assert [(error.id, error.msg) for error in errors] == [
            ("isik.E005", "UnguardedViewSet.create answers POST without honoring an Idempotency-Key."),
            ("isik.E005", "UnguardedViewSet.update answers PUT without honoring an Idempotency-Key."),
            ("isik.E005", "UnguardedViewSet.partial_update answers PATCH without honoring an Idempotency-Key."),
            ("isik.E005", "UnguardedViewSet.destroy answers DELETE without honoring an Idempotency-Key."),
            ("isik.E005", "PlainCreateView answers POST without honoring an Idempotency-Key."),
        ]
        assert errors[0].hint == (
            "Add IdempotencyMixin (BaseModelViewSet has it) with the method in idempotent_methods, or exempt the "
            "action in idempotency_exempt_actions."
        )

    def test_each_write_to_one_object_not_asking_if_match_once(self):
        errors = checks.every_routed_write_to_one_object_asks_if_match(app_configs=None)

        assert [(error.id, error.msg) for error in errors] == [
            ("isik.E007", "UnguardedViewSet.update answers PUT without asking If-Match."),
            ("isik.E007", "UnguardedViewSet.partial_update answers PATCH without asking If-Match."),
            ("isik.E007", "UnguardedViewSet.destroy answers DELETE without asking If-Match."),
        ]
        assert errors[0].hint == (
            "Add PreconditionMixin (BaseModelViewSet has it) with the method in precondition_methods, or exempt "
            "the action in precondition_exempt_actions."
        )

    def test_an_api_view_is_named_by_its_class(self, settings):
        settings.ROOT_URLCONF = "tests.django.apps.idempotency.urls"

        messages = [error.msg for error in checks.every_routed_write_to_one_object_asks_if_match(app_configs=None)]

        assert "CreateWidgetView answers" not in " ".join(messages)
        assert any(message.startswith("WidgetViewSet.update") for message in messages)

    def test_each_view_outside_the_transaction_without_a_reason(self):
        (error,) = checks.every_view_outside_the_transaction_says_why(app_configs=None)

        assert error.id == "isik.E011"
        assert error.msg == (
            "leaves_silently (leaves-silently/) is served outside a transaction without saying why - decorate it "
            "with not_atomic(reason) instead of transaction.non_atomic_requests."
        )
        assert error.hint == "See not_atomic() (isik.django.apps.common.atomic)."

    def test_told_to_check_another_urlconf_it_checks_that(self, settings):
        elsewhere = {CheckOption.URLCONFS: ["tests.django.drf.preconditions.urls"]}
        settings.ISIK_CHECKS = {
            Check.IDEMPOTENCY: elsewhere,
            Check.PRECONDITIONS: elsewhere,
            Check.NOT_ATOMIC: elsewhere,
        }

        assert not any("UnguardedViewSet" in error.msg for error in self.view_errors())
        assert checks.every_view_outside_the_transaction_says_why(app_configs=None) == []

    def view_errors(self):
        return [
            *checks.every_routed_write_honors_an_idempotency_key(app_configs=None),
            *checks.every_routed_write_to_one_object_asks_if_match(app_configs=None),
        ]

    @pytest.mark.parametrize(
        "check",
        [
            "every_routed_write_honors_an_idempotency_key",
            "every_routed_write_to_one_object_asks_if_match",
            "every_view_outside_the_transaction_says_why",
            "every_flattened_singleton_is_proven",
            "every_guarded_write_has_a_version_to_read",
            "every_precondition_view_has_options_that_make_sense",
        ],
    )
    def test_says_nothing_without_drf(self, monkeypatch, check):
        monkeypatch.setattr(apps, "is_installed", lambda name: name != "rest_framework")

        assert getattr(checks, check)(app_configs=None) == []


class TestTheSingletonCheck:
    def test_reports_each_problem(self, monkeypatch):
        monkeypatch.setattr(flattened_singleton_related, "singleton_related_problems", lambda: ["one", "two"])

        errors = checks.every_flattened_singleton_is_proven(app_configs=None)

        assert [(error.id, error.msg) for error in errors] == [("isik.E006", "one"), ("isik.E006", "two")]
        assert errors[0].hint == "See FlattenedSingletonRelatedMixin (isik.django.drf.serializers)."


class TestTheHistoryIndexCheck:
    def test_reads_the_projects_own_apps(self, monkeypatch):
        seen = []
        monkeypatch.setattr(db_history, "history_index_problems", lambda configs: seen.append(configs) or ["one"])

        (error,) = checks.every_event_model_indexes_its_streams(app_configs=None)

        assert (error.id, error.msg) == ("isik.E008", "one")
        assert error.hint == "See object_stream_index() (isik.django.apps.common.db.history)."
        assert [config.label for config in seen[0]] == ["testapp"]

    def test_reads_the_apps_it_is_told(self, settings, monkeypatch):
        seen = []
        monkeypatch.setattr(db_history, "history_index_problems", lambda configs: seen.append(configs) or [])
        settings.ISIK_CHECKS = {Check.HISTORY_INDEXES: {CheckOption.APPS: ["isik_common", "testapp"]}}

        checks.every_event_model_indexes_its_streams(app_configs=None)

        assert [config.label for config in seen[0]] == ["isik_common", "testapp"]

    def test_says_nothing_without_pghistory(self, monkeypatch):
        monkeypatch.setattr(apps, "is_installed", lambda name: name != "pghistory")
        monkeypatch.setattr(db_history, "history_index_problems", lambda configs: ["one"])

        assert checks.every_event_model_indexes_its_streams(app_configs=None) == []

    def test_the_test_project_has_every_index(self):
        assert checks.every_event_model_indexes_its_streams(app_configs=None) == []


class TestTheKeptFieldConditionCheck:
    def test_reads_the_projects_own_apps(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            conditions, "kept_field_condition_problems", lambda configs: seen.append(configs) or ["one", "two"]
        )

        errors = checks.no_trigger_condition_compares_a_kept_field(app_configs=None)

        assert [(error.id, error.msg) for error in errors] == [("isik.E013", "one"), ("isik.E013", "two")]
        assert errors[0].hint == (
            "See AnyChange and its siblings (isik.django.apps.common.db.conditions), which leave them out."
        )
        assert [config.label for config in seen[0]] == ["testapp"]

    def test_reads_the_apps_it_is_told(self, settings, monkeypatch):
        seen = []
        monkeypatch.setattr(conditions, "kept_field_condition_problems", lambda configs: seen.append(configs) or [])
        settings.ISIK_CHECKS = {Check.KEPT_FIELD_CONDITIONS: {CheckOption.APPS: ["isik_common"]}}

        checks.no_trigger_condition_compares_a_kept_field(app_configs=None)

        assert [config.label for config in seen[0]] == ["isik_common"]

    def test_the_test_project_has_none(self):
        assert checks.no_trigger_condition_compares_a_kept_field(app_configs=None) == []


class TestTheExemptionsCheck:
    def test_names_each_exemption_loading_missed(self, monkeypatch):
        urlconfs = []
        monkeypatch.setattr(
            exemptions, "unseen_project_exemptions", lambda urlconf: urlconfs.append(urlconf) or [("a.py", 3, "Skip")]
        )
        monkeypatch.setattr(exemptions, "unimported_project_exemption_types", lambda urlconf: [("b.py", 7, "NoX")])

        errors = checks.every_exemption_is_seen(app_configs=None)

        assert [(error.id, error.msg) for error in errors] == [
            ("isik.E009", "a.py:3 makes an exemption through Skip that loading the project didn't."),
            ("isik.E009", "b.py:7 declares NoX, which loading the project didn't import."),
        ]
        assert errors[0].hint == "Make it where loading the project runs it - see manage.py exemptions."
        assert urlconfs == [None]

    def test_the_test_project_has_none(self, settings):
        settings.ROOT_URLCONF = URLS

        assert checks.every_exemption_is_seen(app_configs=None) == []


class TestTheDocumentationCheck:
    def test_reads_the_projects_own_apps_with_every_exemption(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            documentation,
            "documentation_problems",
            lambda configs, exemption_types, inherited_from_libraries: (
                seen.append((configs, exemption_types)) or ["one"]
            ),
        )

        (error,) = checks.every_field_says_what_it_holds(app_configs=None)

        assert (error.id, error.msg) == ("isik.E010", "one")
        assert error.hint == "See documentation_problems() (isik.django.apps.common.db.documentation)."
        configs, types = seen[0]
        assert [config.label for config in configs] == ["testapp"]
        assert types == [Exemption]

    def test_takes_exemption_types_and_their_paths(self, settings, monkeypatch):
        seen = []
        monkeypatch.setattr(
            documentation,
            "documentation_problems",
            lambda configs, exemption_types, inherited_from_libraries: seen.append(exemption_types) or [],
        )
        settings.ISIK_CHECKS = {
            Check.DOCUMENTATION: {
                CheckOption.EXEMPTION_TYPES: ["isik.django.apps.common.atomic.NotAtomic", ViewSetRegistryExemption]
            }
        }

        checks.every_field_says_what_it_holds(app_configs=None)

        assert seen == [[NotAtomic, ViewSetRegistryExemption]]


def test_project_app_configs_by_label_in_the_order_given():
    assert [config.label for config in project_app_configs(["testapp", "auth"])] == ["testapp", "auth"]


class TestACheckOfIsiks:
    def test_is_registered_with_its_tags_and_runs_with_its_options(self, settings):
        from django.core.checks import Tags, registry

        seen = []

        def made_here(options, **kwargs):
            seen.append((options, kwargs))
            return [Error("message", hint="hint", obj="object", id="tests.E001")]

        check = checks.isik_check(Check.VALIDATORS, Tags.database)(made_here)
        try:
            settings.ISIK_CHECKS = {Check.VALIDATORS: {CheckOption.SEVERITY: Severity.WARNING}}

            (message,) = check(app_configs=None, databases=["default"])

            assert (type(message), message.msg, message.hint, message.obj, message.id) == (
                CheckWarning,
                "message",
                "hint",
                "object",
                "tests.E001",
            )
            assert seen == [(checks.check_options(Check.VALIDATORS), {"databases": ["default"]})]
            assert check in registry.registry.registered_checks
            assert check.tags == (Tags.database,)
            assert check.__name__ == "made_here"
        finally:
            registry.registry.registered_checks.discard(check)


class TestTheSettingsCheckGoesOn:
    def hints(self):
        return {message.hint for message in checks.isik_checks_are_configured(None) if message.id == "isik.E012"}

    def test_after_an_unknown_check(self, settings):
        settings.ISIK_CHECKS = {"idempotence": {}, Check.VALIDATORS: {CheckOption.SEVERITY: "loud"}}

        assert [message.id for message in checks.isik_checks_are_configured(None)] == ["isik.E012", "isik.E012"]
        assert self.hints() == {"See isik.django.apps.common.checks."}

    def test_after_an_entry_not_a_dict(self, settings):
        settings.ISIK_CHECKS = {
            Check.IDEMPOTENCY: False,
            Check.VALIDATORS: {CheckOption.SEVERITY: "loud", CheckOption.APPS: []},
        }

        assert len(checks.isik_checks_are_configured(None)) == 3
        assert self.hints() == {"See isik.django.apps.common.checks."}

    def test_names_every_urlconf_left_unchecked(self, settings, monkeypatch):
        from isik.django.apps.common import urlconfs

        monkeypatch.setattr(
            urlconfs, "project_urlconfs", lambda urlconf=None: ["a", "b", "c"] if urlconf is None else list(urlconf)
        )
        settings.ISIK_CHECKS = {Check.IDEMPOTENCY: {CheckOption.URLCONFS: ["b"]}}

        (message,) = checks.isik_checks_are_configured(None)

        assert message.msg == "ISIK_CHECKS[Check.IDEMPOTENCY] leaves served urlconfs unchecked: a, c."


@pytest.mark.parametrize(
    ("check", "module", "names"),
    [
        ("every_precondition_view_compares_a_token_it_can_trust", preconditions, ["precondition_problems"]),
        (
            "every_precondition_view_has_options_that_make_sense",
            preconditions,
            ["precondition_option_problems", "precondition_option_warnings"],
        ),
        ("every_guarded_write_has_a_version_to_read", preconditions, ["precondition_read_problems"]),
        ("every_exemption_is_seen", exemptions, ["unseen_project_exemptions", "unimported_project_exemption_types"]),
    ],
)
def test_a_check_reads_the_urlconfs_it_is_told(settings, monkeypatch, check, module, names):
    asked = []
    for name in names:
        monkeypatch.setattr(module, name, lambda urlconf, name=name: asked.append((name, urlconf)) or [])
    by_check = {
        "every_precondition_view_compares_a_token_it_can_trust": Check.CONCURRENCY_TOKENS,
        "every_precondition_view_has_options_that_make_sense": Check.PRECONDITION_OPTIONS,
        "every_guarded_write_has_a_version_to_read": Check.VERSION_READS,
        "every_exemption_is_seen": Check.EXEMPTIONS,
    }
    settings.ISIK_CHECKS = {by_check[check]: {CheckOption.URLCONFS: [URLS]}}

    getattr(checks, check)(app_configs=None)

    assert asked == [(name, [URLS]) for name in names]


def test_the_documentation_check_hands_on_inherited_from_libraries(settings, monkeypatch):
    seen = []
    monkeypatch.setattr(
        documentation,
        "documentation_problems",
        lambda configs, exemption_types, inherited_from_libraries: seen.append(inherited_from_libraries) or [],
    )

    checks.every_field_says_what_it_holds(app_configs=None)
    settings.ISIK_CHECKS = {Check.DOCUMENTATION: {CheckOption.INHERITED_FROM_LIBRARIES: False}}
    checks.every_field_says_what_it_holds(app_configs=None)

    assert seen == [True, False]
    assert checks.isik_checks_are_configured(None) == []
