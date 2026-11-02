"""Bounded parsing for optional URL filters. Invalid filters return HTTP 400."""
from django.core.exceptions import BadRequest
from django.utils.dateparse import parse_date


def date_filter(value, label='date'):
    if not value or str(value).lower()=='none':
        return None
    try:
        result=parse_date(str(value)) if len(str(value))<=10 else None
    except ValueError:
        result=None
    if result is None:
        raise BadRequest(f'Use a valid YYYY-MM-DD value for {label}.')
    return result


def id_filter(value, label='record'):
    if value in (None,''):
        return None
    text=str(value)
    if len(text)>19 or not text.isascii() or not text.isdecimal() or not 0<int(text)<=9223372036854775807:
        raise BadRequest(f'Choose a valid {label}.')
    return int(text)


def csv_cell(value):
    """Keep text cells as text when a downloaded CSV is opened in a spreadsheet."""
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r')):
        return "'"+value
    return value
