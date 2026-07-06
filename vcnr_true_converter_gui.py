import os
import secrets
import string
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from vcnr_true_core import check_ffmpeg, create_true_vcnr


def gen_pass(length=24):
    alphabet = string.ascii_letters + string.digits + "-_@$#"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Converter(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VCNR v3 Browser-Compatible Converter")
        self.geometry("900x660")
        self.input = tk.StringVar()
        self.output = tk.StringVar()
        self.passcode = tk.StringVar()
        self.confirm = tk.StringVar()
        self.title_v = tk.StringVar()
        self.owner = tk.StringVar()
        self.copy_id = tk.StringVar()
        self.quality = tk.StringVar(value="23")
        self.preset = tk.StringVar(value="medium")
        self.max_width = tk.StringVar(value="1920")
        self.build()
        self.log("FFmpeg found." if check_ffmpeg() else "WARNING: FFmpeg not found.")

    def build(self):
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        rows = [
            ("Input Video", self.input, self.browse_input, False),
            ("Output VCNR", self.output, self.browse_output, False),
            ("Passcode", self.passcode, self.auto_pass, True),
            ("Confirm Passcode", self.confirm, self.copy_pass, True),
            ("Title", self.title_v, None, False),
            ("Owner", self.owner, None, False),
            ("Copy ID", self.copy_id, None, False),
        ]
        for row, (label, variable, command, secret) in enumerate(rows):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=8)
            ttk.Entry(frame, textvariable=variable, show="*" if secret else "").grid(
                row=row, column=1, sticky="ew", padx=8
            )
            if command:
                text = "Browse" if row < 2 else ("Generate" if row == 2 else "Copy")
                ttk.Button(frame, text=text, command=command).grid(row=row, column=2, padx=8)

        row = len(rows)
        ttk.Label(frame, text="H.264 Quality (CRF)").grid(row=row, column=0, sticky="w", padx=8, pady=8)
        ttk.Combobox(
            frame, textvariable=self.quality, values=["18", "20", "23", "26", "28"], width=12
        ).grid(row=row, column=1, sticky="w", padx=8)
        ttk.Label(frame, text="Lower = better quality and larger file").grid(
            row=row, column=1, sticky="w", padx=145
        )

        row += 1
        ttk.Label(frame, text="Compression preset").grid(row=row, column=0, sticky="w", padx=8, pady=8)
        ttk.Combobox(
            frame,
            textvariable=self.preset,
            values=["veryfast", "fast", "medium", "slow", "slower"],
            width=12,
            state="readonly",
        ).grid(row=row, column=1, sticky="w", padx=8)

        row += 1
        ttk.Label(frame, text="Maximum width").grid(row=row, column=0, sticky="w", padx=8, pady=8)
        ttk.Combobox(
            frame, textvariable=self.max_width, values=["854", "1280", "1920", "2560", "3840"], width=12
        ).grid(row=row, column=1, sticky="w", padx=8)

        row += 1
        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.grid(row=row, column=1, sticky="ew", padx=8, pady=12)
        self.status = ttk.Label(frame, text="Ready")
        self.status.grid(row=row, column=2)

        row += 1
        self.button = ttk.Button(frame, text="Create Protected VCNR", command=self.start)
        self.button.grid(row=row, column=1, sticky="w", padx=8, pady=8)

        row += 1
        self.logbox = tk.Text(frame, height=12, wrap="word")
        self.logbox.grid(row=row, column=0, columnspan=3, sticky="nsew", padx=8, pady=8)
        frame.rowconfigure(row, weight=1)

    def log(self, message):
        self.logbox.insert("end", str(message) + "\n")
        self.logbox.see("end")

    def browse_input(self):
        path = filedialog.askopenfilename(
            filetypes=[("Video files", "*.mp4 *.mkv *.avi *.mov *.webm *.wmv *.m4v *.ts"), ("All", "*.*")]
        )
        if path:
            self.input.set(path)
            if not self.output.get():
                self.output.set(os.path.splitext(path)[0] + ".vcnr")
            if not self.title_v.get():
                self.title_v.set(os.path.splitext(os.path.basename(path))[0])

    def browse_output(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".vcnr", filetypes=[("VCNR files", "*.vcnr")]
        )
        if path:
            self.output.set(path)

    def auto_pass(self):
        code = gen_pass()
        self.passcode.set(code)
        self.confirm.set(code)
        self.copy_pass()
        messagebox.showinfo("Passcode", "Generated and copied. Save it safely.")

    def copy_pass(self):
        if self.passcode.get():
            self.clipboard_clear()
            self.clipboard_append(self.passcode.get())

    def set_progress(self, value, status):
        self.progress["value"] = value
        self.status.config(text=status)

    def start(self):
        if not self.input.get() or not self.output.get() or not self.passcode.get():
            messagebox.showerror("Missing", "Input, output, and passcode are required.")
            return
        if self.passcode.get() != self.confirm.get():
            messagebox.showerror("Mismatch", "Passcodes do not match.")
            return
        self.button.config(state="disabled")
        self.progress["value"] = 0
        threading.Thread(target=self.worker, daemon=True).start()

    def worker(self):
        try:
            create_true_vcnr(
                self.input.get(),
                self.output.get(),
                self.passcode.get(),
                title=self.title_v.get(),
                owner=self.owner.get(),
                copy_id=self.copy_id.get(),
                quality=int(self.quality.get()),
                preset=self.preset.get(),
                max_width=int(self.max_width.get()),
                progress=lambda p, s: self.after(0, self.set_progress, p, s),
                log=lambda m: self.after(0, self.log, m),
            )
            self.after(0, messagebox.showinfo, "Done", "Protected VCNR file created.")
        except Exception as exc:
            self.after(0, messagebox.showerror, "Error", str(exc))
            self.after(0, self.log, "ERROR: " + str(exc))
        finally:
            self.after(0, self.button.config, {"state": "normal"})


if __name__ == "__main__":
    Converter().mainloop()
