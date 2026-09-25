"""Private per-user IPC for the persistent Galaxy Buds daemon."""

from __future__ import annotations

import json
import os
import socket
import stat
import struct
import threading
from pathlib import Path
from typing import Callable


MAX_MESSAGE_BYTES = 16 * 1024
# sun_path is 108 bytes on Linux including the trailing NUL, so a path must be
# strictly shorter than this to bind or connect.
SUN_PATH_LIMIT = 108


class BudsIpcError(RuntimeError):
    pass


def _socket_path_str(path: Path) -> str:
    """Return the socket path, refusing ones that cannot fit in sun_path."""
    text = str(path)
    if len(text) >= SUN_PATH_LIMIT:
        raise BudsIpcError(
            f"GalaxyBridge socket path is too long ({len(text)} bytes, "
            f"limit {SUN_PATH_LIMIT - 1}): {text}"
        )
    return text


def default_buds_socket_path() -> Path:
    configured = os.environ.get("XDG_RUNTIME_DIR")
    if configured:
        root = Path(configured)
    else:
        candidate = Path("/run/user") / str(os.getuid())
        root = candidate if candidate.is_dir() else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    path = root / "galaxybridge" / "buds-control.sock"
    if len(str(path)) >= SUN_PATH_LIMIT:
        # Fall back to a short per-user directory so the socket always fits sun_path.
        path = Path("/tmp") / f"gb-{os.getuid()}" / "buds-control.sock"
    return path


def request_buds_daemon(
    payload: dict[str, object],
    path: Path | None = None,
    timeout: float = 20.0,
) -> dict[str, object]:
    target = path or default_buds_socket_path()
    address = _socket_path_str(target)
    request = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(request) > MAX_MESSAGE_BYTES:
        raise BudsIpcError("GalaxyBridge control request is too large")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(address)
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_MESSAGE_BYTES:
                raise BudsIpcError("GalaxyBridge control response is too large")
            chunks.append(chunk)
    except (OSError, socket.timeout) as exc:
        raise BudsIpcError(f"GalaxyBridge Buds service is unavailable: {exc}") from exc
    finally:
        client.close()
    try:
        response = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BudsIpcError("GalaxyBridge Buds service returned an invalid response") from exc
    if not isinstance(response, dict):
        raise BudsIpcError("GalaxyBridge Buds service returned an invalid response")
    return response


class BudsControlServer:
    def __init__(
        self,
        handler: Callable[[dict[str, object]], dict[str, object]],
        idle: Callable[[], None] | None = None,
        cleanup: Callable[[], None] | None = None,
        path: Path | None = None,
    ):
        self.path = path or default_buds_socket_path()
        self._handler = handler
        self._idle = idle
        self._cleanup = cleanup
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self.error = ""

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._serve, name="buds-control-ipc", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        if self.error:
            raise BudsIpcError(self.error)

    def stop(self) -> None:
        self._stop.set()
        # Wake accept without inventing a second control mechanism.
        try:
            wake = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            wake.settimeout(0.2)
            wake.connect(_socket_path_str(self.path))
            wake.close()
        except (OSError, BudsIpcError):
            pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _prepare_path(self) -> None:
        _socket_path_str(self.path)  # fail fast on an unusable path
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            info = self.path.lstat()
        except FileNotFoundError:
            return
        if info.st_uid != os.getuid() or not stat.S_ISSOCK(info.st_mode):
            raise BudsIpcError(f"refusing to replace unsafe IPC path {self.path}")
        self.path.unlink()

    def _read_request(self, connection: socket.socket) -> dict[str, object]:
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = connection.recv(4096)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_MESSAGE_BYTES:
                raise BudsIpcError("control request is too large")
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        payload = json.loads(b"".join(chunks).split(b"\n", 1)[0].decode("utf-8"))
        if not isinstance(payload, dict):
            raise BudsIpcError("control request must be a JSON object")
        return payload

    def _serve(self) -> None:
        listener: socket.socket | None = None
        try:
            self._prepare_path()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(_socket_path_str(self.path))
            os.chmod(self.path, 0o600)
            listener.listen(4)
            listener.settimeout(0.2)
            self._ready.set()
            while not self._stop.is_set():
                try:
                    connection, _address = listener.accept()
                except socket.timeout:
                    if self._idle is not None:
                        self._idle()
                    continue
                with connection:
                    connection.settimeout(3.0)
                    if self._stop.is_set():
                        break
                    try:
                        if hasattr(socket, "SO_PEERCRED"):
                            credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                            _pid, uid, _gid = struct.unpack("3i", credentials)
                            if uid != os.getuid():
                                raise BudsIpcError("control request came from another user")
                        request = self._read_request(connection)
                        response = self._handler(request)
                    except Exception as exc:
                        response = {"success": False, "error": str(exc)}
                    try:
                        connection.sendall(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
                    except OSError:
                        continue  # A departed client must not kill the server.
        except Exception as exc:
            self.error = str(exc)
            self._ready.set()
        finally:
            if self._cleanup is not None:
                try:
                    self._cleanup()
                except Exception:
                    pass
            if listener is not None:
                listener.close()
            try:
                info = self.path.lstat()
                if info.st_uid == os.getuid() and stat.S_ISSOCK(info.st_mode):
                    self.path.unlink()
            except FileNotFoundError:
                pass
            self._ready.set()
