"""GTK 3 control panel for GNOME, KDE, Xfce, Cinnamon, MATE and friends.

The panel follows the active GTK theme (Adwaita, Breeze, …) including its
light/dark preference; CSS is limited to spacing and radius tweaks so the
desktop theme stays in charge. Labels follow the session locale (fr/en).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .i18n import t

CSS = b"""
.card {
  border-radius: 12px;
  padding: 2px;
  background-color: @theme_base_color;
  border: 1px solid alpha(@theme_fg_color, 0.12);
}
.card-title { font-weight: bold; }
.dim { opacity: 0.72; }
.mono { font-family: monospace; }
"""

NOISE_MODES = (("anc", "noise.anc"), ("ambient", "noise.ambient"), ("off", "noise.off"))
AUDIO_MODES = (("music", "audio.music"), ("call", "audio.call"))


def _cache_path(name: str) -> Path:
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_home / "galaxybridge" / name


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Panel:
    """One window; every action shells out to galaxybridgectl in a thread."""

    def __init__(self):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, GLib, Gdk

        self.gtk = Gtk
        self.glib = GLib
        self._syncing = False
        self._busy = False

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.window = Gtk.Window(title=t("app.title"), default_width=520, default_height=700)

        header = Gtk.HeaderBar(show_close_button=True, title=t("app.title"))
        self.header = header
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        refresh.set_tooltip_text(t("device.refresh"))
        refresh.connect("clicked", lambda _b: self.refresh_state())
        header.pack_end(refresh)
        self.window.set_titlebar(header)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(14)
        root.set_margin_bottom(14)
        root.set_margin_start(14)
        root.set_margin_end(14)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(root)
        self.window.add(scrolled)

        self.action_buttons = []
        self._build_device_card(root)
        self._build_noise_card(root)
        self._build_audio_card(root)
        self._build_multipoint_card(root)
        self._build_diagnostics_card(root)

        self.glib.timeout_add_seconds(5, self.refresh_state)
        self.refresh_state()

    # -- layout helpers -------------------------------------------------

    def _card(self, parent, title: str):
        Gtk = self.gtk
        frame = Gtk.Frame()
        frame.get_style_context().add_class("card")
        frame.set_shadow_type(Gtk.ShadowType.NONE)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        if title:
            label = Gtk.Label(label=title, xalign=0)
            label.get_style_context().add_class("card-title")
            box.pack_start(label, False, False, 0)
        frame.add(box)
        parent.pack_start(frame, False, False, 0)
        return box

    def _button(self, box, label: str, callback, expand: bool = False):
        button = self.gtk.Button(label=label)
        button.connect("clicked", callback)
        box.pack_start(button, expand, expand, 0)
        self.action_buttons.append(button)
        return button

    def _dim_label(self, text: str = ""):
        label = self.gtk.Label(label=text, xalign=0)
        label.get_style_context().add_class("dim")
        label.set_line_wrap(True)
        return label

    # -- cards ----------------------------------------------------------

    def _build_device_card(self, root):
        Gtk = self.gtk
        box = self._card(root, t("device.title"))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        icon = Gtk.Image.new_from_icon_name("audio-headphones-symbolic", Gtk.IconSize.DIALOG)
        row.pack_start(icon, False, False, 0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.state_label = Gtk.Label(label="", xalign=0)
        self.battery_label = self._dim_label()
        text.pack_start(self.state_label, False, False, 0)
        text.pack_start(self.battery_label, False, False, 0)
        row.pack_start(text, True, True, 0)
        box.pack_start(row, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.connect_button = self._button(actions, t("device.connect"), lambda _b: self._connect())
        self.service_button = self._button(actions, "", lambda _b: self._toggle_service())
        box.pack_start(actions, False, False, 0)

    def _build_noise_card(self, root):
        Gtk = self.gtk
        box = self._card(root, t("noise.title"))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.get_style_context().add_class("linked")
        self.noise_buttons: dict[str, Any] = {}
        group = None
        for mode, key in NOISE_MODES:
            button = Gtk.RadioButton.new_with_label_from_widget(group, t(key))
            button.set_mode(False)  # segmented push-button look, not radio dots
            button.connect("toggled", self._on_noise_toggled, mode)
            row.pack_start(button, True, True, 0)
            self.noise_buttons[mode] = button
            group = button
        box.pack_start(row, False, False, 0)
        self._button(box, t("noise.read"), lambda _b: self._run_cli(["buds", "noise-control", "status"]))
        self.noise_hint = self._dim_label(t("noise.unknown"))
        box.pack_start(self.noise_hint, False, False, 0)

    def _build_audio_card(self, root):
        Gtk = self.gtk
        box = self._card(root, t("audio.title"))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        row.get_style_context().add_class("linked")
        self.audio_buttons: dict[str, Any] = {}
        group = None
        for mode, key in AUDIO_MODES:
            button = Gtk.RadioButton.new_with_label_from_widget(group, t(key))
            button.set_mode(False)
            button.connect("toggled", self._on_audio_toggled, mode)
            row.pack_start(button, True, True, 0)
            self.audio_buttons[mode] = button
            group = button
        box.pack_start(row, False, False, 0)
        box.pack_start(self._dim_label(t("audio.call_warning")), False, False, 0)

    def _build_multipoint_card(self, root):
        Gtk = self.gtk
        box = self._card(root, t("multipoint.title"))
        box.pack_start(self._dim_label(t("multipoint.hint")), False, False, 0)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._button(row, t("multipoint.prepare"), lambda _b: self._run_cli(["buds", "multipoint", "prepare"]), expand=True)
        self._button(row, t("multipoint.activate"), lambda _b: self._run_cli(["buds", "multipoint", "activate"]), expand=True)
        self._button(row, t("multipoint.done"), lambda _b: self._run_cli(["buds", "multipoint", "done"]), expand=True)
        box.pack_start(row, False, False, 0)

    def _build_diagnostics_card(self, root):
        Gtk = self.gtk
        box = self._card(root, t("diagnostics.title"))
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._button(row, t("diagnostics.capabilities"), lambda _b: self._run_cli(["buds", "capabilities"]), expand=True)
        self._button(row, t("diagnostics.doctor"), lambda _b: self._run_cli(["doctor"]), expand=True)
        box.pack_start(row, False, False, 0)

        self.output_revealer = Gtk.Revealer()
        self.output_view = Gtk.TextView(editable=False, cursor_visible=False)
        self.output_view.set_monospace(True)
        self.output_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scroll = Gtk.ScrolledWindow()
        scroll.set_min_content_height(180)
        scroll.add(self.output_view)
        self.output_revealer.add(scroll)
        box.pack_start(self.output_revealer, False, False, 0)

    # -- actions --------------------------------------------------------

    def _set_busy(self, busy: bool):
        self._busy = busy
        for button in self.action_buttons:
            button.set_sensitive(not busy)
        for group in (self.noise_buttons, self.audio_buttons):
            for button in group.values():
                button.set_sensitive(not busy)
        self.header.set_subtitle(t("busy") if busy else None)

    def _run_cli(self, args: list[str], notify=None):
        if self._busy:
            return
        self._set_busy(True)

        def worker():
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "galaxybridge.cli", *args],
                    capture_output=True, text=True, timeout=190,
                )
                text = (result.stdout + result.stderr).strip()
            except (OSError, subprocess.TimeoutExpired) as exc:
                text = f"{exc}\nOperation outcome unknown; inspect daemon status before retrying."
            self.glib.idle_add(self._finish, text, notify)

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, text: str, notify):
        self._set_busy(False)
        if text:
            self.output_view.get_buffer().set_text(text)
            self.output_revealer.set_reveal_child(True)
        if notify:
            notify(text)
        self.refresh_state()
        return False

    def _connect(self):
        self._run_cli(["buds", "connect"])

    def _toggle_service(self):
        if self._busy:
            return
        self._set_busy(True)
        action = "stop" if self.service_active else "start"

        def worker():
            result = subprocess.run(
                ["systemctl", "--user", action, "galaxybridge-buds.service"],
                capture_output=True, text=True, timeout=45,
            )
            self.glib.idle_add(self._finish, (result.stdout + result.stderr).strip(), None)

        threading.Thread(target=worker, daemon=True).start()

    def _on_noise_toggled(self, button, mode: str):
        if self._syncing or not button.get_active():
            return
        self._run_cli(["buds", "noise-control", "set", mode])

    def _on_audio_toggled(self, button, mode: str):
        if self._syncing or not button.get_active():
            return
        self._run_cli(["buds", "audio-mode", "set", mode])

    # -- state ----------------------------------------------------------

    def refresh_state(self, *_args):
        Gtk = self.gtk
        status = _read_json(_cache_path("buds-status.json"))
        noise = _read_json(_cache_path("noise-control.json"))
        self.service_active = status.get("state") not in (None, "stopped")

        state = status.get("state") or "stopped"
        self.state_label.set_text(t(f"state.{state}") if f"state.{state}" in self._state_keys() else t("state.unknown"))
        battery = status.get("battery")
        self.battery_label.set_text(t("device.battery", value=battery) if isinstance(battery, int) else "")
        self.service_button.set_label(t("device.service_stop") if self.service_active else t("device.service_start"))

        self._syncing = True
        try:
            mode = noise.get("mode") if noise.get("state") == "ready" else None
            for name, button in self.noise_buttons.items():
                button.set_active(name == mode)
            self.noise_hint.set_text(t(f"noise.{mode}") if mode else t("noise.unknown"))
            audio_mode = status.get("audio_mode") or "music"
            for name, button in self.audio_buttons.items():
                button.set_active(name == audio_mode)
        finally:
            self._syncing = False
        return True

    def _state_keys(self):
        from .i18n import STRINGS
        return STRINGS["en"]


def build_panel() -> Panel:
    return Panel()


def main() -> int:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
    except Exception:
        print(
            "Install your distribution's GTK 3 bindings first "
            "(e.g. gir1.2-gtk-3.0, gtk3 or typelib-1_0-Gtk-3_0).",
            file=sys.stderr,
        )
        return 2
    panel = build_panel()
    panel.window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
