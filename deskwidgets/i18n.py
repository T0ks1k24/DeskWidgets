"""Two-language UI: every string is written as tr('Ukrainian', 'English')."""
from . import settings

_lang = None


def lang():
    global _lang
    if _lang is None:
        _lang = settings.language()
    return _lang


def set_lang(value):
    global _lang
    _lang = value


def tr(uk, en):
    return en if lang() == 'en' else uk


def loc(value, language=None):
    """A manifest value: plain string, or {"uk": ..., "en": ...}."""
    if isinstance(value, dict):
        return value.get(language or lang()) or value.get('en') or value.get('uk') or next(iter(value.values()), '')
    return value
