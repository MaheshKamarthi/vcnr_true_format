import json
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from vcnr_true_core import TrueVCNRReader, check_ffplay, read_header


class Player(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VCNR v3 Protected Player")
        self.geometry("820x430")
        self.path = tk.StringVar()
        self.passcode = tk.StringVar()
        self.reader = None
        self.process = None
        self.worker_thread = None
        self.info_thread = None
        self.stop_requested = threading.Event()
        self.closing = False
        self.build()

    def build(self):
        top = ttk.Frame(self, padding=12)
        top.pack(fill="both", expand=True)
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="VCNR File or URL").grid(row=0, column=0, sticky="w", pady=8)
        ttk.Entry(top, textvariable=self.path).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(top, text="Browse", command=self.browse).grid(row=0, column=2)
        ttk.Label(
            top,
            text="For online playback, paste an https:// address to a .vcnr file.",
        ).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(top, text="Passcode").grid(row=2, column=0, sticky="w", pady=8)
        ttk.Entry(top, textvariable=self.passcode, show="*").grid(
            row=2, column=1, sticky="ew", padx=8
        )
        buttons = ttk.Frame(top)
        buttons.grid(row=3, column=1, sticky="w", pady=8)
        self.info_button = ttk.Button(buttons, text="Read Info", command=self.info)
        self.info_button.pack(side="left", padx=(0, 8))
        self.play_button = ttk.Button(buttons, text="Unlock & Play", command=self.play)
        self.play_button.pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Stop", command=self.stop).pack(side="left")
        self.status = ttk.Label(top, text="Ready")
        self.status.grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)
        self.infobox = tk.Text(top, height=12, wrap="word")
        self.infobox.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=8)
        top.rowconfigure(5, weight=1)
        if not check_ffplay():
            self.status.config(text="FFplay not found. Install FFmpeg with FFplay.")

    def browse(self):
        path = filedialog.askopenfilename(
            filetypes=[("VCNR files", "*.vcnr"), ("All files", "*.*")]
        )
        if path:
            self.path.set(path)
            self.info()

    def info(self):
        if not self.path.get():
            return
        if self.info_thread and self.info_thread.is_alive():
            return
        source = self.path.get()
        self.info_button.config(state="disabled")
        self.status.config(text="Reading VCNR info...")
        self.info_thread = threading.Thread(
            target=self._read_info,
            args=(source,),
            daemon=True,
        )
        self.info_thread.start()

    def _read_info(self, source):
        try:
            header = read_header(source)
            self._call_ui(self._show_info, header)
        except Exception as exc:
            self._call_ui(messagebox.showerror, "Error", str(exc))
            self._call_ui(self.status.config, text="Could not read VCNR info")
        finally:
            self._call_ui(self.info_button.config, state="normal")

    def _show_info(self, header):
        if not self.closing:
            self.infobox.delete("1.0", "end")
            self.infobox.insert("end", json.dumps(header, indent=2))
            self.status.config(text="VCNR info loaded")

    def _call_ui(self, callback, *args, **kwargs):
        if self.closing:
            return
        try:
            self.after(0, lambda: callback(*args, **kwargs))
        except (RuntimeError, tk.TclError):
            pass

    def play(self):
        if not self.path.get() or not self.passcode.get():
            messagebox.showerror("Missing", "Choose a VCNR file and enter its passcode.")
            return
        if self.worker_thread and self.worker_thread.is_alive():
            return
        if not check_ffplay():
            messagebox.showerror("FFplay missing", "Install FFmpeg with FFplay and add it to PATH.")
            return
        source = self.path.get()
        passcode = self.passcode.get()
        self.stop_requested.clear()
        self.play_button.config(state="disabled")
        self.status.config(text="Unlocking...")
        self.worker_thread = threading.Thread(
            target=self._stream_to_player,
            args=(source, passcode),
            daemon=True,
        )
        self.worker_thread.start()

    def _stream_to_player(self, source, passcode):
        try:
            self.reader = TrueVCNRReader(source, passcode)
            command = [
                "ffplay",
                "-loglevel", "error",
                "-autoexit",
                "-window_title", self.reader.header.get("title", "VCNR Player"),
                "-i", "pipe:0",
            ]
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            process = self.process
            error_chunks = []

            def drain_errors():
                if process.stderr:
                    for chunk in iter(lambda: process.stderr.read(4096), b""):
                        error_chunks.append(chunk)
                        if sum(map(len, error_chunks)) > 64 * 1024:
                            del error_chunks[0]

            error_thread = threading.Thread(target=drain_errors, daemon=True)
            error_thread.start()
            self._call_ui(self.status.config, text="Playing protected VCNR...")
            try:
                assert process.stdin is not None
                for chunk in self.reader.iter_decrypted_chunks():
                    if self.stop_requested.is_set():
                        break
                    process.stdin.write(chunk)
                process.stdin.close()
                return_code = process.wait()
                error_thread.join(timeout=1)
                if return_code and not self.stop_requested.is_set():
                    error = b"".join(error_chunks).decode("utf-8", "replace").strip()
                    raise RuntimeError(error or f"FFplay failed with exit code {return_code}")
            except (BrokenPipeError, OSError) as exc:
                if not self.stop_requested.is_set():
                    raise RuntimeError("FFplay closed unexpectedly") from exc
            if not self.stop_requested.is_set():
                self._call_ui(self.status.config, text="Finished")
        except Exception as exc:
            if not self.stop_requested.is_set():
                self._call_ui(messagebox.showerror, "Cannot play", str(exc))
                self._call_ui(self.status.config, text="Unlock or playback failed")
        finally:
            if self.reader:
                self.reader.close()
            self.reader = None
            self.process = None
            self._call_ui(self.play_button.config, state="normal")

    def stop(self):
        self.stop_requested.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()
        if not self.closing:
            self.status.config(text="Stopped")

    def destroy(self):
        self.closing = True
        self.stop()
        super().destroy()


if __name__ == "__main__":
    Player().mainloop()
