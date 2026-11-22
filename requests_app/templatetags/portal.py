from django import template

register = template.Library()


@register.simple_tag
def percent(value, maximum):
    """Width in percent for the report bars."""
    if not maximum:
        return 0
    return round(100 * (value or 0) / maximum)
