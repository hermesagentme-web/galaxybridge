import io
import sys

from galaxybridge import cli, tui
from galaxybridge.i18n import STRINGS, current_language, t


class FakeTty(io.StringIO):
    def isatty(self):
        return True


def test_menu_requires_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("q\n"))
    assert tui.run() == 2


def test_menu_dispatches_cli_and_quits(monkeypatch):
    executed = []
    monkeypatch.setattr(cli, "main", lambda args: executed.append(list(args)) or 0)
    monkeypatch.setattr(sys, "stdin", FakeTty("1\nq\n"))
    assert tui.run() == 0
    assert executed == [["buds", "status"]]


def test_menu_rejects_unknown_choice_then_quits(monkeypatch, capsys):
    executed = []
    monkeypatch.setattr(cli, "main", lambda args: executed.append(list(args)) or 0)
    monkeypatch.setattr(sys, "stdin", FakeTty("nope\nq\n"))
    assert tui.run() == 0
    assert executed == []
    assert t("menu.invalid") in capsys.readouterr().out


def test_menu_multipoint_wizard(monkeypatch):
    executed = []
    monkeypatch.setattr(cli, "main", lambda args: executed.append(list(args)) or 0)
    monkeypatch.setattr(sys, "stdin", FakeTty("8\n1\nq\n"))
    assert tui.run() == 0
    assert executed == [["buds", "multipoint", "prepare"]]


def test_language_follows_locale(monkeypatch):
    monkeypatch.setenv("GALAXYBRIDGE_LANG", "fr")
    assert current_language() == "fr"
    assert t("menu.quit") == STRINGS["fr"]["menu.quit"]
    monkeypatch.setenv("GALAXYBRIDGE_LANG", "en")
    assert t("menu.quit") == STRINGS["en"]["menu.quit"]
    monkeypatch.delenv("GALAXYBRIDGE_LANG")
    monkeypatch.setenv("LANG", "fr_CH.UTF-8")
    monkeypatch.setattr("locale.getlocale", lambda: ("fr_CH", "UTF-8"))
    assert current_language() == "fr"
