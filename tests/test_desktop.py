import os
import pytest


@pytest.mark.skipif(not os.environ.get('DISPLAY'), reason='graphical display required')
def test_panel_can_construct_and_close(monkeypatch):
    tk = pytest.importorskip('tkinter')
    from galaxybridge.desktop import main
    def close(window):
        window.update_idletasks()
        assert window.title() == 'GalaxyBridge'
        assert window.winfo_children()
        window.destroy()
    monkeypatch.setattr(tk.Tk, 'mainloop', close)
    assert main() == 0
