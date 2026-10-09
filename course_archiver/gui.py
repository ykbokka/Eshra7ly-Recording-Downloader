# -*- coding: utf-8 -*-
"""Mac-inspired desktop interface for the Eshra7ly recording archiver."""
import json
import os
import queue
import threading
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk

import config
from app import make_probe, run_pipeline
from core.errors import ArchiverError, CancelledError
from core.ffmpeg_pipeline import PipelineConfig
from core.filenames import sanitize_filename
from core.stream_classifier import build_plan
from core.tools import find_tools
from platforms.eshra7ly import START_URL, eshra7lyplatform


ROOT = Path(__file__).resolve().parent
PREFS_FILE = ROOT / "gui_settings.json"

COLORS = {
    "app": "#F4F5F8",
    "sidebar": "#FFFFFF",
    "card": "#FFFFFF",
    "border": "#E7E9EF",
    "text": "#20232B",
    "muted": "#858A97",
    "blue": "#3478F6",
    "blue_hover": "#2367E8",
    "blue_soft": "#EAF1FF",
    "green": "#1EAD78",
    "green_soft": "#E8F8F1",
    "red": "#E95B57",
    "yellow": "#F1BC4F",
    "ink": "#171A21",
}


def _default_prefs():
    return {
        "output_dir": str(ROOT / "downloads"),
        "quality": "best",
        "audio": "copy",
        "capture_timeout": 300,
        "keep_temp": False,
        "headless": False,
    }


