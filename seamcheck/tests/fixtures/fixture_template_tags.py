from django import template

register = template.Library()


@register.filter
def shout(value):
    return str(value).upper()


@register.simple_tag
def now_ish():
    return "soon"


@register.inclusion_tag("some_template.html")
def render_box(item):
    return {"item": item}
