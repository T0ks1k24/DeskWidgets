"""Two-language UI: every string is written as tr('Ukrainian', 'English')."""
import locale
import os

_lang = None


def system_language():
    """'uk' or 'en', from the environment (Linux) or the user's locale (Windows)."""
    for var in ('LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
        value = os.environ.get(var, '')
        if value:
            return 'uk' if value.split(':')[0].startswith('uk') else 'en'
    value = _windows_locale() if os.name == 'nt' else (locale.getlocale()[0] or '')
    return 'uk' if value.lower().startswith(('uk', 'ukrainian')) else 'en'


def _windows_locale():
    import ctypes
    buffer = ctypes.create_unicode_buffer(85)
    if ctypes.windll.kernel32.GetUserDefaultLocaleName(buffer, len(buffer)):
        return buffer.value
    return ''


def lang():
    global _lang
    if _lang is None:
        from . import settings
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
