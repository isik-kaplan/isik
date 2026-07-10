from django.template.loader import render_to_string
from mjml import mjml2html


def mjml_template(template, context, request=None):
    return mjml2html(render_to_string(template, context, request), disable_comments=True)


def text_template(template, context, request=None):
    return render_to_string(template, context, request)
