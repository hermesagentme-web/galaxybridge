"""Interactive terminal menu for desktops without a panel and for TTYs.

Every action dispatches into the regular CLI so the menu can never drift from
the documented commands and their JSON output.
"""
from __future__ import annotations

import sys

from .i18n import t


def _banner() -> None:
    line = "━" * 46
    print(f"\n{line}\n  {t('menu.heading')}\n{line}")


def _entries() -> list[tuple[str, str, list[str]]]:
    return [
        ("1", t("menu.status"), ["buds", "status"]),
        ("2", t("menu.anc"), ["buds", "noise-control", "set", "anc"]),
        ("3", t("menu.ambient"), ["buds", "noise-control", "set", "ambient"]),
        ("4", t("menu.off"), ["buds", "noise-control", "set", "off"]),
        ("5", t("menu.music"), ["buds", "audio-mode", "set", "music"]),
        ("6", t("menu.call"), ["buds", "audio-mode", "set", "call"]),
        ("7", t("menu.connect"), ["buds", "connect"]),
        ("8", t("menu.multipoint"), ["__multipoint__"]),
        ("9", t("menu.doctor"), ["doctor"]),
        ("10", t("menu.service"), ["__service__"]),
        ("i", t("menu.capabilities"), ["buds", "capabilities"]),
        ("q", t("menu.quit"), ["__quit__"]),
    ]


def _multipoint_menu(dispatch) -> None:
    steps = [
        ("1", t("menu.multipoint.1"), ["buds", "multipoint", "prepare"]),
        ("2", t("menu.multipoint.2"), ["buds", "multipoint", "activate"]),
        ("3", t("menu.multipoint.3"), ["buds", "multipoint", "done"]),
        ("b", t("menu.multipoint.back"), None),
    ]
    while True:
        print(f"\n  {t('menu.next_step')}")
        for key, label, _args in steps:
            print(f"  [{key}] {label}")
        choice = input(f"{t('menu.prompt')} ▸ ").strip().lower()
        selected = next((args for key, _label, args in steps if key == choice), "invalid")
        if selected == "invalid":
            if choice == "b":
                return
            print(t("menu.invalid"))
            continue
        if selected is None:
            return
        dispatch(selected)
        return


def _service_menu(dispatch) -> None:
    import subprocess

    print(f"  [1] {t('device.service_start')}\n  [2] {t('device.service_stop')}")
    choice = input(f"{t('menu.prompt')} ▸ ").strip()
    action = {"1": "start", "2": "stop"}.get(choice)
    if action is None:
        print(t("menu.invalid"))
        return
    result = subprocess.run(
        ["systemctl", "--user", action, "galaxybridge-buds.service"],
        capture_output=True,
        text=True,
        timeout=45,
    )
    output = (result.stdout + result.stderr).strip()
    if output:
        print(output)
    print(t("done") if result.returncode == 0 else t("state.error"))


def run() -> int:
    """Run the interactive menu until the user quits."""
    from . import cli

    if not sys.stdin.isatty():
        print(t("menu.no_tty"), file=sys.stderr)
        return 2

    def dispatch(args: list[str]) -> None:
        print()
        cli.main(args)

    while True:
        _banner()
        for key, label, _args in _entries():
            print(f"  [{key:>2}] {label}")
        choice = input(f"\n{t('menu.prompt')} ▸ ").strip().lower()
        selected = next((args for key, _label, args in _entries() if key == choice), None)
        if selected is None:
            print(t("menu.invalid"))
            continue
        if selected == ["__quit__"]:
            return 0
        if selected == ["__multipoint__"]:
            _multipoint_menu(dispatch)
            continue
        if selected == ["__service__"]:
            _service_menu(dispatch)
            continue
        dispatch(selected)
