import builtins
import os

import pytest


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="graphical display required")
def test_panel_can_construct_and_close():
    pytest.importorskip("gi")
    from galaxybridge.desktop import build_panel

    panel = build_panel()
    try:
        assert panel.window.get_title() == "GalaxyBridge"
        assert panel.window.get_children()
        assert set(panel.noise_buttons) == {"anc", "ambient", "off"}
        assert set(panel.audio_buttons) == {"music", "call"}
    finally:
        panel.window.destroy()


def test_panel_reports_missing_bindings(monkeypatch, capsys):
    """Without GTK the panel explains which distribution package is missing."""
    import galaxybridge.desktop as desktop

    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "gi":
            raise ImportError("no gi")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert desktop.main() == 2
    assert "GTK 3" in capsys.readouterr().err
