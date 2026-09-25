"""Event-driven user daemon for Buds multipoint and safe runtime controls."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time
from pathlib import Path

from .audio_policy import HostAudioPolicy
from .bluez import (
    A2DP_SINK_UUID,
    BlueZError,
    connect_device,
    device_state,
    disconnect_device,
    normalise_address,
    run_device_monitor,
)
from .buds import BudsPatcher
from .buds_control import NoiseControlMode, WarmNoiseControlSession
from .buds_ipc import BudsControlServer, BudsIpcError
from .buds_session import BudsSessionMarker
from .status import BudsStatusWriter, NoiseControlStatusWriter


LOG = logging.getLogger("galaxybridge.buds-daemon")
DEFAULT_PATCH_RETRY_DELAYS = (3.0, 8.0)
DEFAULT_CONNECT_BACKOFF = (3.0, 8.0, 20.0, 60.0, 180.0)
DEFAULT_RECONNECT_SECONDS = 300.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        LOG.warning("Ignoring invalid %s=%r; falling back to %s", name, os.environ.get(name), default)
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        LOG.warning("Ignoring invalid %s=%r; falling back to %s", name, os.environ.get(name), default)
        return default


def _normalise_address(value: str) -> str:
    return normalise_address(value)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _retry_schedule(value: str | None, default: tuple[float, ...]) -> tuple[float, ...]:
    if not value:
        return default
    try:
        parsed = tuple(max(0.5, float(part.strip())) for part in value.split(",") if part.strip())
    except ValueError:
        return default
    return parsed or default


class BudsDaemon:
    def __init__(
        self,
        address: str,
        channel: int | None = None,
        verified_channel: int | None = None,
        max_attempts: int = 3,
        auto_connect: bool = False,
        auto_patch: bool = False,
        reconnect_seconds: float = DEFAULT_RECONNECT_SECONDS,
        startup_settle_seconds: float | None = None,
        status_writer: BudsStatusWriter | None = None,
        noise_status_writer: NoiseControlStatusWriter | None = None,
        control_socket_path: Path | None = None,
        session_marker: BudsSessionMarker | None = None,
        audio_policy: HostAudioPolicy | None = None,
    ):
        self.address = _normalise_address(address)
        self.channel = channel
        self.verified_channel = verified_channel
        self.max_attempts = max(1, max_attempts)
        self.auto_connect = auto_connect
        self.auto_patch = auto_patch
        # This is the final steady-state interval.  Short attempts are useful
        # just after a real drop, but retrying an unavailable headset forever
        # every 30 seconds leaves BlueZ profile discovery permanently busy.
        self.reconnect_seconds = max(60.0, reconnect_seconds)
        self._connect_schedule = tuple(
            delay for delay in DEFAULT_CONNECT_BACKOFF if delay < self.reconnect_seconds
        ) + (self.reconnect_seconds,)
        self._status_writer = status_writer
        self._noise_status_writer = noise_status_writer
        self._control_socket_path = control_socket_path
        self._lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._connect_failures = 0
        self._next_connect_at: float | None = None
        self._connected_since: float | None = None
        self._phone_priority_until = 0.0
        self._phone_priority_seconds = max(
            60.0,
            _env_float("GALAXYBRIDGE_BUDS_PHONE_PRIORITY_SECONDS", 600.0),
        )
        self._flap_limit = max(2, _env_int("GALAXYBRIDGE_BUDS_FLAP_LIMIT", 2))
        self._stable_connection_seconds = max(
            10.0,
            _env_float("GALAXYBRIDGE_BUDS_STABLE_CONNECTION_SECONDS", 30.0),
        )
        self._health_check_seconds = max(
            15.0,
            _env_float("GALAXYBRIDGE_BUDS_HEALTH_CHECK_SECONDS", 60.0),
        )
        self._discovery_assist_after = max(
            2,
            _env_int("GALAXYBRIDGE_BUDS_DISCOVERY_ASSIST_AFTER", 3),
        )
        if startup_settle_seconds is None:
            startup_settle_seconds = _env_float("GALAXYBRIDGE_BUDS_STARTUP_SETTLE_SECONDS", 5.0)
        self._startup_settle_seconds = max(0.0, startup_settle_seconds)
        self._patched_for_session = False
        self._last_patch_started = float('-inf')
        self._patch_cooldown_seconds = max(
            30.0, _env_float('GALAXYBRIDGE_BUDS_PATCH_COOLDOWN_SECONDS', 60.0)
        )
        self._worker_active = False
        self._disconnect_generation = 0
        self._disconnect_confirm_seconds = max(
            2.0,
            _env_float("GALAXYBRIDGE_BUDS_DISCONNECT_CONFIRM_SECONDS", 5.0),
        )
        self._stop_event = threading.Event()
        self._reconnect_wakeup = threading.Event()
        self._battery: int | None = None
        self._current_state = "starting"
        self._current_detail = ""
        self._audio_mode = "music"
        self._call_mode_until = 0.0
        self._call_mode_seconds = max(
            60.0,
            _env_float("GALAXYBRIDGE_BUDS_CALL_MODE_SECONDS", 600.0),
        )
        self._audio_change_lock = threading.Lock()
        self._multipoint_stage = "idle"
        self._session_marker = session_marker or BudsSessionMarker(
            max_age_seconds=_env_float("GALAXYBRIDGE_BUDS_SESSION_MARKER_SECONDS", 300.0)
        )
        self._audio_policy = audio_policy or HostAudioPolicy(self.address)
        self._noise_session = WarmNoiseControlSession(
            self.address,
            idle_seconds=_env_float("GALAXYBRIDGE_BUDS_CONTROL_IDLE_SECONDS", 15.0),
            on_mode=self._record_noise_mode,
        )
        self._control_server: BudsControlServer | None = None

    def _set_status(self, state: str, detail: str = "", **fields: object) -> None:
        self._current_state = state
        self._current_detail = detail
        fields.setdefault("audio_mode", self._audio_mode)
        fields.setdefault("multipoint_stage", self._multipoint_stage)
        fields.setdefault("auto_connect", self.auto_connect)
        fields.setdefault("auto_patch", self.auto_patch)
        if self._phone_priority_until > time.monotonic():
            fields.setdefault("phone_priority", True)
            fields.setdefault("phone_priority_seconds", int(self._phone_priority_until - time.monotonic()))
        else:
            fields.setdefault("phone_priority", False)
        if self._battery is not None:
            fields.setdefault("battery", self._battery)
        if self._status_writer is None:
            return
        try:
            self._status_writer.update(state, detail, **fields)
        except OSError as exc:
            LOG.warning("Could not update desktop status: %s", exc)

    def _record_noise_mode(self, mode: NoiseControlMode) -> None:
        if self._noise_status_writer is None:
            return
        try:
            self._noise_status_writer.update("ready", mode=mode.label)
        except OSError as exc:
            LOG.warning("Could not update noise-control status: %s", exc)

    def _on_battery(self, percentage: int | None) -> None:
        self._battery = percentage
        self._set_status(self._current_state, self._current_detail)

    def _on_connected(self, *, force_patch: bool = False) -> None:
        try:
            current = device_state(self.address)
            self._battery = current.battery
        except BlueZError:
            pass
        with self._lock:
            # Cancel a pending "power session ended" confirmation. Short
            # regular-profile drops preserve the RAM-only multipoint value.
            self._disconnect_generation += 1
            if self._connected_since is None:
                self._connected_since = time.monotonic()
            self._next_connect_at = None
            if self._worker_active:
                return
            if self._patched_for_session:
                self._set_status("ready", control_available=True)
                self._reconnect_wakeup.set()
                return
            if not self.auto_patch and not force_patch:
                self._set_status(
                    "connected",
                    "multipoint_restore_required",
                    control_available=True,
                )
                self._reconnect_wakeup.set()
                LOG.info(
                    "Buds connected without a current session marker; automatic SMEP patch is disabled"
                )
                return
            if self._worker_active:
                return
            self._worker_active = True
        self._noise_session.invalidate()
        self._set_status("patching", control_available=False)
        self._reconnect_wakeup.set()
        threading.Thread(target=self._patch_worker, name="buds-patch", daemon=True).start()

    def _on_disconnected(self) -> None:
        self._noise_session.invalidate()
        with self._lock:
            worker_active = self._worker_active
            patched = self._patched_for_session
            if not worker_active:
                self._disconnect_generation += 1
            generation = self._disconnect_generation
        if worker_active:
            # BudsPatcher deliberately releases the regular BlueZ session to
            # open SMEP. This is not a new power session and must not reset the
            # one-write state or start a competing reconnect.
            self._set_status("patching", "regular_session_released", control_available=False)
            LOG.debug("Ignoring expected regular-session disconnect while SMEP patch is active")
            return
        now = time.monotonic()
        with self._lock:
            connected_for = now - self._connected_since if self._connected_since is not None else None
            self._connected_since = None
            if connected_for is not None and connected_for >= self._stable_connection_seconds:
                self._connect_failures = 0
            elif connected_for is not None:
                self._connect_failures += 1
            failure_index = min(self._connect_failures, len(self._connect_schedule) - 1)
            reconnect_delay = self._connect_schedule[failure_index]
            phone_priority = self.auto_connect and self._connect_failures >= self._flap_limit
            if phone_priority:
                self._phone_priority_until = now + self._phone_priority_seconds
                self._next_connect_at = None
        self._set_status("disconnected", control_available=False)
        if phone_priority:
            self._set_status(
                "disconnected",
                "phone_priority",
                control_available=False,
                retry_paused=True,
            )
            LOG.warning(
                "Repeated short Buds connections detected; phone-priority pause enabled for %.0fs",
                self._phone_priority_seconds,
            )
        else:
            self._schedule_host_connect(reconnect_delay)
        if patched:
            # BlueZ can report a short disconnect just after the patcher's
            # standard-profile restore. Confirm that it persists before
            # treating it as a case/power-cycle event and writing again.
            threading.Thread(
                target=self._confirm_disconnected,
                args=(generation, self._disconnect_confirm_seconds),
                name="buds-disconnect-confirm",
                daemon=True,
            ).start()
            LOG.info(
                "Buds disconnected; preserving the verified session for %.0fs before rearming the patch",
                self._disconnect_confirm_seconds,
            )
        else:
            LOG.info("Buds disconnected; waiting for a new power-session candidate")

    def _confirm_disconnected(self, generation: int, delay: float) -> None:
        if self._stop_event.wait(delay):
            return
        try:
            if device_state(self.address).connected:
                return
        except BlueZError:
            pass
        with self._lock:
            if generation != self._disconnect_generation or self._worker_active:
                return
            self._patched_for_session = False
        self._session_marker.clear()
        LOG.info("Buds remained disconnected; multipoint patch rearmed for the next connection")
        self._reconnect_wakeup.set()

    def _schedule_host_connect(self, delay: float, *, reset: bool = False) -> None:
        if not self.auto_connect:
            return
        due = time.monotonic() + max(0.0, delay)
        with self._lock:
            if reset:
                self._connect_failures = 0
            if reset or self._next_connect_at is None or due < self._next_connect_at:
                self._next_connect_at = due
        self._reconnect_wakeup.set()

    def _on_bluez_available_hint(self) -> None:
        """Accelerate a parked retry when BlueZ sees the target again."""

        accelerated = time.monotonic() + 1.0
        with self._lock:
            if self._worker_active or self._next_connect_at is None or self._connect_failures:
                return
            if accelerated >= self._next_connect_at:
                return
            self._next_connect_at = accelerated
        LOG.debug("BlueZ activity for the Buds accelerated the next reconnect attempt")
        self._reconnect_wakeup.set()

    def _attempt_host_connect(self, *, discovery_assist: bool = False) -> bool:
        """Ask BlueZ D-Bus for a standard connection when the patcher is idle."""
        if not self._connect_lock.acquire(blocking=False):
            LOG.debug("A BlueZ connection request is already in progress")
            try:
                return device_state(self.address).connected
            except BlueZError:
                return False
        with self._lock:
            if self._worker_active:
                self._connect_lock.release()
                return False
        try:
            current = device_state(self.address)
            self._battery = current.battery
            if current.connected:
                return True
            self._set_status("connecting", control_available=False)
            LOG.info(
                "Buds are disconnected; asking BlueZ to reconnect %s%s",
                self.address,
                " with a short discovery assist" if discovery_assist else "",
            )
            current = connect_device(
                self.address,
                timeout=12.0,
                discovery_assist=discovery_assist,
                profile_uuid=A2DP_SINK_UUID,
            )
            self._battery = current.battery
            if current.connected:
                LOG.info("BlueZ connection request succeeded")
                # A PropertiesChanged signal may already have started the
                # patch. Avoid overwriting its more precise state.
                with self._lock:
                    active = self._worker_active
                    patched = self._patched_for_session
                if not active:
                    if patched:
                        self._set_status("ready", control_available=True)
                    else:
                        self._on_connected()
                return True
        except BlueZError as exc:
            LOG.debug("BlueZ connection attempt did not succeed: %s", exc)
            self._set_status("disconnected", control_available=False)
            return False
        finally:
            self._connect_lock.release()
        self._set_status("disconnected", control_available=False)
        return False

    def _auto_connect_worker(self) -> None:
        schedule = self._connect_schedule
        LOG.info(
            "Host-initiated BlueZ reconnect enabled (backoff %s; discovery assist from attempt %d)",
            ", ".join(f"{v:.0f}s" for v in schedule),
            self._discovery_assist_after,
        )
        self._schedule_host_connect(0.0, reset=True)
        while not self._stop_event.is_set():
            with self._lock:
                due = self._next_connect_at
            if due is None:
                wait_seconds = self._health_check_seconds
            else:
                wait_seconds = min(self._health_check_seconds, max(0.0, due - time.monotonic()))
            self._reconnect_wakeup.wait(wait_seconds)
            self._reconnect_wakeup.clear()
            if self._stop_event.is_set():
                return

            try:
                connected = device_state(self.address).connected
            except BlueZError:
                connected = False
            if connected:
                with self._lock:
                    if self._connected_since is None:
                        self._connected_since = time.monotonic()
                    if (
                        self._connected_since is not None
                        and time.monotonic() - self._connected_since >= self._stable_connection_seconds
                    ):
                        self._connect_failures = 0
                    self._next_connect_at = None
                    needs_patch = not self._patched_for_session and not self._worker_active
                if needs_patch:
                    self._on_connected()
                continue

            now = time.monotonic()
            with self._lock:
                due = self._next_connect_at
                failures = self._connect_failures
                phone_priority_until = self._phone_priority_until
            if now < phone_priority_until:
                self._set_status(
                    "disconnected",
                    "phone_priority",
                    control_available=False,
                    retry_paused=True,
                )
                continue
            if phone_priority_until:
                with self._lock:
                    self._phone_priority_until = 0.0
                    self._connect_failures = 0
                    failures = 0
            if due is None:
                # Reconcile a disconnect signal that was missed while BlueZ or
                # the adapter restarted without erasing flap backoff.
                self._schedule_host_connect(schedule[min(failures, len(schedule) - 1)])
                continue
            if now < due:
                continue

            attempt_number = failures + 1
            assisted = attempt_number >= self._discovery_assist_after
            success = self._attempt_host_connect(discovery_assist=assisted)
            if success:
                with self._lock:
                    self._next_connect_at = None
                continue

            with self._lock:
                self._connect_failures += 1
                failures = self._connect_failures
                delay = schedule[min(failures - 1, len(schedule) - 1)]
                self._next_connect_at = time.monotonic() + delay
            self._set_status(
                "disconnected",
                "retry_wait",
                reconnect_in=int(delay),
                reconnect_attempt=failures,
                control_available=False,
            )
            LOG.info("BlueZ reconnect did not complete; next attempt in %.0fs", delay)

    def _heartbeat_worker(self) -> None:
        while not self._stop_event.wait(10.0):
            if self._status_writer is None:
                continue
            try:
                self._status_writer.heartbeat()
            except OSError as exc:
                LOG.warning("Could not refresh desktop status: %s", exc)

    def _patch_worker(self) -> None:
        retry_delays = _retry_schedule(
            os.environ.get("GALAXYBRIDGE_BUDS_RETRY_SECONDS"),
            DEFAULT_PATCH_RETRY_DELAYS,
        )
        try:
            for attempt in range(1, self.max_attempts + 1):
                remaining = self._patch_cooldown_seconds - (time.monotonic() - self._last_patch_started)
                if remaining > 0:
                    self._set_status('patching', 'patch_cooldown', control_available=False)
                    LOG.info('Deferring multipoint activation for %.0fs to avoid repeated audio interruptions', remaining)
                    if self._stop_event.wait(remaining):
                        return
                try:
                    if not device_state(self.address).connected:
                        self._set_status('disconnected', control_available=False)
                        return
                except BlueZError:
                    self._set_status('error', 'bluez_monitor_unavailable', control_available=False)
                    return
                self._last_patch_started = time.monotonic()
                LOG.info("Applying MDE_VERSION asVer=2 (attempt %d/%d)", attempt, self.max_attempts)
                try:
                    result = BudsPatcher(
                        self.address,
                        self.channel,
                        verified_channel=self.verified_channel,
                    ).apply()
                except Exception:
                    LOG.exception("Unexpected patch worker failure; stopping this session's attempts")
                    self._set_status("error", "unexpected_patch_failure", control_available=False)
                    return
                if result.success:
                    with self._lock:
                        self._patched_for_session = True
                    if result.reported_as_ver is not None and result.channel is not None:
                        self._session_marker.remember(
                            self.address,
                            result.reported_as_ver,
                            result.channel,
                        )
                    try:
                        current = device_state(self.address)
                        control_available = current.connected
                        self._battery = current.battery
                    except BlueZError:
                        control_available = False
                    LOG.info("MDE_VERSION written and verified, asVer=%s", result.reported_as_ver)
                    self._set_status(
                        "ready",
                        "restore_pending" if result.error else "",
                        as_ver=result.reported_as_ver,
                        channel=result.channel,
                        control_available=control_available,
                    )
                    if result.error:
                        LOG.warning("Patch verified but regular BlueZ restore is pending: %s", result.error)
                    return
                LOG.warning("Buds patch attempt failed: %s", result.error)
                try:
                    control_available = device_state(self.address).connected
                except BlueZError:
                    control_available = False
                self._set_status(
                    "error",
                    "patch_failed",
                    attempt=attempt,
                    control_available=control_available,
                )
                if attempt < self.max_attempts:
                    delay = retry_delays[min(attempt - 1, len(retry_delays) - 1)]
                    LOG.info("Retrying Buds patch in %.1fs", delay)
                    if self._stop_event.wait(delay):
                        return
        finally:
            with self._lock:
                self._worker_active = False
            self._reconnect_wakeup.set()

    def _change_audio_mode(self, mode: str) -> dict[str, object]:
        if mode not in {"music", "call"}:
            raise ValueError("audio mode must be music or call")
        if not self._audio_change_lock.acquire(blocking=False):
            return {"success": False, "mode": self._audio_mode, "error": "an audio-mode change is already active"}
        try:
            self._set_status("ready", "audio_mode_changing", control_available=True, requested_audio_mode=mode)
            result = self._audio_policy.music() if mode == "music" else self._audio_policy.call()
            if result.success:
                self._audio_mode = mode
                self._call_mode_until = time.monotonic() + self._call_mode_seconds if mode == "call" else 0.0
                self._set_status(
                    "ready",
                    "",
                    control_available=True,
                    audio_profile=result.profile,
                    sink_restored=result.sink_restored,
                )
            else:
                self._set_status("error", "audio_mode_failed", control_available=True)
            return dict(result.__dict__)
        finally:
            self._audio_change_lock.release()

    def _control_idle(self) -> None:
        self._noise_session.poll()
        if self._audio_mode != "call" or not self._call_mode_until:
            return
        if time.monotonic() < self._call_mode_until or self._audio_change_lock.locked():
            return
        self._call_mode_until = 0.0
        threading.Thread(
            target=self._change_audio_mode,
            args=("music",),
            name="buds-call-mode-expiry",
            daemon=True,
        ).start()

    def _multipoint_prepare(self) -> dict[str, object]:
        self._noise_session.close()
        with self._lock:
            if self._worker_active:
                return {"success": False, "error": "a Buds operation is active"}
            self._multipoint_stage = "phone_off"
            self._patched_for_session = False
            self._connect_failures = 0
            self._next_connect_at = None
            self._phone_priority_until = time.monotonic() + 3600.0
        self._session_marker.clear()
        try:
            current = disconnect_device(self.address)
        except BlueZError as exc:
            return {"success": False, "stage": self._multipoint_stage, "error": str(exc)}
        self._set_status(
            "disconnected",
            "phone_off_then_activate",
            control_available=False,
            connected=current.connected,
        )
        return {
            "success": not current.connected,
            "stage": self._multipoint_stage,
            "instruction": "disable phone Bluetooth, case-cycle the Buds, then activate multipoint",
            "error": "" if not current.connected else "the PC connection is still active",
        }

    def _multipoint_activate(self) -> dict[str, object]:
        if self._multipoint_stage not in {"phone_off", "pc_restore"}:
            return {"success": False, "stage": self._multipoint_stage, "error": "prepare multipoint first"}
        restore_only = self._multipoint_stage == "pc_restore"
        with self._lock:
            if self._worker_active:
                return {"success": False, "stage": self._multipoint_stage, "error": "a Buds operation is active"}
            self._worker_active = True
        self._multipoint_stage = "activating"
        self._set_status("patching", "multipoint_activating", control_available=False)
        patch_verified = restore_only
        try:
            connected = connect_device(
                self.address,
                discovery_assist=True,
                profile_uuid=A2DP_SINK_UUID,
            )
            if not connected.connected:
                raise BlueZError("the PC could not establish its A2DP link")
            if restore_only:
                remembered = self._session_marker.load(self.address)
                if remembered is None:
                    raise BlueZError("the verified session marker expired; prepare multipoint again")
                reported_as_ver = int(remembered["as_ver"])
                channel = int(remembered["channel"])
                result_error = ""
            else:
                result = BudsPatcher(
                    self.address,
                    self.channel,
                    verified_channel=self.verified_channel,
                ).apply()
                if not result.success or result.reported_as_ver is None or result.channel is None:
                    raise BlueZError(result.error or "the Buds did not verify asVer")
                reported_as_ver = result.reported_as_ver
                channel = result.channel
                result_error = result.error
                self._session_marker.remember(self.address, reported_as_ver, channel)
                patch_verified = True
                with self._lock:
                    self._patched_for_session = True
                    self._phone_priority_until = 0.0

            current = device_state(self.address)
            if not current.connected:
                current = connect_device(
                    self.address,
                    timeout=12.0,
                    discovery_assist=True,
                    profile_uuid=A2DP_SINK_UUID,
                )
            if not current.connected:
                raise BlueZError("asVer is verified, but the PC audio link still needs restoration")
            self._multipoint_stage = "phone_on"
            self._set_status(
                "ready",
                "reconnect_phone_now",
                control_available=True,
                as_ver=reported_as_ver,
                channel=channel,
            )
            return {
                "success": True,
                "stage": self._multipoint_stage,
                "reported_as_ver": reported_as_ver,
                "channel": channel,
                "instruction": "re-enable phone Bluetooth and connect the Buds",
                "error": result_error,
            }
        except Exception as exc:
            self._multipoint_stage = "pc_restore" if patch_verified else "phone_off"
            detail = "pc_audio_restore_required" if patch_verified else "multipoint_activate_failed"
            self._set_status("error", detail, control_available=False)
            return {"success": False, "stage": self._multipoint_stage, "error": str(exc)}
        finally:
            with self._lock:
                self._worker_active = False
            self._reconnect_wakeup.set()

    def _multipoint_done(self) -> dict[str, object]:
        if self._multipoint_stage != "phone_on":
            return {"success": False, "stage": self._multipoint_stage, "error": "multipoint is not awaiting phone confirmation"}
        self._multipoint_stage = "idle"
        self._set_status("ready", "", control_available=True)
        return {"success": True, "stage": self._multipoint_stage, "error": ""}

    def _control_idle_worker(self) -> None:
        """Drive periodic control work independently of the IPC idle callback.

        The call-mode expiry must fire even while the IPC server is busy (for
        example during a multipoint operation) or unavailable, so it is driven by
        this dedicated timer rather than only by IPC accept timeouts.
        """
        while not self._stop_event.wait(1.0):
            self._control_idle()

    def _handle_control_request(self, request: dict[str, object]) -> dict[str, object]:
        requested_address = request.get("address")
        if requested_address and _normalise_address(str(requested_address)) != self.address:
            raise ValueError("The daemon controls a different headset; reconfigure it first")
        action = str(request.get("action", ""))
        if action == "noise_status":
            if self._noise_status_writer is not None:
                self._noise_status_writer.update("reading")
            result = self._noise_session.status()
            self._noise_session.close()
            if self._noise_status_writer is not None:
                if result.success:
                    self._noise_status_writer.update("ready", mode=result.reported_mode)
                else:
                    self._noise_status_writer.update("error", "control_failed")
            return dict(result.__dict__)
        if action == "noise_set":
            mode = NoiseControlMode.from_name(str(request.get("mode", "")))
            if self._noise_status_writer is not None:
                self._noise_status_writer.update("changing", requested_mode=mode.label)
            result = self._noise_session.set_mode(mode)
            self._noise_session.close()
            if self._noise_status_writer is not None:
                if result.success:
                    self._noise_status_writer.update("ready", mode=result.reported_mode)
                else:
                    self._noise_status_writer.update("error", "control_failed", requested_mode=mode.label)
            return dict(result.__dict__)
        if action == "audio_mode_status":
            return {
                "success": True,
                "mode": self._audio_mode,
                "expires_in": max(0, int(self._call_mode_until - time.monotonic())) if self._call_mode_until else 0,
                "error": "",
            }
        if action == "audio_mode_set":
            return self._change_audio_mode(str(request.get("mode", "")))
        if action == "multipoint_prepare":
            return self._multipoint_prepare()
        if action == "multipoint_activate":
            return self._multipoint_activate()
        if action == "multipoint_done":
            return self._multipoint_done()
        if action == "reconnect":
            with self._lock:
                self._connect_failures = 0
                self._next_connect_at = None
                self._phone_priority_until = 0.0
            # A user-requested recovery can immediately use the same short scan
            # that otherwise appears only after repeated automatic failures.
            success = self._attempt_host_connect(discovery_assist=True)
            if success:
                policy = self._audio_policy.music()
                if policy.success:
                    self._audio_mode = "music"
            try:
                current = device_state(self.address)
                return {
                    "success": current.connected,
                    "address": self.address,
                    "connected": current.connected,
                    "battery": current.battery,
                    "error": "" if current.connected else "the Buds are not currently available",
                }
            except BlueZError as exc:
                return {"success": False, "address": self.address, "connected": False, "error": str(exc)}
        raise ValueError("unsupported GalaxyBridge control action")

    def _start_control_server(self) -> None:
        self._control_server = BudsControlServer(
            self._handle_control_request,
            idle=self._control_idle,
            cleanup=self._noise_session.close,
            path=self._control_socket_path,
        )
        self._control_server.start()
        LOG.info("Private Buds control service listening on %s", self._control_server.path)

    def _restore_remembered_session(self) -> bool:
        remembered = self._session_marker.load(self.address)
        if remembered is None:
            return False
        with self._lock:
            self._patched_for_session = True
        LOG.info(
            "Reusing recent verified Buds session asVer=%s channel=%s",
            remembered["as_ver"],
            remembered["channel"],
        )
        self._set_status(
            "ready",
            "session_restored",
            control_available=True,
            as_ver=remembered["as_ver"],
            channel=remembered["channel"],
        )
        return True

    def run(self, once: bool = False) -> int:
        self._set_status("starting", control_available=False)
        if once:
            self._on_connected(force_patch=True)
            while True:
                with self._lock:
                    active = self._worker_active
                if not active:
                    return 0
                time.sleep(0.1)

        previous_term = signal.getsignal(signal.SIGTERM)
        previous_int = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGTERM, lambda _signum, _frame: self._stop_event.set())
        signal.signal(signal.SIGINT, lambda _signum, _frame: self._stop_event.set())
        reconnect_thread: threading.Thread | None = None
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_worker,
            name="buds-status-heartbeat",
            daemon=True,
        )
        control_idle_thread = threading.Thread(
            target=self._control_idle_worker,
            name="buds-control-idle",
            daemon=True,
        )
        try:
            try:
                self._start_control_server()
            except BudsIpcError as exc:
                LOG.warning("Private Buds control service is unavailable: %s", exc)
            heartbeat_thread.start()
            control_idle_thread.start()
            if self._startup_settle_seconds:
                LOG.info(
                    "Waiting %.0fs for BlueZ and WirePlumber to finish startup",
                    self._startup_settle_seconds,
                )
                if self._stop_event.wait(self._startup_settle_seconds):
                    return 0
            current = device_state(self.address)
            self._battery = current.battery
            LOG.info("Watching BlueZ Device1 events for %s", self.address)
            if current.connected:
                self._restore_remembered_session()
                self._on_connected()
            else:
                self._set_status("disconnected", control_available=False)
            if self.auto_connect:
                reconnect_thread = threading.Thread(
                    target=self._auto_connect_worker,
                    name="buds-auto-connect",
                    daemon=True,
                )
                reconnect_thread.start()
            run_device_monitor(
                self.address,
                lambda connected: self._on_connected() if connected else self._on_disconnected(),
                self._on_battery,
                self._stop_event.is_set,
                self._on_bluez_available_hint,
            )
        except BlueZError as exc:
            LOG.error("cannot monitor BlueZ: %s", exc)
            self._set_status("error", "bluez_monitor_unavailable", control_available=False)
            return 2
        finally:
            self._stop_event.set()
            self._reconnect_wakeup.set()
            if self._control_server is not None:
                self._control_server.stop()
                self._control_server = None
            if reconnect_thread is not None:
                reconnect_thread.join(timeout=1.0)
            if control_idle_thread.is_alive():
                control_idle_thread.join(timeout=1.0)
            if heartbeat_thread.is_alive():
                heartbeat_thread.join(timeout=1.0)
            self._set_status("stopped", control_available=False)
            signal.signal(signal.SIGTERM, previous_term)
            signal.signal(signal.SIGINT, previous_int)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GalaxyBridge Buds user daemon")
    parser.add_argument("--address", default=os.environ.get("GALAXYBRIDGE_BUDS_ADDRESS"))
    parser.add_argument(
        "--channel",
        type=int,
        default=_env_int("GALAXYBRIDGE_BUDS_CHANNEL", 0) if os.environ.get("GALAXYBRIDGE_BUDS_CHANNEL") else None,
    )
    parser.add_argument(
        "--verified-channel",
        type=int,
        default=(
            _env_int("GALAXYBRIDGE_BUDS_VERIFIED_CHANNEL", 0)
            if os.environ.get("GALAXYBRIDGE_BUDS_VERIFIED_CHANNEL")
            else None
        ),
        help="reuse a channel previously confirmed by exact SMEP discovery and asVer readback",
    )
    parser.add_argument(
        "--auto-connect",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("GALAXYBRIDGE_BUDS_AUTO_CONNECT"),
        help="ask BlueZ to reconnect with bounded progressive backoff",
    )
    parser.add_argument(
        "--auto-patch",
        action=argparse.BooleanOptionalAction,
        default=_env_flag("GALAXYBRIDGE_BUDS_AUTO_PATCH"),
        help="apply SMEP automatically on an unmarked connection (off by default)",
    )
    parser.add_argument(
        "--reconnect-seconds",
        type=float,
        default=_env_float("GALAXYBRIDGE_BUDS_RECONNECT_SECONDS", DEFAULT_RECONNECT_SECONDS),
        help="steady-state delay after the initial 3s, 8s, 20s, 60s and 180s reconnect attempts",
    )
    parser.add_argument("--once", action="store_true", help="apply once and exit; useful for hardware validation")
    args = parser.parse_args(argv)
    if not args.address:
        parser.error("--address or GALAXYBRIDGE_BUDS_ADDRESS is required")
    logging.basicConfig(
        level=os.environ.get("GALAXYBRIDGE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return BudsDaemon(
        args.address,
        args.channel,
        verified_channel=args.verified_channel,
        auto_connect=args.auto_connect,
        auto_patch=args.auto_patch,
        max_attempts=1,
        reconnect_seconds=args.reconnect_seconds,
        status_writer=BudsStatusWriter(),
        noise_status_writer=NoiseControlStatusWriter(),
    ).run(args.once)


if __name__ == "__main__":
    raise SystemExit(main())
