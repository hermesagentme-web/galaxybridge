"""Small BlueZ D-Bus helpers used by the Buds daemon.

Only normal host-side Bluetooth connection management and read-only device
properties live here.  No vendor payload is sent by this module.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable


class BlueZError(RuntimeError):
    pass


A2DP_SINK_UUID = "0000110b-0000-1000-8000-00805f9b34fb"


def normalise_address(value: str) -> str:
    return value.replace("_", ":").upper()


@dataclass(frozen=True)
class BlueZDeviceState:
    address: str
    path: str
    connected: bool
    battery: int | None = None
    name: str = ""


def _dbus_modules():
    try:
        import dbus
        import dbus.mainloop.glib
        from gi.repository import GLib
    except ImportError as exc:
        raise BlueZError("Python D-Bus support is missing (install python3-dbus and python3-gi)") from exc
    return dbus, dbus.mainloop.glib, GLib


def _managed_device(bus: object, address: str) -> tuple[str, dict[str, object], dict[str, object]]:
    dbus, _dbus_glib, _glib = _dbus_modules()
    manager = dbus.Interface(
        bus.get_object("org.bluez", "/"),
        "org.freedesktop.DBus.ObjectManager",
    )
    target = normalise_address(address)
    for path, interfaces in manager.GetManagedObjects().items():
        device = interfaces.get("org.bluez.Device1")
        if device and normalise_address(str(device.get("Address", ""))) == target:
            battery = interfaces.get("org.bluez.Battery1", {})
            return str(path), dict(device), dict(battery)
    raise BlueZError(f"Buds {target} are not known to BlueZ")


def device_state(address: str, bus: object | None = None) -> BlueZDeviceState:
    dbus, _dbus_glib, _glib = _dbus_modules()
    try:
        system_bus = bus or dbus.SystemBus()
        path, device, battery = _managed_device(system_bus, address)
    except BlueZError:
        raise
    except Exception as exc:
        raise BlueZError(f"could not read BlueZ device state: {exc}") from exc
    percentage = battery.get("Percentage")
    return BlueZDeviceState(
        address=normalise_address(address),
        path=path,
        connected=bool(device.get("Connected", False)),
        battery=int(percentage) if percentage is not None else None,
        name=str(device.get("Alias") or device.get("Name") or ""),
    )


def _wait_for_device_state(
    address: str,
    bus: object,
    wanted_connected: bool,
    seconds: float = 1.5,
) -> BlueZDeviceState:
    """Reconcile an ambiguous D-Bus result against Device1.Connected.

    A client-side D-Bus timeout does not prove that BlueZ abandoned the
    operation.  Give the authoritative property a short window to settle so a
    successful late connection is not followed by another competing request.
    """

    deadline = time.monotonic() + max(0.0, seconds)
    latest = device_state(address, bus)
    while latest.connected != wanted_connected and time.monotonic() < deadline:
        time.sleep(0.15)
        latest = device_state(address, bus)
    return latest


def _device_method(
    address: str,
    method: str,
    timeout: float = 12.0,
    bus: object | None = None,
    profile_uuid: str | None = None,
) -> BlueZDeviceState:
    dbus, _dbus_glib, _glib = _dbus_modules()
    try:
        system_bus = bus or dbus.SystemBus()
    except Exception as exc:
        raise BlueZError(f"could not connect to BlueZ D-Bus: {exc}") from exc
    state = device_state(address, system_bus)
    device = dbus.Interface(system_bus.get_object("org.bluez", state.path), "org.bluez.Device1")
    dbus_method = "ConnectProfile" if method == "Connect" and profile_uuid else method
    method_args = (profile_uuid,) if profile_uuid else ()
    try:
        getattr(device, dbus_method)(*method_args, timeout=timeout)
    except Exception as exc:
        if profile_uuid:
            # Connected can describe a vendor/HFP link, not the requested A2DP.
            raise BlueZError(f"BlueZ {dbus_method} {profile_uuid} failed: {exc}") from exc
        # A connection can complete just before BlueZ returns InProgress or a
        # client-side timeout.  The property is authoritative in that case.
        try:
            after = _wait_for_device_state(address, system_bus, method == "Connect")
        except BlueZError:
            after = state
        wanted = method == "Connect"
        if after.connected == wanted:
            return after
        operation = f"{method.lower()} profile {profile_uuid}" if profile_uuid else method.lower()
        raise BlueZError(f"BlueZ {operation} failed: {exc}") from exc
    return device_state(address, system_bus)


def connect_device(
    address: str,
    timeout: float = 12.0,
    *,
    discovery_assist: bool = False,
    discovery_seconds: float = 2.0,
    profile_uuid: str | None = None,
) -> BlueZDeviceState:
    state = device_state(address)
    if state.connected:
        if profile_uuid:
            # Device1.Connected may be true for HFP or a vendor control link
            # while A2DP is still absent. ConnectProfile is idempotent and
            # makes the requested music profile explicit.
            return _device_method(address, "Connect", timeout, profile_uuid=profile_uuid)
        return state
    if not discovery_assist:
        return _device_method(address, "Connect", timeout, profile_uuid=profile_uuid)

    # Some dual-mode Galaxy Buds only become reconnectable after BlueZ has
    # refreshed their BR/EDR/LE identity during discovery.  Keep this recovery
    # local, short and scoped to a private D-Bus client.  Closing the client also
    # guarantees its discovery session cannot remain active.
    dbus, _dbus_glib, _glib = _dbus_modules()
    try:
        bus = dbus.SystemBus(private=True)
    except Exception as exc:
        raise BlueZError(f"could not connect to BlueZ D-Bus: {exc}") from exc
    discovery_started = False
    try:
        state = device_state(address, bus)
        adapter_path = state.path.rsplit("/dev_", 1)[0]
        adapter = dbus.Interface(bus.get_object("org.bluez", adapter_path), "org.bluez.Adapter1")
        try:
            adapter.SetDiscoveryFilter({"Transport": "auto"}, timeout=3.0)
            adapter.StartDiscovery(timeout=3.0)
            discovery_started = True
        except Exception:
            # Discovery may already be active for GNOME.  A normal connection
            # attempt is still safe and useful in that case.
            pass

        deadline = time.monotonic() + max(0.0, discovery_seconds)
        while time.monotonic() < deadline:
            current = device_state(address, bus)
            if current.connected:
                return current
            time.sleep(0.2)
        return _device_method(address, "Connect", timeout, bus=bus, profile_uuid=profile_uuid)
    finally:
        if discovery_started:
            try:
                adapter.StopDiscovery(timeout=3.0)
            except Exception:
                pass
        try:
            bus.close()
        except Exception:
            pass


def disconnect_device(address: str, timeout: float = 8.0) -> BlueZDeviceState:
    state = device_state(address)
    if not state.connected:
        return state
    return _device_method(address, "Disconnect", timeout)


def _decode_properties_changed(args: tuple[object, ...]) -> tuple[str, dict[object, object]] | None:
    """Accept both standard and shortened dbus-python signal deliveries."""

    if len(args) < 2:
        return None
    try:
        return str(args[0]), dict(args[1])
    except (TypeError, ValueError):
        return None


def run_device_monitor(
    address: str,
    on_connected: Callable[[bool], None],
    on_battery: Callable[[int | None], None],
    should_stop: Callable[[], bool],
    on_available: Callable[[], None] | None = None,
) -> None:
    """Run a native BlueZ property monitor until ``should_stop`` is true."""
    dbus, dbus_glib, GLib = _dbus_modules()
    dbus_glib.DBusGMainLoop(set_as_default=True)
    # Use a private connection created after installing the GLib main loop;
    # an earlier synchronous SystemBus connection may otherwise be cached
    # without signal dispatch support.
    bus = dbus.SystemBus(private=True)
    state = device_state(address, bus)
    target_path = state.path
    loop = GLib.MainLoop()

    def properties_changed(*args: object, path: object = None, **_kwargs: object) -> None:
        if str(path) != target_path:
            return
        decoded = _decode_properties_changed(args)
        if decoded is None:
            return
        interface, values = decoded
        if interface == "org.bluez.Device1" and "Connected" in values:
            on_connected(bool(values["Connected"]))
        elif interface == "org.bluez.Battery1" and "Percentage" in values:
            on_battery(int(values["Percentage"]))
        if on_available is not None and (
            interface == "org.bluez.Battery1"
            or any(name in values for name in ("RSSI", "ServicesResolved", "UUIDs"))
        ):
            on_available()

    def interfaces_added(path: object, interfaces: object) -> None:
        if str(path) != target_path:
            return
        values = dict(interfaces)
        device = values.get("org.bluez.Device1")
        if device and "Connected" in device:
            on_connected(bool(device["Connected"]))
        battery = values.get("org.bluez.Battery1")
        if battery and "Percentage" in battery:
            on_battery(int(battery["Percentage"]))
        if on_available is not None:
            on_available()

    def interfaces_removed(path: object, interfaces: object) -> None:
        if str(path) != target_path:
            return
        removed = {str(interface) for interface in interfaces}
        if "org.bluez.Battery1" in removed:
            on_battery(None)
        if "org.bluez.Device1" in removed:
            on_connected(False)

    bus.add_signal_receiver(
        properties_changed,
        signal_name="PropertiesChanged",
        dbus_interface="org.freedesktop.DBus.Properties",
        path_keyword="path",
    )
    bus.add_signal_receiver(
        interfaces_added,
        signal_name="InterfacesAdded",
        dbus_interface="org.freedesktop.DBus.ObjectManager",
    )
    bus.add_signal_receiver(
        interfaces_removed,
        signal_name="InterfacesRemoved",
        dbus_interface="org.freedesktop.DBus.ObjectManager",
    )

    source_active = True

    def check_stop() -> bool:
        nonlocal source_active
        if should_stop():
            source_active = False
            loop.quit()
            return False
        return True

    source_id = GLib.timeout_add(500, check_stop)
    try:
        loop.run()
    finally:
        if source_active:
            GLib.source_remove(source_id)
        bus.remove_signal_receiver(
            properties_changed,
            signal_name="PropertiesChanged",
            dbus_interface="org.freedesktop.DBus.Properties",
        )
        bus.remove_signal_receiver(
            interfaces_added,
            signal_name="InterfacesAdded",
            dbus_interface="org.freedesktop.DBus.ObjectManager",
        )
        bus.remove_signal_receiver(
            interfaces_removed,
            signal_name="InterfacesRemoved",
            dbus_interface="org.freedesktop.DBus.ObjectManager",
        )
        try:
            bus.close()
        except Exception:
            pass
