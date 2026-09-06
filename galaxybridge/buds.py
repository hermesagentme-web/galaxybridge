"""Galaxy Buds SMEP multipoint patcher.

This is a small, standalone implementation of the public GalaxyBudsClient
SMEP message shape.  It only writes the version-only MDE_VERSION property:
``04 03 04 00 00 0B 02``.  It does not touch Samsung Account fields.
"""

from __future__ import annotations

import logging
import re
import socket
import struct
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from .bluez import A2DP_SINK_UUID, BlueZError, connect_device, device_state, disconnect_device
from .operation_lock import serialized_buds_operation


LOG = logging.getLogger("galaxybridge.buds")


SMEP_UUID = "f8620674-a1ed-41ab-a8b9-de9ad655729d"
SMEP_SOM = 0xFC
SMEP_EOM = 0xCC
OUTER_ALT_ID = 0x01
WRITE_PROPERTY = 0x43
NOTIFY_PROPERTY = 0x45
MDE_VERSION_OPCODE = 0x0B
MDE_VERSION_AS_VER2 = bytes.fromhex("04 03 04 00 00 0B 02")


class BudsError(RuntimeError):
    pass


def crc16_ccitt(data: bytes, initial: int = 0) -> int:
    """CRC16-CCITT used by the SMEP SPP codec (poly 0x1021, init 0)."""
    crc = initial
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def encode_smep_frame(inner_id: int, payload: bytes = b"", response: bool = False) -> bytes:
    """Encode one non-fragmented alternative-mode frame."""
    body = bytes([OUTER_ALT_ID, inner_id]) + payload
    size = len(body) + 2  # outer message id + payload + CRC
    header = size | (0x1000 if response else 0)
    crc = crc16_ccitt(body)
    return bytes([SMEP_SOM]) + struct.pack("<H", header) + body + struct.pack("<H", crc) + bytes([SMEP_EOM])


def encode_mde_version_as_ver(as_ver: int = 2) -> bytes:
    if as_ver not in (2, 3):
        raise ValueError("asVer must be 2 or 3")
    blob = bytearray(MDE_VERSION_AS_VER2)
    blob[-1] = as_ver
    return encode_smep_frame(WRITE_PROPERTY, bytes(blob))


def decode_smep_frame(raw: bytes) -> tuple[int, bytes, bool]:
    if len(raw) < 8 or raw[0] != SMEP_SOM or raw[-1] != SMEP_EOM:
        raise BudsError("invalid SMEP frame markers")
    header = struct.unpack_from("<H", raw, 1)[0]
    size = header & 0x03FF
    if len(raw) != size + 4:
        raise BudsError("invalid SMEP frame size")
    body = raw[3:-3]
    actual_crc = struct.unpack_from("<H", raw, len(raw) - 3)[0]
    if crc16_ccitt(body) != actual_crc:
        raise BudsError("invalid SMEP CRC")
    return body[0], body[1:], bool(header & 0x1000)


def extract_as_ver(frame: bytes) -> int | None:
    """Read the state-frame asVer used by PR #729's readback check."""
    try:
        outer_id, body, _ = decode_smep_frame(frame)
    except BudsError:
        return None
    if outer_id != OUTER_ALT_ID or not body or body[0] != NOTIFY_PROPERTY:
        return None
    payload = body[1:]
    # GalaxyBudsClient's TryReadAsVer checks this exact state prefix and
    # deliberately reads the final peer field at offset six.
    if len(payload) >= 10 and payload[:4] == bytes.fromhex("02 05 4C 0B"):
        return payload[6]
    return None


def extract_frames(buffer: bytearray) -> Iterable[bytes]:
    """Yield complete SMEP frames from a stream buffer and mutate it in place."""
    while True:
        try:
            start = buffer.index(SMEP_SOM)
        except ValueError:
            buffer.clear()
            return
        if start:
            del buffer[:start]
        if len(buffer) < 4:
            return
        size = struct.unpack_from("<H", buffer, 1)[0] & 0x03FF
        total = size + 4
        if total < 8 or total > 4096:
            del buffer[0]
            continue
        if len(buffer) < total:
            return
        frame = bytes(buffer[:total])
        del buffer[:total]
        yield frame


def _normalise_uuid(value: str) -> str:
    value = value.lower().removeprefix("0x")
    return re.sub(r"[^0-9a-f]", "", value)


