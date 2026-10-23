"""What isik.E002 and precondition_coverage() say of the views a urlconf routes."""

from django.apps import apps

from isik.django.apps.common.checks import every_precondition_view_compares_a_token_it_can_trust
from isik.django.drf.coverage import CoverageStatus, precondition_coverage
from isik.django.drf.viewsets import preconditions
from isik.django.drf.viewsets.preconditions import precondition_problems


PROBLEMS = "tests.django.drf.preconditions.problem_urls"
FINE = "tests.django.drf.preconditions.urls"


class TestPreconditionProblems:
    def test_views_whose_tokens_can_be_trusted_have_none(self):
        assert precondition_problems(FINE) == []

    def test_each_problem_once_in_route_order(self):
        assert precondition_problems(PROBLEMS) == [
            "testapp.CleanedNote has no concurrency token - make it a VersionedModel or a DatabaseTimestampsModel, "
            "or name one with concurrency_field = ConcurrencyToken(field, reason=...).",
            "ModellessViewSet compares concurrency tokens of no model isik can find - set precondition_model.",
            "StampWritingSerializer, a serializer of StampWritingViewSet, lets a request write testapp.Widget's "
            "concurrency token 'updated_at' as 'updated_at' - make it read-only.",
            "StampRenamingSerializer, a serializer of StampWritingViewSet, lets a request write testapp.Widget's "
            "concurrency token 'updated_at' as 'stamp' - make it read-only.",
            "VersionWritingSerializer, a serializer of VersionWritingViewSet, lets a request write "
            "testapp.VersionedWidget's concurrency token 'version' as 'version' - make it read-only.",
        ]


class TestTheCheck:
    def test_reports_each_problem_as_an_error(self, monkeypatch):
        monkeypatch.setattr(preconditions, "precondition_problems", lambda urlconf: ["one", "two"])

        errors = every_precondition_view_compares_a_token_it_can_trust(app_configs=None)

        assert [(error.msg, error.id) for error in errors] == [("one", "isik.E002"), ("two", "isik.E002")]
        assert errors[0].hint == (
            "See PreconditionMixin (isik.django.drf.viewsets.preconditions) and concurrency tokens "
            "(isik.django.apps.common.db.concurrency)."
        )

    def test_reads_every_urlconf_the_project_serves(self, settings):
        settings.ROOT_URLCONF = PROBLEMS

        assert len(every_precondition_view_compares_a_token_it_can_trust(app_configs=None)) == 5

    def test_says_nothing_without_drf(self, monkeypatch):
        monkeypatch.setattr(apps, "is_installed", lambda name: name != "rest_framework")
        monkeypatch.setattr(preconditions, "precondition_problems", lambda urlconf: ["one"])

        assert every_precondition_view_compares_a_token_it_can_trust(app_configs=None) == []


class TestPreconditionCoverage:
    def by_route(self, urlconf):
        return {
            (entry.routed.route, entry.routed.method): (entry.status, entry.reason)
            for entry in precondition_coverage(urlconf)
        }

    def test_covers_each_write_to_one_object_and_nothing_else(self):
        report = self.by_route(FINE)
        detail = "^widgets/(?P<pk>[^/.]+)/$"

        assert (
            report[detail, "PUT"]
            == report[detail, "PATCH"]
            == report[detail, "DELETE"]
            == (
                CoverageStatus.COVERED,
                None,
            )
        )
        assert report["^widgets/(?P<pk>[^/.]+)/bump/$", "POST"] == (CoverageStatus.COVERED, None)
        assert report["^widgets/(?P<pk>[^/.]+)/rename_freely/$", "POST"] == (
            CoverageStatus.EXEMPT,
            "a rename that may land over any other write, by design",
        )
        assert report["widget/<uuid:pk>/", "PUT"] == (CoverageStatus.COVERED, None)
        # A read, a create and a POST to no one object have no version to name.
        assert (detail, "GET") not in report
        assert ("^widgets/$", "POST") not in report
        assert ("^widgets/make_two/$", "POST") not in report
        assert ("widget/", "POST") not in report

    def test_a_view_without_the_mixin_or_leaving_the_method_out_is_uncovered(self):
        report = self.by_route(PROBLEMS)

        assert report["^plain/(?P<pk>[^/.]+)/$", "PATCH"] == (CoverageStatus.UNCOVERED, None)
        assert report["^no-deletes/(?P<pk>[^/.]+)/$", "DELETE"] == (CoverageStatus.UNCOVERED, None)
        assert report["^no-deletes/(?P<pk>[^/.]+)/$", "PATCH"] == (CoverageStatus.COVERED, None)

    def test_each_entry_is_the_routed_action(self):
        (entry,) = [e for e in precondition_coverage(FINE) if e.routed.action == "bump"]

        assert entry.routed.view.__name__ == "WidgetViewSet"
        assert entry.is_uncovered is False
