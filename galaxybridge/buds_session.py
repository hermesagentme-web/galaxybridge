"""Short-lived proof that the current Buds power session was patched.

The marker lives in XDG_RUNTIME_DIR, so it never survives logout or reboot. It
avoids repeating the volatile SMEP write when the daemon is restarted moments
after a verified CLI patch. A persistent Buds disconnect clears it.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from pathlib import Path
from typing import Callable

from .bluez import normalise_address


def default_session_path() -> Path:
    configured = os.environ.get("XDG_RUNTIME_DIR")
    if configured:
        root = Path(configured)
    else:
        candidate = Path("/run/user") / str(os.getuid())
        root = candidate if candidate.is_dir() else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "galaxybridge" / "buds-session.json"


class BudsSessionMarker:
    def __init__(
        self,
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
        max_age_seconds: float = 300.0,
    ):
        self.path = path or default_session_path()
        self._clock = clock
        self.max_age_seconds = max(30.0, max_age_seconds)

    def remember(self, address: str, as_ver: int, channel: int) -> None:
        payload = {
            "version": 1,
            "address": normalise_address(address),
            "as_ver": int(as_ver),
            "channel": int(channel),
            "verified_at": self._clock(),
        }
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".buds-session-", dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def load(self, address: str) -> dict[str, object] | None:
        try:
            info = self.path.lstat()
            if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                return None
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return None
        if normalise_address(str(payload.get("address", ""))) != normalise_address(address):
            return None
        try:
            verified_at = float(payload["verified_at"])
            as_ver = int(payload["as_ver"])
            channel = int(payload["channel"])
        except (KeyError, TypeError, ValueError):
            return None
        age = self._clock() - verified_at
        if age < -5.0 or age > self.max_age_seconds or as_ver not in {2, 3} or not 1 <= channel <= 30:
            return None
        return {
            "address": normalise_address(address),
            "as_ver": as_ver,
            "channel": channel,
            "verified_at": verified_at,
        }

    def clear(self) -> None:
        try:
            info = self.path.lstat()
            if info.st_uid == os.getuid() and stat.S_ISREG(info.st_mode):
                self.path.unlink()
        except FileNotFoundError:
            pass
