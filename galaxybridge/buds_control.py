"""Safe, one-shot noise-control client for modern Galaxy Buds.

The Buds4 Pro regular control profile is deliberately kept separate from the
SMEP multipoint profile in :mod:`galaxybridge.buds`.  This module only exposes
the documented NOISE_CONTROLS command and accepts success after an exact mode
readback from the earbuds.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Iterable

from .buds import BudsError, crc16_ccitt, device_connected
from .operation_lock import buds_operation_lock


CONTROL_UUID = "2e73a4ad-332d-41fc-90e2-16bef06523f2"
SPP_SOM = 0xFD
SPP_EOM = 0xDD
ACKNOWLEDGEMENT = 0x42
EXTENDED_STATUS_UPDATED = 0x61
NOISE_CONTROLS_UPDATE = 0x77
NOISE_CONTROLS = 0x78


_CONTROL_BUS: object | None = None


def _persistent_control_bus(dbus: object, dbus_glib: object) -> object:
    """Return one GLib-attached private bus for the daemon process.

    BlueZ Profile1 objects need asynchronous D-Bus dispatch.  A process-global
    SystemBus connection may already have been cached by an earlier synchronous
    Device1 read without a main loop, while repeatedly closing private buses can
    race BlueZ's final Release callback.  One private, main-loop-aware control
    bus avoids both lifetime problems.
    """

    global _CONTROL_BUS
    if _CONTROL_BUS is None:
        mainloop = dbus_glib.DBusGMainLoop()
        _CONTROL_BUS = dbus.SystemBus(private=True, mainloop=mainloop)
    return _CONTROL_BUS


class NoiseControlMode(IntEnum):
    OFF = 0
    ANC = 1
    AMBIENT = 2

    @classmethod
    def from_name(cls, value: str) -> "NoiseControlMode":
        aliases = {
            "off": cls.OFF,
            "anc": cls.ANC,
            "ambient": cls.AMBIENT,
        }
        try:
            return aliases[value.strip().lower()]
        except KeyError as exc:
            raise ValueError("noise-control mode must be off, anc or ambient") from exc

    @property
    def label(self) -> str:
        return {
            self.OFF: "off",
            self.ANC: "anc",
            self.AMBIENT: "ambient",
        }[self]


def encode_spp_frame(message_id: int, payload: bytes = b"") -> bytes:
    """Encode one modern non-fragmented SPP request."""
    if not 0 <= message_id <= 0xFF:
        raise ValueError("message id must fit in one byte")
    body = bytes([message_id]) + payload
    size = len(body) + 2  # message id + payload + CRC
    crc = crc16_ccitt(body)
    return bytes([SPP_SOM]) + struct.pack("<H", size) + body + struct.pack("<H", crc) + bytes([SPP_EOM])


def decode_spp_frame(raw: bytes) -> tuple[int, bytes]:
    if len(raw) < 7 or raw[0] != SPP_SOM or raw[-1] != SPP_EOM:
        raise BudsError("invalid regular SPP frame markers")
    header = struct.unpack_from("<H", raw, 1)[0]
    size = header & 0x03FF
    if header & 0x2000:
        raise BudsError("fragmented regular SPP frames are not supported")
    if len(raw) != size + 4:
        raise BudsError("invalid regular SPP frame size")
    body = raw[3:-3]
    if not body:
        raise BudsError("regular SPP frame has no message id")
    actual_crc = struct.unpack_from("<H", raw, len(raw) - 3)[0]
    if crc16_ccitt(body) != actual_crc:
        raise BudsError("invalid regular SPP CRC")
    return body[0], body[1:]


def extract_spp_frames(buffer: bytearray) -> Iterable[bytes]:
    """Yield complete regular SPP frames and retain an incomplete tail."""
    while True:
        try:
            start = buffer.index(SPP_SOM)
        except ValueError:
            buffer.clear()
            return
        if start:
            del buffer[:start]
        if len(buffer) < 4:
            return
        size = struct.unpack_from("<H", buffer, 1)[0] & 0x03FF
        total = size + 4
        if total < 7 or total > 4096:
            del buffer[0]
            continue
        if len(buffer) < total:
            return
        frame = bytes(buffer[:total])
        del buffer[:total]
        yield frame


def extract_noise_mode(frame: bytes) -> NoiseControlMode | None:
    """Extract only an explicit noise-control readback."""
    try:
        message_id, payload = decode_spp_frame(frame)
    except BudsError:
        return None
    raw_mode: int | None = None
    if message_id == ACKNOWLEDGEMENT and len(payload) >= 2 and payload[0] == NOISE_CONTROLS:
        raw_mode = payload[1]
    elif message_id == NOISE_CONTROLS_UPDATE and payload:
        raw_mode = payload[0]
    elif message_id == EXTENDED_STATUS_UPDATED and len(payload) > 12:
        raw_mode = payload[12]
    try:
        return NoiseControlMode(raw_mode) if raw_mode is not None else None
    except ValueError:
        return None


class _RegularProfileTransport:
    def __init__(self, control_socket: socket.socket, cleanup: Callable[[], None]):
        self.socket = control_socket
        self._cleanup = cleanup
        self._closed = False
        self._eof = False

    def __enter__(self) -> "_RegularProfileTransport":
        return self

    def send(self, frame: bytes) -> None:
        self.socket.sendall(frame)

    @property
    def closed(self) -> bool:
        return self._closed or self._eof

    def read_until(
        self,
        seconds: float,
        predicate: Callable[[list[bytes]], bool] | None = None,
        settle_seconds: float = 0.0,
    ) -> list[bytes]:
        deadline = time.monotonic() + seconds
        buffer = bytearray()
        frames: list[bytes] = []
        matched_until: float | None = None
        while time.monotonic() < deadline:
            now = time.monotonic()
            if matched_until is not None and now >= matched_until:
                break
            read_end = min(deadline, matched_until) if matched_until is not None else deadline
            self.socket.settimeout(max(0.01, min(0.5, read_end - now)))
            try:
                chunk = self.socket.recv(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                raise BudsError(f"regular Buds control read failed: {exc}") from exc
            if not chunk:
                self._eof = True
                break
            buffer.extend(chunk)
            frames.extend(extract_spp_frames(buffer))
            if predicate is not None:
                if predicate(frames):
                    matched_until = time.monotonic() + max(0.0, settle_seconds)
                else:
                    matched_until = None
        return frames

    def read_for(self, seconds: float) -> list[bytes]:
        return self.read_until(seconds)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.socket.close()
        finally:
            self._cleanup()

    def __exit__(self, *_exc: object) -> None:
        self.close()


def open_regular_profile(address: str, timeout: float = 12.0) -> _RegularProfileTransport:
    """Open the Buds4 Pro regular profile through BlueZ Profile1.

    BlueZ owns service discovery and passes the exact connected RFCOMM file
    descriptor to us.  No numeric channel is guessed or reused from SMEP.
    """
    try:
        import dbus
        import dbus.mainloop.glib
        import dbus.service
        from gi.repository import GLib
    except ImportError as exc:
        raise BudsError("Python D-Bus support is missing (install python3-dbus and python3-gi)") from exc

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = _persistent_control_bus(dbus, dbus.mainloop.glib)
    object_manager = dbus.Interface(
        bus.get_object("org.bluez", "/"),
        "org.freedesktop.DBus.ObjectManager",
    )
    target = address.upper()
    device_path: str | None = None
    connected = False
    for path, interfaces in object_manager.GetManagedObjects().items():
        properties = interfaces.get("org.bluez.Device1")
        if properties and str(properties.get("Address", "")).upper() == target:
            device_path = str(path)
            connected = bool(properties.get("Connected", False))
            break
    if device_path is None:
        raise BudsError(f"Buds {address} are not known to BlueZ")
    if not connected:
        raise BudsError("Buds must already be connected before changing noise control")

    profile_path = "/org/galaxybridge/buds_control_profile"
    loop = GLib.MainLoop()

    class Profile(dbus.service.Object):
        def __init__(self) -> None:
            super().__init__(bus, profile_path)
            self.fd = -1
            self.error = ""

        @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
        def Release(self) -> None:
            if self.fd < 0:
                self.error = "BlueZ released the Buds control profile"
            if loop.is_running():
                loop.quit()

        @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
        def NewConnection(self, path: object, descriptor: object, _properties: object) -> None:
            received_fd = descriptor.take()
            if str(path) != device_path:
                os.close(received_fd)
                self.error = "BlueZ delivered a control connection for another device"
            else:
                self.fd = received_fd
            if loop.is_running():
                loop.quit()

        @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
        def RequestDisconnection(self, _path: object) -> None:
            if self.fd >= 0:
                os.close(self.fd)
                self.fd = -1

    profile = Profile()
    manager = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
    options = {
        "Role": "client",
        "Service": CONTROL_UUID,
        "Name": "GalaxyBridge",
    }
    try:
        manager.RegisterProfile(profile_path, CONTROL_UUID, options)
    except Exception as exc:
        profile.remove_from_connection()
        raise BudsError(f"could not register the regular Buds control profile: {exc}") from exc

    device = dbus.Interface(bus.get_object("org.bluez", device_path), "org.bluez.Device1")

    def connection_error(error: object) -> None:
        profile.error = str(error)
        if loop.is_running():
            loop.quit()

    def connection_reply() -> None:
        # NewConnection normally arrives immediately after this reply.  Keep
        # the loop alive until the descriptor itself is delivered.
        return None

    source_active = True

    def connection_timeout() -> bool:
        nonlocal source_active
        source_active = False
        profile.error = "BlueZ timed out while opening the Buds control profile"
        if loop.is_running():
            loop.quit()
        return False

    source_id = GLib.timeout_add(max(1, int(timeout * 1000)), connection_timeout)
    try:
        device.ConnectProfile(CONTROL_UUID, reply_handler=connection_reply, error_handler=connection_error)
        loop.run()
    except Exception as exc:
        profile.error = str(exc)
    finally:
        if source_id and source_active:
            try:
                GLib.source_remove(source_id)
            except GLib.Error:
                pass

    if profile.fd < 0:
        try:
            manager.UnregisterProfile(profile_path)
        except Exception:
            pass
        profile.remove_from_connection()
        raise BudsError(f"could not open the regular Buds control profile: {profile.error or 'no descriptor received'}")

    control_socket = socket.socket(fileno=profile.fd)
    profile.fd = -1  # ownership moved to the socket object

    def cleanup() -> None:
        try:
            manager.UnregisterProfile(profile_path)
        except Exception:
            pass
        try:
            profile.remove_from_connection()
        except Exception:
            pass
        # Keep the private control bus alive for the daemon's lifetime.  Only
        # this temporary BlueZ profile is unregistered here.

    return _RegularProfileTransport(control_socket, cleanup)


@dataclass
class NoiseControlResult:
    success: bool
    address: str
    requested_mode: str | None
    reported_mode: str | None
    frames_seen: int
    error: str = ""


class NoiseControlClient:
    def __init__(
        self,
        address: str,
        transport_factory: Callable[[str, float], _RegularProfileTransport] = open_regular_profile,
    ):
        self.address = address.upper()
        self._transport_factory = transport_factory

    def status(self, read_seconds: float = 2.5) -> NoiseControlResult:
        frames: list[bytes] = []
        try:
            if not device_connected(self.address):
                raise BudsError("Buds must already be connected before reading noise control")
            with buds_operation_lock(), self._transport_factory(self.address, 12.0) as transport:
                frames = transport.read_until(
                    read_seconds,
                    lambda observed: any(extract_noise_mode(frame) is not None for frame in observed),
                    settle_seconds=0.1,
                )
            values = [value for value in (extract_noise_mode(frame) for frame in frames) if value is not None]
            if not values:
                raise BudsError("the Buds did not report their noise-control mode")
            return NoiseControlResult(True, self.address, None, values[-1].label, len(frames))
        except (BudsError, OSError) as exc:
            return NoiseControlResult(False, self.address, None, None, len(frames), str(exc))

    def set_mode(self, mode: NoiseControlMode, read_seconds: float = 2.5) -> NoiseControlResult:
        if mode not in (NoiseControlMode.OFF, NoiseControlMode.ANC, NoiseControlMode.AMBIENT):
            raise ValueError("unsupported noise-control mode")
        frames: list[bytes] = []
        try:
            if not device_connected(self.address):
                raise BudsError("Buds must already be connected before changing noise control")
            with buds_operation_lock(), self._transport_factory(self.address, 12.0) as transport:
                transport.send(encode_spp_frame(NOISE_CONTROLS, bytes([mode])))
                frames = transport.read_until(
                    read_seconds,
                    lambda observed: (
                        bool(values := [value for value in (extract_noise_mode(frame) for frame in observed) if value is not None])
                        and values[-1] == mode
                    ),
                    settle_seconds=0.1,
                )
            values = [value for value in (extract_noise_mode(frame) for frame in frames) if value is not None]
            reported = values[-1] if values else None
            if reported != mode:
                observed = reported.label if reported is not None else "none"
                raise BudsError(f"no matching noise-control readback was observed (reported: {observed})")
            return NoiseControlResult(True, self.address, mode.label, reported.label, len(frames))
        except (BudsError, OSError) as exc:
            return NoiseControlResult(False, self.address, mode.label, None, len(frames), str(exc))


class WarmNoiseControlSession:
    """Reuse the regular control profile briefly and observe mode updates.

    The owner must call :meth:`poll` regularly from the same thread that calls
    :meth:`status` and :meth:`set_mode`.  Keeping all D-Bus profile lifetime
    work on one thread avoids cross-thread profile cleanup.  The connection is
    closed after a short idle period so the phone's manager is never competed
    with indefinitely.
    """

    def __init__(
        self,
        address: str,
        transport_factory: Callable[[str, float], _RegularProfileTransport] = open_regular_profile,
        idle_seconds: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
        on_mode: Callable[[NoiseControlMode], None] | None = None,
    ):
        self.address = address.upper()
        self._transport_factory = transport_factory
        self._idle_seconds = max(1.0, idle_seconds)
        self._clock = clock
        self._on_mode = on_mode
        self._transport: _RegularProfileTransport | None = None
        self._last_used = 0.0
        self._last_mode: NoiseControlMode | None = None
        self._invalidated = threading.Event()

    @property
    def last_mode(self) -> NoiseControlMode | None:
        return self._last_mode

    def _remember(self, frames: list[bytes]) -> NoiseControlMode | None:
        values = [value for value in (extract_noise_mode(frame) for frame in frames) if value is not None]
        if values:
            self._last_mode = values[-1]
            if self._on_mode is not None:
                self._on_mode(self._last_mode)
        return self._last_mode

    def _close(self) -> None:
        transport, self._transport = self._transport, None
        self._last_mode = None
        if transport is not None:
            transport.close()

    def close(self) -> None:
        self._close()

    def invalidate(self) -> None:
        """Request closure after a BlueZ disconnect from any daemon thread."""
        self._last_mode = None
        self._invalidated.set()

    def _ensure_transport(self) -> _RegularProfileTransport:
        if self._invalidated.is_set():
            self._invalidated.clear()
            self._close()
        if self._transport is None or self._transport.closed:
            self._close()
            self._transport = self._transport_factory(self.address, 12.0)
        self._last_used = self._clock()
        return self._transport

    def status(self, read_seconds: float = 1.5) -> NoiseControlResult:
        frames: list[bytes] = []
        try:
            if not device_connected(self.address):
                raise BudsError("Buds must already be connected before reading noise control")
            with buds_operation_lock():
                transport = self._ensure_transport()
                # Polling keeps the cached mode current while the warm profile
                # is open.  Return it immediately instead of forcing silence
                # on the socket for every menu opening.
                if self._last_mode is None:
                    frames = transport.read_until(
                        read_seconds,
                        lambda observed: any(extract_noise_mode(frame) is not None for frame in observed),
                        settle_seconds=0.1,
                    )
                    self._remember(frames)
            if self._last_mode is None:
                raise BudsError("the Buds did not report their noise-control mode")
            self._last_used = self._clock()
            return NoiseControlResult(True, self.address, None, self._last_mode.label, len(frames))
        except (BudsError, OSError) as exc:
            self._close()
            return NoiseControlResult(False, self.address, None, None, len(frames), str(exc))

    def set_mode(self, mode: NoiseControlMode, read_seconds: float = 1.5) -> NoiseControlResult:
        if mode not in (NoiseControlMode.OFF, NoiseControlMode.ANC, NoiseControlMode.AMBIENT):
            raise ValueError("unsupported noise-control mode")
        frames: list[bytes] = []
        try:
            if not device_connected(self.address):
                raise BudsError("Buds must already be connected before changing noise control")
            with buds_operation_lock():
                transport = self._ensure_transport()
                transport.send(encode_spp_frame(NOISE_CONTROLS, bytes([mode])))
                frames = transport.read_until(
                    read_seconds,
                    lambda observed: (
                        bool(values := [value for value in (extract_noise_mode(frame) for frame in observed) if value is not None])
                        and values[-1] == mode
                    ),
                    settle_seconds=0.1,
                )
                values = [value for value in (extract_noise_mode(frame) for frame in frames) if value is not None]
                reported = values[-1] if values else None
                self._remember(frames)
            self._last_used = self._clock()
            if reported != mode:
                observed = reported.label if reported is not None else "none"
                raise BudsError(f"no matching noise-control readback was observed (reported: {observed})")
            return NoiseControlResult(True, self.address, mode.label, reported.label, len(frames))
        except (BudsError, OSError) as exc:
            self._close()
            return NoiseControlResult(False, self.address, mode.label, None, len(frames), str(exc))

    def poll(self, seconds: float = 0.05) -> None:
        """Consume spontaneous phone/touch updates while the profile is warm."""
        if self._invalidated.is_set():
            self._invalidated.clear()
            self._close()
            return
        if self._transport is None:
            return
        if self._clock() - self._last_used >= self._idle_seconds:
            self._close()
            return
        try:
            frames = self._transport.read_until(seconds)
            self._remember(frames)
            if self._transport.closed:
                self._close()
        except (BudsError, OSError):
            self._close()
