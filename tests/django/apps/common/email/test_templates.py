from django.test import RequestFactory

from isik.django.apps.common.email import mjml_template, text_template


def test_text_template_renders_context_into_the_template():
    result = text_template("welcome.txt", {"name": "Alice"})
    assert result.strip() == "Welcome, Alice!"


def test_mjml_template_renders_context_and_compiles_to_html():
    result = mjml_template("welcome.mjml", {"name": "Alice"})
    assert "<!doctype html>" in result
    assert "Welcome, Alice!" in result


def test_mjml_template_strips_comments():
    result = mjml_template("with_comment.mjml", {"name": "Alice"})
    assert "a comment that disable_comments" not in result


def test_text_template_forwards_the_request_so_context_processors_fire():
    request = RequestFactory().get("/hello/")
    result = text_template("request_aware.txt", {}, request)
    assert "/hello/" in result


def test_mjml_template_forwards_the_request_so_context_processors_fire():
    request = RequestFactory().get("/hello/")
    result = mjml_template("request_aware.mjml", {}, request)
    assert "/hello/" in result
