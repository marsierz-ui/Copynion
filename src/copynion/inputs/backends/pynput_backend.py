"""Global input capture via pynput.

pynput wraps the three platform mechanisms that exist for this (X11's XRecord,
macOS's CGEventTap, Windows' SetWindowsHookEx). Writing three native hooks by
hand would be a great deal of fragile code for no benefit, so this is the one
place Copynion takes a real dependency - and it is an **optional extra**,
installed only by someone who has decided they want input capture:

    pip install 'copynion[input]'

The observer runs perfectly well without it; input fidelity simply stays off.

Permissions: macOS requires both Accessibility and Input Monitoring for the
terminal or app running Copynion. X11 needs no special permission, which is
itself worth knowing. Wayland generally will not deliver global input to an
ordinary client, and Copynion reports that rather than working around it.
"""

from __future__ import annotations

import contextlib
import time

from copynion.inputs.backends.base import InputBackend
from copynion.inputs.recorder import InputRecorder
from copynion.inputs.secure_input import is_secure_input_active

# The import is lazy and cached. Two reasons: importing pynput starts platform
# machinery we do not want loaded in a process that will never capture input,
# and `tests/test_no_network.py` asserts that importing Copynion pulls in no
# networking modules - which must stay true regardless of what an optional
# third-party package happens to import at module scope.
_pynput: tuple[object, object] | None = None
_pynput_probed = False
_pynput_error: str = ""


def _load_pynput() -> tuple[object, object] | None:
    global _pynput, _pynput_probed, _pynput_error
    if _pynput_probed:
        return _pynput
    _pynput_probed = True
    try:
        from pynput import keyboard, mouse

        _pynput = (keyboard, mouse)
    except BaseException as exc:  # noqa: BLE001 - a broken build raises more than ImportError
        # The exception type does not distinguish the two cases: pynput raises
        # ImportError("this platform is not supported") when it is installed but
        # has no display to attach to. Ask the import system whether the package
        # is actually present instead of guessing from the exception.
        _pynput = None
        _pynput_error = (
            f"installed, but unusable here: {_one_line(str(exc))}"
            if _is_installed()
            else "not installed"
        )
    return _pynput


def _is_installed() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("pynput") is not None
    except BaseException:  # noqa: BLE001 - a broken package can break find_spec too
        return False


def _one_line(text: str, limit: int = 110) -> str:
    """Flatten a multi-line library error so it fits one line of `doctor`."""
    flattened = " ".join(text.split())
    return flattened if len(flattened) <= limit else flattened[:limit].rstrip() + "..."


def unavailable_reason() -> str:
    """Why input capture is unavailable, distinguishing the two cases.

    Telling someone to install a package they already have is the same class of
    unhelpfulness as claiming their key is in a keychain when it is in a file.
    """
    if _load_pynput() is not None:
        return ""
    return _pynput_error or "not installed"


def __getattr__(name: str):
    """Keep ``PYNPUT_AVAILABLE`` importable without forcing the import early."""
    if name == "PYNPUT_AVAILABLE":
        return _load_pynput() is not None
    raise AttributeError(name)

#: pynput modifier key names, normalised to side-independent names.
_MODIFIER_ALIASES = {
    "ctrl_l": "ctrl", "ctrl_r": "ctrl", "control": "ctrl",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt",
    "shift_l": "shift", "shift_r": "shift",
    "cmd_l": "cmd", "cmd_r": "cmd", "super": "cmd",
}


class PynputBackend(InputBackend):
    name = "pynput"
    requirement = (
        "pip install 'copynion[input]'; macOS also needs Accessibility "
        "and Input Monitoring permission"
    )

    @classmethod
    def describe_state(cls) -> str:
        """One line for ``copynion doctor``, accurate about which case applies."""
        if cls.is_available():
            return "available"
        reason = unavailable_reason()
        if reason.startswith("installed"):
            return f"{reason} (on Linux: no X11 DISPLAY, or a Wayland session)"
        return f"{reason} - run: pip install 'copynion[input]'"

    def __init__(self) -> None:
        self._recorder: InputRecorder | None = None
        self._keyboard_listener = None
        self._mouse_listener = None
        self._held_modifiers: set[str] = set()

    @classmethod
    def is_available(cls) -> bool:
        return _load_pynput() is not None

    # -- lifecycle ------------------------------------------------------------

    def start(self, recorder: InputRecorder) -> None:
        loaded = _load_pynput()
        if loaded is None:
            raise RuntimeError(
                "pynput is not installed; run: pip install 'copynion[input]'"
            )
        keyboard, mouse = loaded
        self._recorder = recorder
        self._keyboard_listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._mouse_listener = mouse.Listener(
            on_click=self._on_click, on_scroll=self._on_scroll, on_move=self._on_move
        )
        self._keyboard_listener.start()
        self._mouse_listener.start()

    def stop(self) -> None:
        for listener in (self._keyboard_listener, self._mouse_listener):
            if listener is not None:
                with contextlib.suppress(Exception):
                    listener.stop()
        self._keyboard_listener = self._mouse_listener = None
        self._held_modifiers.clear()

    # -- callbacks ------------------------------------------------------------
    #
    # These run on pynput's listener threads. They must never raise: an
    # exception here would tear down the listener and silently stop capture.

    def _guard(self) -> InputRecorder | None:
        recorder = self._recorder
        if recorder is None:
            return None
        # Re-check secure input on every event rather than on a timer: a
        # password field can gain focus between two keystrokes, and the whole
        # point is that those keystrokes are the ones that must not be kept.
        if is_secure_input_active():
            recorder.suppress("secure input (password field)")
        elif recorder.suppress_reason.startswith("secure input"):
            recorder.unsuppress()
        return recorder

    def _on_press(self, key) -> None:
        try:
            recorder = self._guard()
            if recorder is None:
                return
            name = self._key_name(key)
            if name in {"ctrl", "alt", "shift", "cmd"}:
                self._held_modifiers.add(name)
                return
            recorder.record_key(name, time.time(), frozenset(self._held_modifiers))
        except Exception:
            pass

    def _on_release(self, key) -> None:
        with contextlib.suppress(Exception):
            self._held_modifiers.discard(self._key_name(key))

    def _on_click(self, x, y, button, pressed) -> None:
        try:
            if not pressed:
                return
            recorder = self._guard()
            if recorder is not None:
                recorder.record_click(
                    getattr(button, "name", str(button)), int(x), int(y), time.time()
                )
        except Exception:
            pass

    def _on_scroll(self, x, y, dx, dy) -> None:
        try:
            recorder = self._guard()
            if recorder is not None:
                recorder.record_scroll(int(dx), int(dy), int(x), int(y), time.time())
        except Exception:
            pass

    def _on_move(self, x, y) -> None:
        try:
            recorder = self._guard()
            if recorder is not None:
                recorder.record_move(float(x), float(y), time.time())
        except Exception:
            pass

    # -- translation ----------------------------------------------------------

    @staticmethod
    def _key_name(key) -> str:
        """Normalise a pynput key to our naming scheme.

        Printable keys come back as the character itself; everything else as a
        lowercase name such as ``backspace``.
        """
        char = getattr(key, "char", None)
        if char:
            return char
        name = getattr(key, "name", None)
        if name:
            return _MODIFIER_ALIASES.get(name.lower(), name.lower())
        return str(key).replace("Key.", "").lower()
