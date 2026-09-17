"""Detecting when the OS says "do not look at this".

macOS has a real answer: an application that focuses a password field can enable
*secure event input*, which is exactly the "stop watching" signal we want, and
``IsSecureEventInputEnabled()`` reports it.

Windows and X11 have no equivalent that an ordinary process can query. That is
worth stating plainly rather than papering over: on those platforms Copynion
cannot know a password field has focus, and the protection falls back to the
application policy (password managers are denied by default) and to the user's
own denylist. Anyone enabling ``full`` input fidelity on Windows or Linux should
understand that a password typed into a browser form is, in principle,
capturable - which is why the tier is opt-in and off by default.
"""

from __future__ import annotations

import sys

_checker = None
_probed = False


def _load_checker():
    """Return a callable reporting secure input, or None if unsupported."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util

        path = ctypes.util.find_library("Carbon") or (
            "/System/Library/Frameworks/Carbon.framework/Carbon"
        )
        carbon = ctypes.cdll.LoadLibrary(path)
        func = carbon.IsSecureEventInputEnabled
        func.restype = ctypes.c_bool
        func.argtypes = []
        func()  # probe once; a linkage problem surfaces here, not mid-session
        return func
    except Exception:
        return None


def is_secure_input_active() -> bool | None:
    """True/False on platforms that can tell us, ``None`` where we cannot.

    ``None`` is deliberately distinct from ``False``: "no password field is
    focused" and "we have no way to know" are different facts, and the caller
    reports them differently in ``copynion doctor``.
    """
    global _checker, _probed
    if not _probed:
        _checker = _load_checker()
        _probed = True
    if _checker is None:
        return None
    try:
        return bool(_checker())
    except Exception:
        return None


def describe() -> str:
    state = is_secure_input_active()
    if state is None:
        return "not detectable on this platform (relies on app policy instead)"
    return "active - input recording suppressed" if state else "inactive"
