from isik.django.apps.common.email import mjml_template, text_template


def test_text_template_renders_context_into_the_template():
    result = text_template("welcome.txt", {"name": "Alice"})
    assert result.strip() == "Welcome, Alice!"


def test_mjml_template_renders_context_and_compiles_to_html():
    result = mjml_template("welcome.mjml", {"name": "Alice"})
    assert "<!doctype html>" in result
    assert "Welcome, Alice!" in result
