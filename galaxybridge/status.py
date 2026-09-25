"""Small atomic status file shared by the Buds daemon and desktop UI."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable


def default_buds_status_path() -> Path:
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_home / "galaxybridge" / "buds-status.json"


def default_noise_status_path() -> Path:
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_home / "galaxybridge" / "noise-control.json"


class BudsStatusWriter:
    def __init__(
        self,
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.path = path or default_buds_status_path()
        self._clock = clock
        self._lock = threading.Lock()
        self._payload: dict[str, Any] = {
            "version": 1,
            "state": "starting",
            "detail": "",
        }

    # Fields that describe a single operation and must not linger after it ends:
    # they are cleared on every update and only present again if re-supplied.
    _EPHEMERAL_KEYS = frozenset({
        "requested_audio_mode",
        "requested_mode",
        "retry_paused",
        "reconnect_in",
        "attempt",
    })

    def update(self, state: str, detail: str = "", **fields: Any) -> None:
        with self._lock:
            for _key in self._EPHEMERAL_KEYS:
                self._payload.pop(_key, None)
            self._payload.update(fields)
            self._payload.update({
                "version": 1,
                "state": state,
                "detail": detail,
                "updated_at": self._clock(),
            })
            self._write(dict(self._payload))

    def heartbeat(self) -> None:
        with self._lock:
            self._payload["updated_at"] = self._clock()
            self._write(dict(self._payload))

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".buds-status-", dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


class NoiseControlStatusWriter(BudsStatusWriter):
    def __init__(
        self,
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ):
        super().__init__(path or default_noise_status_path(), clock)
