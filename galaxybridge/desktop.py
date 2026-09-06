"""Optional Tk control panel for GNOME, KDE, Xfce and other Linux desktops."""
import queue
import subprocess
import sys
import threading


def main() -> int:
    try:
        import tkinter as tk
        from tkinter import ttk
    except ImportError:
        print('Install your distribution\'s Python Tk package (e.g. python3-tk).', file=sys.stderr)
        return 2
    try:
        window = tk.Tk()
    except tk.TclError as exc:
        print(f'Graphical session unavailable: {exc}', file=sys.stderr)
        return 2
    window.title('GalaxyBridge')
    window.geometry('680x620')
    pane = ttk.Frame(window, padding=16)
    pane.pack(fill='both', expand=True)
    ttk.Label(pane, text='Galaxy Buds · Linux controls', font=('', 16)).pack(anchor='w')
    ttk.Label(pane, text='Device Bluetooth address (or leave blank to use configuration)').pack(anchor='w', pady=(12, 0))
    address = ttk.Entry(pane)
    address.pack(fill='x')
    messages = queue.Queue()
    buttons = []
    output = tk.Text(pane, height=12, wrap='word')

    def execute(args):
        argv = [sys.executable, '-m', 'galaxybridge.cli', 'buds', *args]
        if address.get().strip():
            argv += ['--address', address.get().strip()]
        for button in buttons:
            button.configure(state='disabled')
        def worker():
            try:
                result = subprocess.run(argv, capture_output=True, text=True, timeout=190)
                messages.put(result.stdout + result.stderr)
            except (OSError, subprocess.TimeoutExpired) as exc:
                messages.put(str(exc) + '\nOperation outcome unknown; inspect daemon status before retrying.')
        threading.Thread(target=worker, daemon=True).start()

    for label, args in [
        ('Inspect capabilities (read only)', ['capabilities']),
        ('Connect PC audio once', ['connect']),
        ('Read noise mode', ['noise-control', 'status']),
        ('Noise cancellation', ['noise-control', 'set', 'anc']),
        ('Ambient sound', ['noise-control', 'set', 'ambient']),
        ('Noise control off', ['noise-control', 'set', 'off']),
        ('Music profile (PipeWire)', ['audio-mode', 'set', 'music']),
    ]:
        button = ttk.Button(pane, text=label, command=lambda a=args: execute(a))
        button.pack(fill='x', pady=2)
        buttons.append(button)
    ttk.Label(pane, text='Experimental multipoint · requires a running GalaxyBridge daemon.\n1. Prepare disconnects the PC. Disable phone Bluetooth and case-cycle Buds.\n2. Activate; wait for success. 3. Reconnect phone, then confirm.', wraplength=640).pack(anchor='w', pady=8)
    row = ttk.Frame(pane)
    row.pack(fill='x')
    for label, action in [('1. Prepare', 'prepare'), ('2. Activate', 'activate'), ('3. Confirm phone', 'done')]:
        button = ttk.Button(row, text=label, command=lambda a=action: execute(['multipoint', a]))
        button.pack(side='left', expand=True, fill='x')
        buttons.append(button)
    output.pack(fill='both', expand=True, pady=8)
    def poll():
        try:
            message = messages.get_nowait()
        except queue.Empty:
            pass
        else:
            output.delete('1.0', 'end')
            output.insert('end', message)
            for button in buttons:
                button.configure(state='normal')
        window.after(100, poll)
    poll()
    window.mainloop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
