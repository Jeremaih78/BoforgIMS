from django import template

register = template.Library()


@register.filter
def task_duration(value):
    """Render a duration as a compact, human-readable dashboard label."""
    if not value:
        return ""
    total_minutes = max(0, round(value.total_seconds() / 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes} min"
