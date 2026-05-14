import gettext
import os

DOMAIN = "nvtray"
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LOCALEDIR_SYSTEM = "/usr/share/locale"
_LOCALEDIR_LOCAL = os.path.join(_SCRIPT_DIR, "locales")


def _get_localedir() -> str:
    if os.path.isfile(os.path.join(_LOCALEDIR_SYSTEM, "zh_CN", "LC_MESSAGES", f"{DOMAIN}.mo")):
        return _LOCALEDIR_SYSTEM
    return _LOCALEDIR_LOCAL


def _get_translator():
    localedir = _get_localedir()
    try:
        translation = gettext.translation(DOMAIN, localedir=localedir)
        return translation.gettext
    except FileNotFoundError:
        return lambda s: s


_ = _get_translator()