def _channel_from_sdp(text: str, uuid: str = SMEP_UUID) -> int | None:
    """Return only the RFCOMM channel positively tied to the SMEP UUID."""
    target = _normalise_uuid(uuid)
    xml_blocks = re.findall(r"<record>.*?</record>", text, flags=re.IGNORECASE | re.DOTALL)
    blocks = xml_blocks or re.split(r"\n\s*\n|(?=Service Name:)", text)
    for block in blocks:
        uuids = re.findall(r"UUID128\s*:\s*(0x[0-9a-f-]+)", block, flags=re.IGNORECASE)
        uuids.extend(re.findall(r'<uuid\s+value="([^"]+)"\s*/>', block, flags=re.IGNORECASE))
        if target not in {_normalise_uuid(value) for value in uuids}:
            continue
        xml_channel = re.search(
            r'<uuid\s+value="0x0003"\s*/>.*?<uint(?:8|16|32)\s+value="0x([0-9a-f]+)"\s*/>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if xml_channel:
            return int(xml_channel.group(1), 16)
        hexadecimal = re.search(
            r"RFCOMM.*?Channel/Port\s*\(Integer\)\s*:\s*0x([0-9a-f]+)",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if hexadecimal:
            return int(hexadecimal.group(1), 16)
        decimal = re.search(r"RFCOMM.*?Channel:\s*(\d+)", block, flags=re.IGNORECASE | re.DOTALL)
        if decimal:
            return int(decimal.group(1))
    return None


def find_rfcomm_channel(address: str, uuid: str = SMEP_UUID) -> int:
    """Resolve SPPSERVICE4 without ever guessing from an unrelated RFCOMM profile."""
    errors: list[str] = []
    commands = (["sdptool", "records", "--xml", address], ["sdptool", "browse", address])
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=58)
        except subprocess.TimeoutExpired as exc:
            partial_parts = [part for part in (exc.stdout, exc.stderr) if part]
            partial = "".join(
                part.decode(errors="replace") if isinstance(part, bytes) else part
                for part in partial_parts
            )
            channel = _channel_from_sdp(partial, uuid)
            if channel is not None:
                return channel
            errors.append(str(exc))
            continue
        except OSError as exc:
            errors.append(str(exc))
            continue
        output = result.stdout + result.stderr
        channel = _channel_from_sdp(output, uuid)
        # An exact UUID-to-RFCOMM mapping is authoritative even when sdptool
        # reports a later, unrelated browse error through its exit status.
        if channel is not None:
            return channel
        errors.append((result.stderr or result.stdout).strip())
    detail = "; ".join(error for error in errors if error)
    suffix = f" ({detail})" if detail else ""
    raise BudsError(
        f"SPPSERVICE4 ({uuid}) was not positively identified; refusing to guess an RFCOMM channel{suffix}"
    )


class RfcommTransport:
    def __init__(self, address: str, channel: int, timeout: float = 10.0):
        self.address = address
        self.channel = channel
        self.timeout = timeout
        self.socket: socket.socket | None = None

    def __enter__(self) -> "RfcommTransport":
        if not hasattr(socket, "AF_BLUETOOTH"):
            raise BudsError("this Python build has no Bluetooth socket support")
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(self.timeout)
        try:
            sock.connect((self.address, self.channel))
        except OSError as exc:
            sock.close()
            raise BudsError(f"could not open SMEP RFCOMM channel {self.channel}: {exc}") from exc
        self.socket = sock
        return self

    def send(self, frame: bytes) -> None:
        if self.socket is None:
            raise BudsError("SMEP transport is not open")
        self.socket.sendall(frame)

    def read_until(
        self,
        seconds: float,
        predicate: Callable[[list[bytes]], bool] | None = None,
        settle_seconds: float = 0.0,
    ) -> list[bytes]:
        if self.socket is None:
            raise BudsError("SMEP transport is not open")
        end = time.monotonic() + seconds
        buffer = bytearray()
        frames: list[bytes] = []
        matched_until: float | None = None
        while time.monotonic() < end:
            now = time.monotonic()
            if matched_until is not None and now >= matched_until:
                break
            read_end = min(end, matched_until) if matched_until is not None else end
            self.socket.settimeout(max(0.01, min(0.5, read_end - now)))
            try:
                chunk = self.socket.recv(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                raise BudsError(f"SMEP read failed: {exc}") from exc
            if not chunk:
                break
            buffer.extend(chunk)
            frames.extend(extract_frames(buffer))
            if predicate is not None:
                if predicate(frames):
                    matched_until = time.monotonic() + max(0.0, settle_seconds)
                else:
                    matched_until = None
        return frames

    def read_for(self, seconds: float) -> list[bytes]:
        return self.read_until(seconds)

    def __exit__(self, *_exc: object) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None


def bluetoothctl(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, check=False, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise BudsError(f"bluetoothctl {' '.join(args)} timed out after 20 seconds") from exc
    except OSError as exc:
        raise BudsError("bluetoothctl is unavailable") from exc


def device_info(address: str) -> str:
    result = bluetoothctl("info", address)
    return result.stdout + result.stderr


def device_connected(address: str) -> bool:
    try:
        return device_state(address).connected
    except BlueZError:
        return bool(re.search(r"^\s*Connected:\s*yes\s*$", device_info(address), flags=re.MULTILINE | re.IGNORECASE))


@dataclass
class PatchResult:
    success: bool
    address: str
    channel: int | None
    reported_as_ver: int | None
    frames_seen: int
    error: str = ""


class BudsPatcher:
    def __init__(
        self,
        address: str,
        channel: int | None = None,
        verified_channel: int | None = None,
    ):
        self.address = address
        self.channel = channel
        self.verified_channel = verified_channel

    def _verified_channel(self) -> int:
        if self.verified_channel is not None:
            if self.channel is not None and self.channel != self.verified_channel:
                raise BudsError(
                    f"channel hint {self.channel} conflicts with pre-verified channel {self.verified_channel}"
                )
            return self.verified_channel
        discovered_channel = find_rfcomm_channel(self.address)
        if self.channel is not None and self.channel != discovered_channel:
            raise BudsError(
                f"refusing unverified RFCOMM channel {self.channel}; "
                f"SPPSERVICE4 is on channel {discovered_channel}"
            )
        return discovered_channel

    def _restore_regular_session(self) -> str:
        """Restore music only; HFP would compete with the phone for multipoint."""
        try:
            state = connect_device(self.address, profile_uuid=A2DP_SINK_UUID)
        except BlueZError as exc:
            return str(exc)
        return "" if state.connected else "BlueZ returned before the regular connection was restored"

    @serialized_buds_operation
    def probe(self) -> PatchResult:
        """Open and close the verified SMEP transport without sending a payload."""
        connected_before = device_connected(self.address)
        released_regular_session = False
        channel: int | None = None
        try:
            channel = self._verified_channel()
            if connected_before:
                disconnected = disconnect_device(self.address)
                if disconnected.connected:
                    raise BudsError("could not release the regular Bluetooth session")
                released_regular_session = True
                time.sleep(0.5)
            with RfcommTransport(self.address, channel):
                pass
            result = PatchResult(True, self.address, channel, None, 0)
        except (BudsError, BlueZError) as exc:
            result = PatchResult(False, self.address, channel, None, 0, str(exc))
        if released_regular_session:
            restore_error = self._restore_regular_session()
            if restore_error:
                result.error = "; ".join(filter(None, (result.error, f"BlueZ restore pending: {restore_error}")))
        return result

    @serialized_buds_operation
    def apply(self, verify_seconds: float = 3.0) -> PatchResult:
        connected_before = device_connected(self.address)
        released_regular_session = False
        channel: int | None = None
        try:
            channel = self._verified_channel()
            if connected_before:
                disconnected = disconnect_device(self.address)
                if disconnected.connected:
                    raise BudsError("could not release the regular Bluetooth session")
                released_regular_session = True
                time.sleep(0.5)
            with RfcommTransport(self.address, channel) as transport:
                transport.send(encode_mde_version_as_ver(2))
                frames = transport.read_until(
                    verify_seconds,
                    lambda observed: (
                        bool(values := [value for value in (extract_as_ver(frame) for frame in observed) if value is not None])
                        and values[-1] in (2, 3)
                    ),
                    settle_seconds=0.25,
                )
            reported = next((value for value in (extract_as_ver(frame) for frame in frames) if value is not None), None)
            # The last notify is authoritative; earlier frames can reflect the
            # old peer value. Keep that behavior from the upstream patch.
            values = [value for value in (extract_as_ver(frame) for frame in frames) if value is not None]
            reported = values[-1] if values else reported
            if reported not in (2, 3):
                raise BudsError("write completed but no asVer=2/3 readback was observed")
            result = PatchResult(True, self.address, channel, reported, len(frames))
        except (BudsError, BlueZError) as exc:
            result = PatchResult(False, self.address, channel, None, 0, str(exc))
        if released_regular_session:
            restore_error = self._restore_regular_session()
            if restore_error:
                result.error = "; ".join(filter(None, (result.error, f"BlueZ restore pending: {restore_error}")))
        return result