class Eshra7lyGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.title("Eshra7ly Downloader")
        self.geometry("1180x780")
        self.minsize(1020, 680)
        self.configure(fg_color=COLORS["app"])

        self.prefs = self._load_prefs()
        self.jobs = queue.Queue()
        self.busy = False
        self.stop_event = threading.Event()
        self.active_page = "Home"
        self.recent_downloads = []
        self.status_text = "Ready when you are."
        self.progress_value = 0.0
        self.progress_label = "Waiting for a recording"
        self.progress_details = "Your next lesson will appear here."
        self.speed_text = "—"
        self._build_shell()
        self._show_page("Home")
        self.after(45, self._pump_jobs)

    def _load_prefs(self):
        prefs = _default_prefs()
        try:
            if PREFS_FILE.exists():
                with PREFS_FILE.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    prefs.update({key: value for key, value in data.items() if key in prefs})
        except (OSError, ValueError):
            pass
        if not os.path.isdir(prefs.get("output_dir", "")):
            prefs["output_dir"] = _default_prefs()["output_dir"]
        return prefs

    def _save_prefs(self):
        try:
            with PREFS_FILE.open("w", encoding="utf-8") as handle:
                json.dump(self.prefs, handle, indent=2, ensure_ascii=False)
        except OSError as exc:
            self._set_status(f"Couldn't save settings: {exc}")

    def _build_shell(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.sidebar = ctk.CTkFrame(self, width=224, corner_radius=0, fg_color=COLORS["sidebar"])
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_propagate(False)
        self.sidebar.grid_rowconfigure(8, weight=1)

        brand = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand.pack(fill="x", padx=22, pady=(27, 34))
        logo = ctk.CTkFrame(brand, width=42, height=42, corner_radius=14, fg_color=COLORS["blue"])
        logo.pack(side="left")
        logo.pack_propagate(False)
        ctk.CTkLabel(logo, text="e", font=("SF Pro Display", 27, "bold"),
                     text_color="#FFFFFF").place(relx=0.5, rely=0.43, anchor="center")
        brand_text = ctk.CTkFrame(brand, fg_color="transparent")
        brand_text.pack(side="left", padx=(11, 0))
        ctk.CTkLabel(brand_text, text="eshra7ly", font=("SF Pro Text", 17, "bold"),
                     text_color=COLORS["text"]).pack(anchor="w")
        ctk.CTkLabel(brand_text, text="RECORDING ARCHIVER", font=("SF Pro Text", 8, "bold"),
                     text_color=COLORS["muted"]).pack(anchor="w", pady=(2, 0))

        ctk.CTkLabel(self.sidebar, text="WORKSPACE", font=("SF Pro Text", 9, "bold"),
                     text_color="#A0A4AF").pack(anchor="w", padx=24, pady=(0, 10))

        self.nav_buttons = {}
        for title, icon in (("Home", "⌂"), ("Downloads", "↓"), ("Settings", "⚙")):
            button = ctk.CTkButton(
                self.sidebar, text=f"   {icon}     {title}", anchor="w",
                height=43, corner_radius=12, border_spacing=12,
                font=("SF Pro Text", 13, "bold"),
                fg_color="transparent", hover_color="#F0F3F9",
                text_color=COLORS["muted"], command=lambda page=title: self._show_page(page),
            )
            button.pack(fill="x", padx=12, pady=3)
            self.nav_buttons[title] = button

        bottom = ctk.CTkFrame(self.sidebar, fg_color="#F7F8FA", corner_radius=15)
        bottom.pack(fill="x", padx=13, pady=17, side="bottom")
        ctk.CTkLabel(bottom, text="●  LOCAL & PRIVATE", font=("SF Pro Text", 9, "bold"),
                     text_color=COLORS["green"]).pack(anchor="w", padx=13, pady=(13, 5))
        ctk.CTkLabel(bottom, text="Login happens in your browser.\nCredentials aren't saved here.",
                     justify="left", wraplength=175, font=("SF Pro Text", 10),
                     text_color=COLORS["muted"]).pack(anchor="w", padx=13, pady=(0, 13))

        self.main = ctk.CTkFrame(self, fg_color=COLORS["app"], corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_rowconfigure(2, weight=1)
        self.main.grid_columnconfigure(0, weight=1)

        topbar = ctk.CTkFrame(self.main, fg_color=COLORS["app"], corner_radius=0, height=59)
        topbar.grid(row=0, column=0, sticky="ew", padx=29)
        topbar.grid_columnconfigure(1, weight=1)
        traffic = ctk.CTkFrame(topbar, fg_color="transparent")
        traffic.grid(row=0, column=0, sticky="w", pady=19)
        for color in (COLORS["red"], COLORS["yellow"], "#42C979"):
            dot = ctk.CTkFrame(traffic, width=11, height=11, corner_radius=6, fg_color=color)
            dot.pack(side="left", padx=(0, 7))
        self.page_hint = ctk.CTkLabel(topbar, text="HOME", font=("SF Pro Text", 9, "bold"),
                                      text_color=COLORS["muted"])
        self.page_hint.grid(row=0, column=1, sticky="e", pady=19)
        self.connection_pill = ctk.CTkLabel(
            topbar, text="●  READY", font=("SF Pro Text", 10, "bold"),
            text_color=COLORS["green"], fg_color=COLORS["green_soft"],
            corner_radius=10, padx=12, pady=6,
        )
        self.connection_pill.grid(row=0, column=2, sticky="e", padx=(17, 0), pady=12)

        self.page_host = ctk.CTkFrame(self.main, fg_color="transparent", corner_radius=0)
        self.page_host.grid(row=2, column=0, sticky="nsew", padx=29, pady=(4, 24))
        self.page_host.grid_columnconfigure(0, weight=1)
        self.page_host.grid_rowconfigure(0, weight=1)

    def _show_page(self, page):
        self.active_page = page
        for attr in (
            "home_status", "home_progress_title", "home_progress", "home_progress_info",
            "activity_box", "start_button", "cancel_button", "quality_menu", "audio_menu",
            "name_entry", "output_entry", "download_title", "download_page_status",
            "download_progress", "download_progress_details",
        ):
            if hasattr(self, attr):
                delattr(self, attr)
        for child in self.page_host.winfo_children():
            child.destroy()
        for name, button in self.nav_buttons.items():
            active = name == page
            button.configure(
                fg_color=COLORS["blue_soft"] if active else "transparent",
                text_color=COLORS["blue"] if active else COLORS["muted"],
                hover_color="#F0F3F9",
            )
        self.page_hint.configure(text=page.upper())
        if page == "Home":
            self._render_home()
        elif page == "Downloads":
            self._render_downloads()
        else:
            self._render_settings()

    def _label(self, parent, text, size=12, color=None, bold=False, **kwargs):
        return ctk.CTkLabel(
            parent, text=text, font=("SF Pro Text", size, "bold" if bold else "normal"),
            text_color=color or COLORS["text"], **kwargs,
        )

    def _card(self, parent, **kwargs):
        return ctk.CTkFrame(
            parent, fg_color=COLORS["card"], corner_radius=18,
            border_width=1, border_color=COLORS["border"], **kwargs,
        )

    def _render_home(self):
        host = self.page_host
        host.grid_columnconfigure(0, weight=7)
        host.grid_columnconfigure(1, weight=5)
        host.grid_rowconfigure(3, weight=1)

        self._label(host, "YOUR LEARNING, ARCHIVED.", 10, COLORS["blue"], True).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(8, 8))
        self._label(host, "A quieter way to save\nyour lessons.", 29, COLORS["text"], True,
                    justify="left").grid(row=1, column=0, columnspan=2, sticky="w")
        self._label(host, "Choose a quality, pick a destination, and let it run.",
                    12, COLORS["muted"]).grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 21))

        setup = self._card(host)
        setup.grid(row=3, column=0, sticky="nsew", padx=(0, 12), pady=(0, 12))
        setup.grid_columnconfigure(0, weight=1)
        self._label(setup, "New download", 18, COLORS["text"], True).grid(
            row=0, column=0, sticky="w", padx=22, pady=(22, 4))
        self._label(setup, "The recording chooser will open after you continue.",
                    11, COLORS["muted"]).grid(row=1, column=0, sticky="w", padx=22, pady=(0, 19))

        fields = ctk.CTkFrame(setup, fg_color="transparent")
        fields.grid(row=2, column=0, sticky="ew", padx=22)
        fields.grid_columnconfigure(0, weight=1)
        fields.grid_columnconfigure(1, weight=1)
        self._label(fields, "VIDEO QUALITY", 9, COLORS["muted"], True).grid(row=0, column=0, sticky="w")
        self._label(fields, "AUDIO TRACK", 9, COLORS["muted"], True).grid(row=0, column=1, sticky="w", padx=(13, 0))
        self.quality_menu = ctk.CTkOptionMenu(
            fields, values=["best", "1440", "1080", "720", "480"],
            height=39, corner_radius=10, fg_color="#F3F5F8", button_color="#E6EAF1",
            button_hover_color="#DCE3EE", text_color=COLORS["text"],
            font=("SF Pro Text", 12), dropdown_fg_color="#FFFFFF", dropdown_text_color=COLORS["text"],
        )
        self.quality_menu.grid(row=1, column=0, sticky="ew", pady=(7, 17), padx=(0, 7))
        self.quality_menu.set(self.prefs["quality"])
        self.audio_menu = ctk.CTkOptionMenu(
            fields, values=["Original AAC", "FLAC (transcode)"],
            height=39, corner_radius=10, fg_color="#F3F5F8", button_color="#E6EAF1",
            button_hover_color="#DCE3EE", text_color=COLORS["text"],
            font=("SF Pro Text", 12), dropdown_fg_color="#FFFFFF", dropdown_text_color=COLORS["text"],
        )
        self.audio_menu.grid(row=1, column=1, sticky="ew", pady=(7, 17), padx=(7, 0))
        self.audio_menu.set("FLAC (transcode)" if self.prefs["audio"] == "flac" else "Original AAC")

        self._label(fields, "FILE NAME  ·  OPTIONAL", 9, COLORS["muted"], True).grid(
            row=2, column=0, columnspan=2, sticky="w")
        self.name_entry = ctk.CTkEntry(
            fields, height=39, corner_radius=10, border_color=COLORS["border"],
            fg_color="#FAFBFC", text_color=COLORS["text"], placeholder_text="Use the recording title",
        )
        self.name_entry.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(7, 17))

        self._label(fields, "SAVE TO", 9, COLORS["muted"], True).grid(row=4, column=0, columnspan=2, sticky="w")
        output_row = ctk.CTkFrame(fields, fg_color="transparent")
        output_row.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(7, 21))
        output_row.grid_columnconfigure(0, weight=1)
        self.output_entry = ctk.CTkEntry(
            output_row, height=39, corner_radius=10, border_color=COLORS["border"],
            fg_color="#FAFBFC", text_color=COLORS["text"],
        )
        self.output_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.output_entry.insert(0, self.prefs["output_dir"])
        ctk.CTkButton(
            output_row, text="Browse", width=81, height=39, corner_radius=10,
            fg_color="#EDF0F5", hover_color="#E1E6EF", text_color=COLORS["text"],
            command=self._browse_output,
        ).grid(row=0, column=1)

        actions = ctk.CTkFrame(setup, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 21))
        actions.grid_columnconfigure(0, weight=1)
        self.start_button = ctk.CTkButton(
            actions, text="Choose recording  →", height=45, corner_radius=12,
            font=("SF Pro Text", 13, "bold"), fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"], command=self._start_download,
        )
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.cancel_button = ctk.CTkButton(
            actions, text="Stop", width=75, height=45, corner_radius=12,
            fg_color="#FCEDEC", hover_color="#F8DDDA", text_color=COLORS["red"],
            command=self._cancel_download, state="disabled",
        )
        self.cancel_button.grid(row=0, column=1)
        if self.busy:
            self.start_button.configure(state="disabled", text="Downloading…")
            self.cancel_button.configure(state="normal")

        right = ctk.CTkFrame(host, fg_color="transparent")
        right.grid(row=3, column=1, sticky="nsew", padx=(12, 0), pady=(0, 12))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        status_card = self._card(right)
        status_card.grid(row=0, column=0, sticky="ew", pady=(0, 13))
        self._label(status_card, "CURRENT SESSION", 9, COLORS["muted"], True).pack(
            anchor="w", padx=19, pady=(18, 10))
        self.home_status = self._label(status_card, self.status_text, 15, COLORS["text"], True,
                                       wraplength=275, justify="left")
        self.home_status.pack(anchor="w", padx=19)
        self.home_progress_title = self._label(status_card, self.progress_label, 10, COLORS["muted"])
        self.home_progress_title.pack(anchor="w", padx=19, pady=(15, 7))
        self.home_progress = ctk.CTkProgressBar(
            status_card, height=7, corner_radius=5,
            fg_color="#E9EDF4", progress_color=COLORS["blue"],
        )
        self.home_progress.pack(fill="x", padx=19)
        self.home_progress.set(self.progress_value)
        self.home_progress_info = self._label(status_card, self.progress_details, 10, COLORS["muted"])
        self.home_progress_info.pack(anchor="w", padx=19, pady=(7, 18))

        activity = self._card(right)
        activity.grid(row=1, column=0, sticky="nsew")
        self._label(activity, "Activity", 15, COLORS["text"], True).pack(anchor="w", padx=18, pady=(17, 4))
        self._label(activity, "A little trail of what the app is doing.", 10, COLORS["muted"]).pack(
            anchor="w", padx=18, pady=(0, 10))
        self.activity_box = ctk.CTkTextbox(
            activity, corner_radius=10, fg_color="#FAFBFC", border_width=1,
            border_color=COLORS["border"], text_color="#626978",
            font=("SF Mono", 10), wrap="word",
        )
        self.activity_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.activity_box.insert("end", "Ready. Your browser session stays on this computer.\n")
        self.activity_box.configure(state="disabled")
        self._set_status(self.status_text, log=False)

    def _render_downloads(self):
        host = self.page_host
        host.grid_columnconfigure(0, weight=1)
        self._label(host, "YOUR LIBRARY", 10, COLORS["blue"], True).pack(anchor="w", pady=(8, 8))
        self._label(host, "Downloads", 29, COLORS["text"], True).pack(anchor="w")
        self._label(host, "Completed recordings from this app session.", 12, COLORS["muted"]).pack(
            anchor="w", pady=(7, 23))

        current = self._card(host)
        current.pack(fill="x", pady=(0, 16))
        row = ctk.CTkFrame(current, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(20, 13))
        row.grid_columnconfigure(1, weight=1)
        icon = ctk.CTkFrame(row, width=46, height=46, corner_radius=13, fg_color=COLORS["blue_soft"])
        icon.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 13))
        icon.grid_propagate(False)
        ctk.CTkLabel(icon, text="↓", font=("SF Pro Text", 22, "bold"),
                     text_color=COLORS["blue"]).place(relx=.5, rely=.45, anchor="center")
        self._label(row, self.progress_label, 14, COLORS["text"], True).grid(row=0, column=1, sticky="w")
        self.download_title = self._label(row, self.progress_label, 14, COLORS["text"], True)
        self.download_title.grid(row=0, column=1, sticky="w")
        self.download_page_status = self._label(row, self.status_text, 10, COLORS["muted"])
        self.download_page_status.grid(row=1, column=1, sticky="w", pady=(3, 0))
        self.download_progress = ctk.CTkProgressBar(current, height=8, corner_radius=6,
                                                     fg_color="#E9EDF4", progress_color=COLORS["blue"])
        self.download_progress.pack(fill="x", padx=22, pady=(0, 8))
        self.download_progress.set(self.progress_value)
        self.download_progress_details = self._label(current, self.progress_details, 10, COLORS["muted"])
        self.download_progress_details.pack(anchor="w", padx=22, pady=(0, 18))

        self._label(host, "RECENT FILES", 9, COLORS["muted"], True).pack(anchor="w", pady=(4, 10))
        if not self.recent_downloads:
            empty = self._card(host)
            empty.pack(fill="x")
            self._label(empty, "Nothing here just yet", 16, COLORS["text"], True).pack(
                anchor="w", padx=22, pady=(23, 5))
            self._label(empty, "Your completed recordings will show up here after the first download.",
                        11, COLORS["muted"]).pack(anchor="w", padx=22, pady=(0, 23))
        else:
            for item in reversed(self.recent_downloads):
                card = self._card(host)
                card.pack(fill="x", pady=5)
                row = ctk.CTkFrame(card, fg_color="transparent")
                row.pack(fill="x", padx=18, pady=14)
                row.grid_columnconfigure(0, weight=1)
                self._label(row, item["name"], 13, COLORS["text"], True).grid(row=0, column=0, sticky="w")
                self._label(row, item["path"], 10, COLORS["muted"], wraplength=650).grid(
                    row=1, column=0, sticky="w", pady=(4, 0))
                ctk.CTkButton(
                    row, text="Show folder", width=100, height=33, corner_radius=9,
                    fg_color="#EEF2F8", hover_color="#E1E7F0", text_color=COLORS["text"],
                    command=lambda path=item["path"]: self._open_folder(path),
                ).grid(row=0, column=1, rowspan=2, padx=(10, 0))

    def _render_settings(self):
        host = self.page_host
        host.grid_columnconfigure(0, weight=1)
        self._label(host, "MAKE IT YOURS", 10, COLORS["blue"], True).pack(anchor="w", pady=(8, 8))
        self._label(host, "Settings", 29, COLORS["text"], True).pack(anchor="w")
        self._label(host, "A few sensible defaults for your archive.", 12, COLORS["muted"]).pack(
            anchor="w", pady=(7, 23))

        card = self._card(host)
        card.pack(fill="x")
        self._label(card, "Download preferences", 17, COLORS["text"], True).pack(
            anchor="w", padx=22, pady=(21, 4))
        self._label(card, "These preferences stay on this computer.", 11, COLORS["muted"]).pack(
            anchor="w", padx=22, pady=(0, 19))

        self._label(card, "Default output folder", 11, COLORS["text"], True).pack(
            anchor="w", padx=22)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(7, 18))
        row.grid_columnconfigure(0, weight=1)
        self.settings_output = ctk.CTkEntry(row, height=39, corner_radius=10,
                                             border_color=COLORS["border"], fg_color="#FAFBFC")
        self.settings_output.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.settings_output.insert(0, self.prefs["output_dir"])
        ctk.CTkButton(row, text="Browse", width=86, height=39, corner_radius=10,
                      fg_color="#EDF0F5", hover_color="#E1E6EF", text_color=COLORS["text"],
                      command=lambda: self._browse_output(self.settings_output)).grid(row=0, column=1)

        self._label(card, "Default quality", 11, COLORS["text"], True).pack(anchor="w", padx=22)
        self.settings_quality = ctk.CTkOptionMenu(
            card, values=["best", "1440", "1080", "720", "480"], height=39, corner_radius=10,
            fg_color="#F3F5F8", button_color="#E6EAF1", button_hover_color="#DCE3EE",
            text_color=COLORS["text"], dropdown_fg_color="#FFFFFF", dropdown_text_color=COLORS["text"],
        )
        self.settings_quality.pack(anchor="w", padx=22, pady=(7, 18))
        self.settings_quality.set(self.prefs["quality"])

        self._label(card, "Default audio handling", 11, COLORS["text"], True).pack(anchor="w", padx=22)
        self.settings_audio = ctk.CTkOptionMenu(
            card, values=["Original AAC", "FLAC (transcode)"], height=39, corner_radius=10,
            fg_color="#F3F5F8", button_color="#E6EAF1", button_hover_color="#DCE3EE",
            text_color=COLORS["text"], dropdown_fg_color="#FFFFFF", dropdown_text_color=COLORS["text"],
        )
        self.settings_audio.pack(anchor="w", padx=22, pady=(7, 18))
        self.settings_audio.set("FLAC (transcode)" if self.prefs["audio"] == "flac" else "Original AAC")

        self.keep_temp_var = tk.BooleanVar(value=bool(self.prefs["keep_temp"]))
        ctk.CTkCheckBox(
            card, text="Keep temporary media files after successful downloads",
            variable=self.keep_temp_var, font=("SF Pro Text", 11),
            text_color=COLORS["text"], fg_color=COLORS["blue"], hover_color=COLORS["blue_hover"],
            border_color="#C7CCD5",
        ).pack(anchor="w", padx=22, pady=(1, 12))

        self.background_browser_var = tk.BooleanVar(
            value=bool(self.prefs.get("headless", False))
        )
        ctk.CTkCheckBox(
            card, text="Run Chromium in the background",
            variable=self.background_browser_var, font=("SF Pro Text", 11, "bold"),
            text_color=COLORS["text"], fg_color=COLORS["blue"], hover_color=COLORS["blue_hover"],
            border_color="#C7CCD5",
        ).pack(anchor="w", padx=22, pady=(0, 4))
        self._label(
            card,
            "Best after you've signed in once. Turn this off if the site asks for a code or human verification.",
            10, COLORS["muted"], wraplength=690, justify="left",
        ).pack(anchor="w", padx=46, pady=(0, 15))

        ctk.CTkButton(
            card, text="Save preferences", height=43, corner_radius=11, width=170,
            font=("SF Pro Text", 12, "bold"), fg_color=COLORS["blue"],
            hover_color=COLORS["blue_hover"], command=self._save_settings_from_page,
        ).pack(anchor="w", padx=22, pady=(0, 22))

        privacy = self._card(host)
        privacy.pack(fill="x", pady=(16, 0))
        self._label(privacy, "Privacy by design", 13, COLORS["text"], True).pack(
            anchor="w", padx=20, pady=(16, 4))
        self._label(privacy,
                    "Credentials are entered locally when needed and aren't written to this settings file. "
                    "Protected or DRM-encrypted streams are not supported.",
                    11, COLORS["muted"], justify="left", wraplength=760).pack(
            anchor="w", padx=20, pady=(0, 16))

    def _browse_output(self, target=None):
        current = target.get() if target else getattr(self, "output_entry", None).get()
        selected = filedialog.askdirectory(initialdir=current if os.path.isdir(current) else str(ROOT))
        if selected:
            widget = target if target else self.output_entry
            widget.delete(0, "end")
            widget.insert(0, selected)

    def _save_settings_from_page(self):
        self.prefs.update({
            "output_dir": self.settings_output.get().strip() or str(ROOT / "downloads"),
            "quality": self.settings_quality.get(),
            "audio": "flac" if self.settings_audio.get().startswith("FLAC") else "copy",
            "keep_temp": bool(self.keep_temp_var.get()),
            "headless": bool(self.background_browser_var.get()),
        })
        self._save_prefs()
        self._set_status("Preferences saved.")
        self._show_toast("Preferences saved on this computer.")
        self._show_page("Settings")

    def _show_toast(self, message):
        self.connection_pill.configure(text="●  SAVED", text_color=COLORS["green"],
                                       fg_color=COLORS["green_soft"])
        self._append_activity(message)

    def _start_download(self):
        if self.busy:
            return
        output_dir = self.output_entry.get().strip() or str(ROOT / "downloads")
        try:
            os.makedirs(output_dir, exist_ok=True)
        except OSError as exc:
            self._show_error("Output folder", f"Can't use that folder:\n{exc}")
            return

        self.prefs.update({
            "output_dir": output_dir,
            "quality": self.quality_menu.get(),
            "audio": "flac" if self.audio_menu.get().startswith("FLAC") else "copy",
        })
        self._save_prefs()
        self.busy = True
        self.stop_event.clear()
        self.start_button.configure(state="disabled", text="Starting browser…")
        self.cancel_button.configure(state="normal")
        self.connection_pill.configure(text="●  WORKING", text_color=COLORS["blue"], fg_color=COLORS["blue_soft"])
        self._set_status("Preparing your browser session…")
        snapshot = {
            "output_dir": output_dir,
            "quality": self.prefs["quality"],
            "audio": self.prefs["audio"],
            "name": self.name_entry.get().strip(),
            "keep_temp": bool(self.prefs["keep_temp"]),
            "capture_timeout": int(self.prefs.get("capture_timeout", 300)),
            "headless": bool(self.prefs.get("headless", False)),
        }
        threading.Thread(target=self._download_worker, args=(snapshot,), daemon=True).start()

    def _download_worker(self, options):
        try:
            self._notify_status("Checking FFmpeg…")
            tools = find_tools()
            self._notify_status(f"FFmpeg ready · {tools.version}")
            self._notify_status("Opening Eshra7ly in Chromium…")
            platform = eshra7lyplatform(
                capture_timeout=options["capture_timeout"],
                on_status=self._notify_status,
                choose_callback=self._choose_from_browser,
                login_callback=self._login_from_browser,
                cancel_event=self.stop_event,
                headless=options["headless"],
            )
            info = platform.extract_media_info(START_URL)
            if self.stop_event.is_set():
                raise CancelledError("cancelled")
            cfg = PipelineConfig(
                tools, info["headers"], audio_mode=options["audio"], cancel=self.stop_event,
            )
            self._notify_status("Identifying available video and audio streams…")
            plan = build_plan(
                info["captures"], options["quality"], headers=info["headers"],
                probe=make_probe(tools, info["headers"], cfg),
            )
            name = sanitize_filename(options["name"] or info.get("title") or "recording")
            self._notify_status(f"Downloading {name} · {plan.label}")
            final_path = run_pipeline(
                plan, cfg, options["output_dir"], name,
                keep_temp=options["keep_temp"], on_progress=self._on_progress,
                on_status=self._notify_status,
            )
            self.jobs.put({
                "kind": "complete", "path": final_path, "name": os.path.basename(final_path),
            })
        except CancelledError:
            self.jobs.put({"kind": "cancelled"})
        except (ArchiverError, OSError, ValueError) as exc:
            self.jobs.put({"kind": "error", "message": str(exc)})
        except Exception as exc:
            self.jobs.put({
                "kind": "error",
                "message": f"{exc}\n\n{traceback.format_exc(limit=5)}",
            })

    def _cancel_download(self):
        if self.busy:
            self.stop_event.set()
            self._set_status("Stopping safely… FFmpeg will be asked to finish its current file.")
            self.cancel_button.configure(state="disabled")

    def _finish_busy_state(self):
        self.busy = False
        if getattr(self, "start_button", None) is not None:
            self.start_button.configure(state="normal", text="Choose recording  →")
        if getattr(self, "cancel_button", None) is not None:
            self.cancel_button.configure(state="disabled")
        self.connection_pill.configure(text="●  READY", text_color=COLORS["green"],
                                       fg_color=COLORS["green_soft"])

    def _notify_status(self, message):
        self.jobs.put({"kind": "status", "message": str(message)})

    def _on_progress(self, label, pct, out_s, size, speed):
        self.jobs.put({
            "kind": "progress", "label": label, "pct": pct,
            "out_s": out_s, "size": size, "speed": speed,
        })

    def _choose_from_browser(self, title, items):
        return self._call_ui(lambda: self._choice_dialog(title, items))

    def _login_from_browser(self):
        return self._call_ui(self._login_dialog)

    def _call_ui(self, callback):
        event = threading.Event()
        result = {}
        self.jobs.put({"kind": "call", "callback": callback, "event": event, "result": result})
        if not event.wait(timeout=900):
            raise TimeoutError("The UI prompt timed out.")
        if "error" in result:
            raise result["error"]
        return result.get("value")

    def _choice_dialog(self, title, items):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Choose a recording")
        dialog.geometry("620x590")
        dialog.minsize(500, 400)
        dialog.configure(fg_color=COLORS["app"])
        dialog.transient(self)
        dialog.grab_set()
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(2, weight=1)
        self._label(dialog, title.title(), 19, COLORS["text"], True).grid(
            row=0, column=0, sticky="w", padx=24, pady=(23, 5))
        self._label(dialog, f"{len(items)} options · choose one to continue", 11, COLORS["muted"]).grid(
            row=1, column=0, sticky="w", padx=24, pady=(0, 15))
        scroll = ctk.CTkScrollableFrame(dialog, fg_color="#FFFFFF", corner_radius=13,
                                        border_width=1, border_color=COLORS["border"])
        scroll.grid(row=2, column=0, sticky="nsew", padx=22, pady=(0, 14))
        selected = tk.IntVar(value=0)
        for index, label in enumerate(items):
            ctk.CTkRadioButton(
                scroll, text=f"{index + 1:02d}   {label}", variable=selected, value=index,
                font=("SF Pro Text", 11), text_color=COLORS["text"],
                fg_color=COLORS["blue"], hover_color=COLORS["blue_hover"],
                border_color="#C7CCD5", radiobutton_width=17, radiobutton_height=17,
                command=lambda: None,
            ).pack(fill="x", padx=14, pady=9, anchor="w")
        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 20))
        buttons.grid_columnconfigure(0, weight=1)
        result = {"index": None}

        def confirm():
            result["index"] = selected.get()
            dialog.destroy()

        def cancel():
            dialog.destroy()

        ctk.CTkButton(buttons, text="Cancel", width=95, height=40, corner_radius=10,
                      fg_color="#E9ECF2", hover_color="#DDE2EB", text_color=COLORS["text"],
                      command=cancel).grid(row=0, column=1, padx=(8, 0))
        ctk.CTkButton(buttons, text="Continue  →", width=130, height=40, corner_radius=10,
                      fg_color=COLORS["blue"], hover_color=COLORS["blue_hover"],
                      command=confirm).grid(row=0, column=2)
        self.wait_window(dialog)
        if result["index"] is None:
            raise CancelledError("selection cancelled")
        return result["index"]

    def _login_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Eshra7ly sign-in")
        dialog.geometry("440x365")
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["app"])
        dialog.transient(self)
        dialog.grab_set()
        self._label(dialog, "Sign in to Eshra7ly", 21, COLORS["text"], True).pack(
            anchor="w", padx=25, pady=(25, 6))
        self._label(dialog, "Entered here only to fill the login form in your browser.",
                    11, COLORS["muted"], wraplength=380, justify="left").pack(
            anchor="w", padx=25, pady=(0, 19))
        self._label(dialog, "EMAIL OR USERNAME", 9, COLORS["muted"], True).pack(anchor="w", padx=25)
        username = ctk.CTkEntry(dialog, height=40, corner_radius=10,
                                border_color=COLORS["border"], fg_color="#FFFFFF")
        username.pack(fill="x", padx=25, pady=(7, 14))
        self._label(dialog, "PASSWORD", 9, COLORS["muted"], True).pack(anchor="w", padx=25)
        password = ctk.CTkEntry(dialog, height=40, corner_radius=10, show="●",
                                border_color=COLORS["border"], fg_color="#FFFFFF")
        password.pack(fill="x", padx=25, pady=(7, 20))
        result = {"credentials": (None, None)}

        def submit():
            result["credentials"] = (username.get().strip(), password.get())
            password.delete(0, "end")
            dialog.destroy()

        def cancel():
            password.delete(0, "end")
            dialog.destroy()

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.pack(fill="x", padx=25, pady=(0, 20))
        ctk.CTkButton(buttons, text="Cancel", width=90, height=40, corner_radius=10,
                      fg_color="#E9ECF2", hover_color="#DDE2EB", text_color=COLORS["text"],
                      command=cancel).pack(side="right", padx=(8, 0))
        ctk.CTkButton(buttons, text="Continue", width=115, height=40, corner_radius=10,
                      fg_color=COLORS["blue"], hover_color=COLORS["blue_hover"],
                      command=submit).pack(side="right")
        self.wait_window(dialog)
        return result["credentials"]

    def _pump_jobs(self):
        try:
            while True:
                item = self.jobs.get_nowait()
                kind = item.get("kind")
                if kind == "call":
                    try:
                        item["result"]["value"] = item["callback"]()
                    except BaseException as exc:
                        item["result"]["error"] = exc
                    finally:
                        item["event"].set()
                elif kind == "status":
                    self._set_status(item["message"])
                elif kind == "progress":
                    self._apply_progress(item)
                elif kind == "complete":
                    self._finish_busy_state()
                    self.stop_event.clear()
                    path = item["path"]
                    self.recent_downloads.append({
                        "name": item["name"], "path": path,
                        "time": datetime.now().strftime("%H:%M"),
                    })
                    self.progress_value = 1.0
                    self.progress_label = item["name"]
                    self.progress_details = "Completed successfully"
                    self._set_status(f"Saved · {path}")
                    self._apply_progress({"kind": "progress", "label": "Complete", "pct": 100,
                                          "out_s": 0, "size": os.path.getsize(path) if os.path.exists(path) else 0,
                                          "speed": ""})
                    self._show_toast("Recording downloaded successfully.")
                    if self.active_page == "Downloads":
                        self._show_page("Downloads")
                elif kind == "cancelled":
                    self._finish_busy_state()
                    self.stop_event.clear()
                    self._set_status("Download cancelled.")
                elif kind == "error":
                    self._finish_busy_state()
                    self.stop_event.clear()
                    self._set_status("Something needs attention.")
                    self._show_error("Download couldn't finish", item["message"])
        except queue.Empty:
            pass
        self.after(45, self._pump_jobs)

    def _apply_progress(self, item):
        pct = item.get("pct")
        if pct is not None:
            self.progress_value = max(0.0, min(1.0, float(pct) / 100.0))
        label = item.get("label", "Downloading")
        self.progress_label = f"{label.title()} · {pct:.1f}%" if pct is not None else label.title()
        size_mb = item.get("size", 0) / 1048576
        speed = str(item.get("speed") or "").strip()
        self.speed_text = speed or "—"
        self.progress_details = f"{size_mb:.1f} MB written" + (f"  ·  {speed}" if speed else "")
        if hasattr(self, "home_progress"):
            self.home_progress.set(self.progress_value)
            self.home_progress_title.configure(text=self.progress_label)
            self.home_progress_info.configure(text=self.progress_details)
        if hasattr(self, "download_progress"):
            self.download_progress.set(self.progress_value)
            self.download_title.configure(text=self.progress_label)
            self.download_progress_details.configure(text=self.progress_details)
            self.download_page_status.configure(text=self.status_text)

    def _set_status(self, message, log=True):
        self.status_text = str(message)
        if hasattr(self, "home_status"):
            self.home_status.configure(text=self.status_text)
        if hasattr(self, "download_page_status"):
            self.download_page_status.configure(text=self.status_text)
        if hasattr(self, "connection_pill") and not self.busy:
            self.connection_pill.configure(text="●  READY", text_color=COLORS["green"],
                                           fg_color=COLORS["green_soft"])
        if log:
            self._append_activity(self.status_text)

    def _append_activity(self, message):
        if not hasattr(self, "activity_box"):
            return
        try:
            self.activity_box.configure(state="normal")
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.activity_box.insert("end", f"{timestamp}  {message}\n")
            self.activity_box.see("end")
            self.activity_box.configure(state="disabled")
        except Exception:
            pass

    def _show_error(self, title, message):
        from tkinter import messagebox
        messagebox.showerror(title, message, parent=self)

    def _open_folder(self, path):
        folder = os.path.dirname(path) if os.path.isfile(path) else path
        if os.path.isdir(folder):
            try:
                os.startfile(folder)
            except OSError as exc:
                self._show_error("Open folder", str(exc))

 
if __name__ == "__main__":
    Eshra7lyGUI().mainloop()
