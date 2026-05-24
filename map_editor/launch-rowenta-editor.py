#!/usr/bin/env python3
"""
Clickable launcher for the Rowenta map editor proxy.

Double-click it to open a small native launcher, or run it from a terminal with
the same arguments as rowenta-editor-server.py.
"""

import argparse
import queue
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError:
    tk = None
    messagebox = None
    ttk = None


SERVER_SCRIPT = Path(__file__).with_name("rowenta-editor-server.py")
DEFAULT_PORT = 8765


class EditorLauncher(tk.Tk if tk else object):
    def __init__(self):
        tk.Tk.__init__(self)
        self.title("Rowenta Map Editor")
        self.resizable(False, False)
        self.process = None
        self.output_queue = queue.Queue()

        self.robot_ip = tk.StringVar()
        self.port = tk.StringVar(value=str(DEFAULT_PORT))
        self.status = tk.StringVar(value="Ready")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._poll_output)

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        frame = ttk.Frame(self, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="Robot IP").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.robot_ip, width=28).grid(
            row=0, column=1, columnspan=2, sticky="ew", **pad
        )

        ttk.Label(frame, text="Port").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(frame, textvariable=self.port, width=10).grid(
            row=1, column=1, sticky="w", **pad
        )

        self.start_button = ttk.Button(frame, text="Start Editor", command=self._start)
        self.start_button.grid(row=2, column=0, sticky="ew", **pad)

        self.open_button = ttk.Button(frame, text="Open Browser", command=self._open)
        self.open_button.grid(row=2, column=1, sticky="ew", **pad)

        self.stop_button = ttk.Button(frame, text="Stop", command=self._stop)
        self.stop_button.grid(row=2, column=2, sticky="ew", **pad)

        ttk.Label(frame, textvariable=self.status).grid(
            row=3, column=0, columnspan=3, sticky="w", **pad
        )

        self.log = tk.Text(frame, width=68, height=12, state="disabled", wrap="word")
        self.log.grid(row=4, column=0, columnspan=3, sticky="ew", **pad)

        frame.columnconfigure(1, weight=1)
        self._set_running(False)

    def _set_running(self, running):
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.open_button.configure(state="normal" if running else "disabled")

    def _server_url(self):
        return "http://localhost:{0}".format(self.port.get().strip() or DEFAULT_PORT)

    def _command(self):
        command = [
            sys.executable,
            str(SERVER_SCRIPT),
        ]

        robot_ip = self.robot_ip.get().strip()
        if robot_ip:
            command.append(robot_ip)

        command.extend(["--port", str(self._validated_port()), "--no-browser"])
        return command

    def _validated_port(self):
        try:
            port = int(self.port.get().strip())
        except ValueError:
            raise ValueError("Port must be a number.")

        if port < 1 or port > 65535:
            raise ValueError("Port must be between 1 and 65535.")

        return port

    def _start(self):
        if self.process and self.process.poll() is None:
            self._open()
            return

        if not SERVER_SCRIPT.exists():
            messagebox.showerror("Missing server", "Cannot find {0}".format(SERVER_SCRIPT))
            return

        try:
            command = self._command()
        except ValueError as err:
            messagebox.showerror("Invalid port", str(err))
            return

        self._append_log("Starting Rowenta Map Editor proxy...\n")
        self.status.set("Starting")

        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(SERVER_SCRIPT.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1,
            )
        except OSError as err:
            self.status.set("Could not start")
            messagebox.showerror("Launch failed", str(err))
            return

        self._set_running(True)
        threading.Thread(target=self._read_output, daemon=True).start()
        self.after(700, self._open_if_running)

    def _open_if_running(self):
        if self.process and self.process.poll() is None:
            self.status.set("Running at {0}".format(self._server_url()))
            self._open()
        else:
            self.status.set("Stopped")
            self._set_running(False)

    def _open(self):
        webbrowser.open(self._server_url())

    def _stop(self):
        if not self.process or self.process.poll() is not None:
            self._set_running(False)
            self.status.set("Stopped")
            return

        self.status.set("Stopping")
        self.process.terminate()
        try:
            self.process.wait(timeout=4)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)

        self._append_log("\nStopped.\n")
        self.status.set("Stopped")
        self._set_running(False)

    def _read_output(self):
        for line in self.process.stdout:
            self.output_queue.put(line)

        code = self.process.poll()
        self.output_queue.put("\nProxy exited with code {0}.\n".format(code))

    def _poll_output(self):
        try:
            while True:
                self._append_log(self.output_queue.get_nowait())
        except queue.Empty:
            pass

        if self.process and self.process.poll() is not None:
            self._set_running(False)
            if not self.status.get().startswith("Stopped"):
                self.status.set("Stopped")

        self.after(150, self._poll_output)

    def _append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_close(self):
        self._stop()
        self.destroy()


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Launch the Rowenta Map Editor local proxy server."
    )
    parser.add_argument(
        "robot_ip",
        nargs="?",
        default=None,
        help="Robot IP, e.g. 192.168.1.50. If omitted, enter it in the UI.",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=DEFAULT_PORT,
        help="Local editor port (default: {0}).".format(DEFAULT_PORT),
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the proxy without opening a browser tab.",
    )
    return parser.parse_args(argv)


def _run_cli(argv):
    args = _parse_args(argv)

    command = [sys.executable, str(SERVER_SCRIPT)]
    if args.robot_ip:
        command.append(args.robot_ip)
    command.extend(["--port", str(args.port)])
    if args.no_browser:
        command.append("--no-browser")

    try:
        return subprocess.call(command, cwd=str(SERVER_SCRIPT.parent))
    except FileNotFoundError:
        print("Cannot find Python interpreter: {0}".format(sys.executable), file=sys.stderr)
    except OSError as err:
        print("Could not launch Rowenta Map Editor proxy: {0}".format(err), file=sys.stderr)

    return 1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv:
        return _run_cli(argv)

    if tk is None:
        print(
            "Tkinter is not available. Install Tkinter or run this launcher "
            "from a terminal with a robot IP.",
            file=sys.stderr,
        )
        return 1

    app = EditorLauncher()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
