from django import template

register = template.Library()


@register.filter
def get(d, key):
    return (d or {}).get(key, "")
