"""Shared runtime state between the watcher and other CLI invocations.

A small JSON file rather than a socket or a daemon protocol. ``copynion pause``
has to work instantly, from any terminal, even if the watcher is wedged - and a
file the user can read, and delete, is the most inspectable way to do that. The
watcher re-reads it every tick, so pausing takes effect within one poll interval.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class State:
    paused: bool = False
    pause_reason: str = ""
    paused_at: float | None = None
    watcher_pid: int | None = None
    watcher_started_at: float | None = None
    last_tick_at: float | None = None
    stats: dict = field(default_factory=dict)

    # -- persistence ----------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> State:
        if not Path(path).exists():
            return cls()
        try:
            raw = json.loads(Path(path).read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt state file must not stop anything; the safe reading is
            # "not paused, not running", which the next write repairs.
            return cls()
        known = {f for f in cls.__slots__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)  # atomic: a reader never sees a half-written state

    # -- watcher liveness -----------------------------------------------------

    @property
    def watcher_running(self) -> bool:
        if not self.watcher_pid:
            return False
        return _pid_alive(self.watcher_pid)

    def claim_watcher(self) -> None:
        self.watcher_pid = os.getpid()
        self.watcher_started_at = time.time()

    def release_watcher(self) -> None:
        self.watcher_pid = None
        self.watcher_started_at = None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True
