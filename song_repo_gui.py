#!/usr/bin/env python3
"""
Native Windows dashboard for song_repo_builder.py.

The GUI intentionally launches the existing builder as a subprocess. That keeps
parsing, matching, caching, and multiprocessing behavior in one tested pipeline.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import webbrowser
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import song_repo_builder
except Exception:  # pragma: no cover - fallback only matters if the builder cannot import.
    song_repo_builder = None

from song_repo_opensong import DEFAULT_OPENSONG_EXPORT_FOLDER, convert_chordpro_output_to_opensong

try:
    from song_repo_review_pyside import open_review_resolver
except Exception:  # pragma: no cover - fallback for environments without PySide6.
    from song_repo_review_gui import open_review_resolver


APP_DIR = Path(__file__).resolve().parent
BUILDER_SCRIPT = APP_DIR / "song_repo_builder.py"
SETTINGS_FILE = APP_DIR / ".song_repo_gui_settings.json"


def default_worker_count() -> int:
    if song_repo_builder is not None:
        return song_repo_builder.default_worker_count()
    cpu_count = os.cpu_count() or 2
    if cpu_count <= 1:
        return 1
    return max(1, min(8, cpu_count - 1))


def _base_preset() -> Dict[str, Any]:
    workers = default_worker_count()
    return {
        "auto_identity_threshold": 0.86,
        "clean_lyric_threshold": 0.96,
        "review_lyric_threshold": 0.65,
        "line_match_threshold": 0.82,
        "max_line_bucket": 250,
        "max_title_bucket": 500,
        "progress_every": 50,
        "parse_workers": workers,
        "score_workers": workers,
        "parallel_parse_threshold": 100,
        "parallel_score_threshold": 1000,
        "score_batch_size": 250,
        "cache_write_batch_size": 500,
    }


def performance_preset(name: str) -> Dict[str, Any]:
    """Return processing defaults without changing matching accuracy thresholds."""
    balanced = _base_preset()
    presets = {
        "Balanced": balanced,
        "Max speed": {
            **balanced,
            "progress_every": 100,
            "parallel_parse_threshold": 10,
            "parallel_score_threshold": 100,
            "score_batch_size": 500,
            "cache_write_batch_size": 1000,
        },
        "Careful audit": {
            **balanced,
            "progress_every": 25,
            "score_batch_size": 200,
            "cache_write_batch_size": 300,
        },
    }
    return dict(presets.get(name, balanced))


@dataclass
class DashboardSettings:
    openlyrics: Optional[Path] = None
    onsong: Optional[Path] = None
    txt: Optional[Path] = None
    output: Path = APP_DIR / "song_repo_output"
    cache_dir: Optional[Path] = None
    preset: str = "Balanced"
    dry_run: bool = False
    clean_output: bool = False
    verify_hashes: bool = False
    force_reparse: bool = False
    force_rescore: bool = False
    auto_identity_threshold: float = 0.86
    clean_lyric_threshold: float = 0.96
    review_lyric_threshold: float = 0.65
    line_match_threshold: float = 0.82
    max_line_bucket: int = 250
    max_title_bucket: int = 500
    progress_every: int = 50
    parse_workers: int = default_worker_count()
    score_workers: int = default_worker_count()
    parallel_parse_threshold: int = 100
    parallel_score_threshold: int = 1000
    score_batch_size: int = 250
    cache_write_batch_size: int = 500

    def to_json_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for key in ("openlyrics", "onsong", "txt", "output", "cache_dir"):
            value = data[key]
            data[key] = str(value) if value else ""
        return data

    @classmethod
    def from_json_dict(cls, data: Dict[str, Any]) -> "DashboardSettings":
        allowed = {field.name for field in fields(cls)}
        cleaned = {key: value for key, value in data.items() if key in allowed}
        for key in ("openlyrics", "onsong", "txt", "output", "cache_dir"):
            if key in cleaned:
                cleaned[key] = Path(cleaned[key]) if str(cleaned[key]).strip() else None
        if cleaned.get("output") is None:
            cleaned["output"] = APP_DIR / "song_repo_output"
        return cls(**cleaned)


def _append_option(command: List[str], flag: str, value: Any) -> None:
    command.extend([flag, str(value)])


def build_builder_command(
    settings: DashboardSettings,
    python_executable: Optional[str] = None,
    script_path: Path = BUILDER_SCRIPT,
) -> List[str]:
    command = [python_executable or sys.executable, str(script_path)]
    if settings.openlyrics:
        _append_option(command, "--openlyrics", settings.openlyrics)
    if settings.onsong:
        _append_option(command, "--onsong", settings.onsong)
    if settings.txt:
        _append_option(command, "--txt", settings.txt)
    _append_option(command, "--out", settings.output)
    if settings.cache_dir:
        _append_option(command, "--cache-dir", settings.cache_dir)

    for enabled, flag in (
        (settings.dry_run, "--dry-run"),
        (settings.clean_output, "--clean-output"),
        (settings.verify_hashes, "--verify-hashes"),
        (settings.force_reparse, "--force-reparse"),
        (settings.force_rescore, "--force-rescore"),
    ):
        if enabled:
            command.append(flag)

    numeric_options = {
        "--auto-identity-threshold": settings.auto_identity_threshold,
        "--clean-lyric-threshold": settings.clean_lyric_threshold,
        "--review-lyric-threshold": settings.review_lyric_threshold,
        "--line-match-threshold": settings.line_match_threshold,
        "--max-line-bucket": settings.max_line_bucket,
        "--max-title-bucket": settings.max_title_bucket,
        "--progress-every": settings.progress_every,
        "--parse-workers": settings.parse_workers,
        "--score-workers": settings.score_workers,
        "--parallel-parse-threshold": settings.parallel_parse_threshold,
        "--parallel-score-threshold": settings.parallel_score_threshold,
        "--score-batch-size": settings.score_batch_size,
        "--cache-write-batch-size": settings.cache_write_batch_size,
    }
    for flag, value in numeric_options.items():
        _append_option(command, flag, value)
    return command


def load_run_summary(out_dir: Path) -> Dict[str, Any]:
    summary_path = Path(out_dir) / "reports" / "run_summary.json"
    try:
        return json.loads(summary_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def review_cache_db_path(settings: DashboardSettings) -> Path:
    cache_dir = settings.cache_dir if settings.cache_dir else settings.output / "_cache"
    return cache_dir / "song_repo_cache.sqlite"


def default_opensong_output_dir(output_dir: Path) -> Path:
    return Path(output_dir) / DEFAULT_OPENSONG_EXPORT_FOLDER


def convert_existing_output_to_opensong(settings: DashboardSettings):
    output_dir = Path(settings.output)
    if not output_dir.exists() or not output_dir.is_dir():
        raise ValueError(f"Output folder does not exist:\n{output_dir}")
    target_dir = default_opensong_output_dir(output_dir)
    return convert_chordpro_output_to_opensong(output_dir, target_dir)


def open_review_resolver_for_settings(parent: tk.Misc, settings: DashboardSettings) -> None:
    open_review_resolver(parent, settings.output, cache_db_path=review_cache_db_path(settings))


def open_in_file_manager(path: Path) -> None:
    resolved = Path(path)
    if sys.platform.startswith("win"):
        os.startfile(str(resolved))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(resolved)])
    else:
        subprocess.Popen(["xdg-open", str(resolved)])


class SongRepoDashboard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Song Repository Dashboard")
        self.geometry("1180x760")
        self.minsize(1040, 680)
        self.configure(bg="#f4f6f8")

        self.process: Optional[subprocess.Popen[str]] = None
        self.log_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.metric_vars: Dict[str, tk.StringVar] = {}
        self.path_vars: Dict[str, tk.StringVar] = {
            "openlyrics": tk.StringVar(),
            "onsong": tk.StringVar(),
            "txt": tk.StringVar(),
            "output": tk.StringVar(value=str(APP_DIR / "song_repo_output")),
            "cache_dir": tk.StringVar(),
        }
        self.bool_vars: Dict[str, tk.BooleanVar] = {
            "dry_run": tk.BooleanVar(value=False),
            "clean_output": tk.BooleanVar(value=False),
            "verify_hashes": tk.BooleanVar(value=False),
            "force_reparse": tk.BooleanVar(value=False),
            "force_rescore": tk.BooleanVar(value=False),
        }
        self.preset_var = tk.StringVar(value="Balanced")
        self.status_var = tk.StringVar(value="Ready")
        self.detail_var = tk.StringVar(value="Choose folders, confirm processing settings, then start.")
        self.numeric_vars: Dict[str, tk.Variable] = {}

        self._create_styles()
        self._build_layout()
        self._load_settings()
        self.after(100, self._poll_log_queue)

    def _create_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("App.TFrame", background="#f4f6f8")
        style.configure("Panel.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("Inner.TFrame", background="#ffffff")
        style.configure("Metric.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("Header.TLabel", background="#f4f6f8", foreground="#152033", font=("Segoe UI", 20, "bold"))
        style.configure("Subtle.TLabel", background="#f4f6f8", foreground="#667085", font=("Segoe UI", 10))
        style.configure("PanelTitle.TLabel", background="#ffffff", foreground="#152033", font=("Segoe UI", 12, "bold"))
        style.configure("Body.TLabel", background="#ffffff", foreground="#344054", font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background="#ffffff", foreground="#667085", font=("Segoe UI", 9))
        style.configure("MetricValue.TLabel", background="#ffffff", foreground="#1d4ed8", font=("Segoe UI", 18, "bold"))
        style.configure("MetricLabel.TLabel", background="#ffffff", foreground="#667085", font=("Segoe UI", 9))
        style.configure("Status.TLabel", background="#e9f8f3", foreground="#067647", font=("Segoe UI", 10, "bold"), padding=(12, 5))
        style.configure("Danger.TLabel", background="#fff1f0", foreground="#b42318", font=("Segoe UI", 10, "bold"), padding=(12, 5))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 7))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 8), foreground="#ffffff", background="#1d4ed8")
        style.map("Accent.TButton", background=[("active", "#1e40af"), ("disabled", "#98a2b3")])
        style.configure("TEntry", padding=(7, 5))
        style.configure("TCombobox", padding=(7, 5))
        style.configure("TLabelframe", background="#ffffff", borderwidth=1)
        style.configure("TLabelframe.Label", background="#ffffff", foreground="#152033", font=("Segoe UI", 10, "bold"))

    def _build_layout(self) -> None:
        root = ttk.Frame(self, style="App.TFrame", padding=18)
        root.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)

        header = ttk.Frame(root, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.grid_columnconfigure(0, weight=1)
        ttk.Label(header, text="Song Repository Dashboard", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Deduplicate songs, preserve chords, and export the classified repository.",
            style="Subtle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.status_label = ttk.Label(header, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.grid(row=0, column=1, rowspan=2, sticky="e")

        metrics = ttk.Frame(root, style="App.TFrame")
        metrics.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        for idx in range(5):
            metrics.grid_columnconfigure(idx, weight=1)
        self._add_metric(metrics, 0, "Sources", "source_count")
        self._add_metric(metrics, 1, "Groups", "group_count")
        self._add_metric(metrics, 2, "Exported", "exported_count")
        self._add_metric(metrics, 3, "Conflicts", "title_lyric_conflicts")
        self._add_metric(metrics, 4, "Elapsed", "elapsed")

        body = ttk.Frame(root, style="App.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=0, minsize=430)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ttk.Frame(body, style="Panel.TFrame", padding=16)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        left.grid_columnconfigure(0, weight=1)
        self._build_settings_panel(left)

        right = ttk.Frame(body, style="Panel.TFrame", padding=16)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(4, weight=1)
        self._build_run_panel(right)

    def _add_metric(self, parent: ttk.Frame, column: int, label: str, key: str) -> None:
        frame = ttk.Frame(parent, style="Metric.TFrame", padding=(14, 10))
        frame.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        frame.grid_columnconfigure(0, weight=1)
        var = tk.StringVar(value="-")
        self.metric_vars[key] = var
        ttk.Label(frame, textvariable=var, style="MetricValue.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text=label, style="MetricLabel.TLabel").grid(row=1, column=0, sticky="w")

    def _build_settings_panel(self, parent: ttk.Frame) -> None:
        parent.grid_rowconfigure(2, weight=1)
        ttk.Label(parent, text="Setup", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")

        sources = ttk.LabelFrame(parent, text="Folders", padding=12)
        sources.grid(row=1, column=0, sticky="ew", pady=(12, 14))
        sources.grid_columnconfigure(1, weight=1)
        self._folder_row(sources, 0, "OpenLyrics", "openlyrics")
        self._folder_row(sources, 1, "OnSong", "onsong")
        self._folder_row(sources, 2, "TXT / ChordPro", "txt")
        self._folder_row(sources, 3, "Output", "output")
        self._folder_row(sources, 4, "Cache", "cache_dir")

        processing = ttk.LabelFrame(parent, text="Processing", padding=12)
        processing.grid(row=2, column=0, sticky="nsew")
        processing.grid_columnconfigure(1, weight=1)

        ttk.Label(processing, text="Preset", style="Body.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        preset = ttk.Combobox(
            processing,
            textvariable=self.preset_var,
            values=("Balanced", "Max speed", "Careful audit"),
            state="readonly",
            width=18,
        )
        preset.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        preset.bind("<<ComboboxSelected>>", lambda _event: self._apply_preset())

        checks = ttk.Frame(processing, style="Inner.TFrame")
        checks.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 12))
        checks.grid_columnconfigure(0, weight=1)
        checks.grid_columnconfigure(1, weight=1)
        labels = {
            "dry_run": "Dry run",
            "clean_output": "Clean output",
            "verify_hashes": "Verify hashes",
            "force_reparse": "Force reparse",
            "force_rescore": "Force rescore",
        }
        for idx, (key, text) in enumerate(labels.items()):
            ttk.Checkbutton(checks, text=text, variable=self.bool_vars[key]).grid(
                row=idx // 2,
                column=idx % 2,
                sticky="w",
                pady=2,
            )

        advanced = ttk.LabelFrame(processing, text="Performance settings", padding=10)
        advanced.grid(row=2, column=0, columnspan=2, sticky="ew")
        advanced.grid_columnconfigure(1, weight=1)
        advanced.grid_columnconfigure(3, weight=1)
        self._numeric_row(advanced, 0, "Parse workers", "parse_workers", int, 1, 64, 0)
        self._numeric_row(advanced, 0, "Score workers", "score_workers", int, 1, 64, 2)
        self._numeric_row(advanced, 1, "Parse threshold", "parallel_parse_threshold", int, 1, 100000, 0)
        self._numeric_row(advanced, 1, "Score threshold", "parallel_score_threshold", int, 1, 10000000, 2)
        self._numeric_row(advanced, 2, "Score batch", "score_batch_size", int, 1, 100000, 0)
        self._numeric_row(advanced, 2, "Cache batch", "cache_write_batch_size", int, 1, 100000, 2)
        self._numeric_row(advanced, 3, "Progress every", "progress_every", int, 0, 100000, 0)
        self._numeric_row(advanced, 3, "Line threshold", "line_match_threshold", float, 0.1, 1.0, 2)
        self._numeric_row(advanced, 4, "Auto threshold", "auto_identity_threshold", float, 0.1, 1.0, 0)
        self._numeric_row(advanced, 4, "Clean threshold", "clean_lyric_threshold", float, 0.1, 1.0, 2)
        self._numeric_row(advanced, 5, "Review threshold", "review_lyric_threshold", float, 0.1, 1.0, 0)
        self._numeric_row(advanced, 5, "Title bucket", "max_title_bucket", int, 1, 100000, 2)
        self._numeric_row(advanced, 6, "Line bucket", "max_line_bucket", int, 1, 100000, 0)

    def _folder_row(self, parent: ttk.Frame, row: int, label: str, key: str) -> None:
        ttk.Label(parent, text=label, style="Body.TLabel").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 8))
        entry = ttk.Entry(parent, textvariable=self.path_vars[key])
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(parent, text="Browse", command=lambda: self._browse_folder(key)).grid(
            row=row,
            column=2,
            sticky="e",
            padx=(8, 0),
            pady=4,
        )

    def _numeric_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        key: str,
        value_type: type,
        from_: float,
        to: float,
        column: int,
    ) -> None:
        preset = performance_preset(self.preset_var.get())
        variable: tk.Variable
        if value_type is int:
            variable = tk.IntVar(value=int(preset[key]))
            increment = 1
        else:
            variable = tk.DoubleVar(value=float(preset[key]))
            increment = 0.01
        self.numeric_vars[key] = variable
        ttk.Label(parent, text=label, style="Body.TLabel").grid(
            row=row,
            column=column,
            sticky="w",
            pady=4,
            padx=(0, 8),
        )
        spin = ttk.Spinbox(
            parent,
            textvariable=variable,
            from_=from_,
            to=to,
            increment=increment,
            width=9,
        )
        spin.grid(row=row, column=column + 1, sticky="ew", pady=4, padx=(0 if column == 0 else 8, 10))

    def _build_run_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="Run", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(parent, textvariable=self.detail_var, style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 12))

        controls = ttk.Frame(parent, style="Inner.TFrame")
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        controls.grid_columnconfigure(7, weight=1)
        self.start_button = ttk.Button(controls, text="Start", style="Accent.TButton", command=self._start_run)
        self.start_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.cancel_button = ttk.Button(controls, text="Cancel", command=self._cancel_run, state="disabled")
        self.cancel_button.grid(row=0, column=1, sticky="w", padx=(0, 8))
        ttk.Button(controls, text="Open output", command=self._open_output).grid(row=0, column=2, sticky="w", padx=(0, 8))
        ttk.Button(controls, text="Open reports", command=self._open_reports).grid(row=0, column=3, sticky="w", padx=(0, 8))
        ttk.Button(controls, text="Open summary", command=self._open_summary).grid(row=0, column=4, sticky="w")
        ttk.Button(controls, text="Review issues", command=self._open_review_issues).grid(
            row=0,
            column=5,
            sticky="w",
            padx=(8, 0),
        )
        ttk.Button(controls, text="Convert to OpenSong", command=self._convert_to_opensong).grid(
            row=0,
            column=6,
            sticky="w",
            padx=(8, 0),
        )

        self.progress = ttk.Progressbar(parent, mode="indeterminate")
        self.progress.grid(row=3, column=0, sticky="ew", pady=(0, 12))

        log_frame = ttk.Frame(parent, style="Inner.TFrame")
        log_frame.grid(row=4, column=0, sticky="nsew")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            wrap="word",
            height=20,
            bg="#111827",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            selectbackground="#2563eb",
            relief="flat",
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)
        self._append_log("Ready. Pick your folders and press Start.")

    def _browse_folder(self, key: str) -> None:
        initial = self.path_vars[key].get().strip() or str(APP_DIR)
        folder = filedialog.askdirectory(initialdir=initial, title="Choose folder")
        if folder:
            self.path_vars[key].set(folder)

    def _apply_preset(self) -> None:
        preset = performance_preset(self.preset_var.get())
        for key, value in preset.items():
            variable = self.numeric_vars.get(key)
            if variable is not None:
                variable.set(value)

    def _path_value(self, key: str) -> Optional[Path]:
        value = self.path_vars[key].get().strip()
        return Path(value) if value else None

    def _collect_settings(self) -> DashboardSettings:
        output = self._path_value("output")
        if output is None:
            raise ValueError("Choose an output folder.")
        values = {key: variable.get() for key, variable in self.numeric_vars.items()}
        int_keys = {
            "max_line_bucket",
            "max_title_bucket",
            "progress_every",
            "parse_workers",
            "score_workers",
            "parallel_parse_threshold",
            "parallel_score_threshold",
            "score_batch_size",
            "cache_write_batch_size",
        }
        for key in int_keys:
            values[key] = int(values[key])
        return DashboardSettings(
            openlyrics=self._path_value("openlyrics"),
            onsong=self._path_value("onsong"),
            txt=self._path_value("txt"),
            output=output,
            cache_dir=self._path_value("cache_dir"),
            preset=self.preset_var.get(),
            dry_run=self.bool_vars["dry_run"].get(),
            clean_output=self.bool_vars["clean_output"].get(),
            verify_hashes=self.bool_vars["verify_hashes"].get(),
            force_reparse=self.bool_vars["force_reparse"].get(),
            force_rescore=self.bool_vars["force_rescore"].get(),
            **values,
        )

    def _validate_settings(self, settings: DashboardSettings) -> None:
        sources = [settings.openlyrics, settings.onsong, settings.txt]
        if not any(sources):
            raise ValueError("Choose at least one source folder.")
        for source in sources:
            if source and not source.exists():
                raise ValueError(f"Source folder does not exist:\n{source}")
        if settings.output.exists() and not settings.output.is_dir():
            raise ValueError(f"Output path is not a folder:\n{settings.output}")
        if settings.cache_dir and settings.cache_dir.exists() and not settings.cache_dir.is_dir():
            raise ValueError(f"Cache path is not a folder:\n{settings.cache_dir}")

    def _save_settings(self, settings: DashboardSettings) -> None:
        try:
            SETTINGS_FILE.write_text(json.dumps(settings.to_json_dict(), indent=2), encoding="utf-8")
        except OSError:
            pass

    def _load_settings(self) -> None:
        if not SETTINGS_FILE.exists():
            self._apply_preset()
            return
        try:
            settings = DashboardSettings.from_json_dict(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            self._apply_preset()
            return
        for key in self.path_vars:
            value = getattr(settings, key)
            self.path_vars[key].set(str(value) if value else "")
        for key in self.bool_vars:
            self.bool_vars[key].set(bool(getattr(settings, key)))
        self.preset_var.set(settings.preset if settings.preset in ("Balanced", "Max speed", "Careful audit") else "Balanced")
        for key, variable in self.numeric_vars.items():
            variable.set(getattr(settings, key))
        self._refresh_summary_cards()

    def _start_run(self) -> None:
        try:
            settings = self._collect_settings()
            self._validate_settings(settings)
        except ValueError as exc:
            messagebox.showerror("Check settings", str(exc), parent=self)
            return

        settings.output.mkdir(parents=True, exist_ok=True)
        if settings.cache_dir:
            settings.cache_dir.mkdir(parents=True, exist_ok=True)
        self._save_settings(settings)

        command = build_builder_command(settings)
        self._clear_log()
        self._append_log("Starting song repository build...")
        self._append_log(f"Builder: {BUILDER_SCRIPT}")
        self._append_log(f"Output: {settings.output}")
        self._set_running(True)

        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(APP_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._set_running(False)
            messagebox.showerror("Could not start builder", str(exc), parent=self)
            return

        thread = threading.Thread(target=self._read_process_output, args=(self.process, settings.output), daemon=True)
        thread.start()

    def _read_process_output(self, process: subprocess.Popen[str], output_dir: Path) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self.log_queue.put(("log", line.rstrip()))
        return_code = process.wait()
        self.log_queue.put(("done", (return_code, output_dir)))

    def _cancel_run(self) -> None:
        if self.process and self.process.poll() is None:
            self._append_log("Cancel requested. Stopping the builder...")
            self.process.terminate()

    def _poll_log_queue(self) -> None:
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                    self._update_status_from_log(str(payload))
                elif kind == "done":
                    return_code, output_dir = payload
                    self.process = None
                    self._set_running(False)
                    if return_code == 0:
                        self.status_var.set("Finished")
                        self.status_label.configure(style="Status.TLabel")
                        self.detail_var.set("Run complete. Review the reports or open the exported folders.")
                    else:
                        self.status_var.set("Stopped" if return_code in (-15, 3221225786) else "Failed")
                        self.status_label.configure(style="Danger.TLabel")
                        self.detail_var.set(f"Builder exited with code {return_code}. Check the activity log.")
                    self._refresh_summary_cards(Path(output_dir))
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _set_running(self, running: bool) -> None:
        if running:
            self.status_var.set("Running")
            self.status_label.configure(style="Status.TLabel")
            self.detail_var.set("Processing songs. You can leave this window open and watch progress here.")
            self.start_button.configure(state="disabled")
            self.cancel_button.configure(state="normal")
            self.progress.start(12)
        else:
            self.start_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")
            self.progress.stop()

    def _update_status_from_log(self, line: str) -> None:
        if line.startswith("Discovered "):
            self.detail_var.set(line)
        elif line.startswith("Parsed "):
            self.detail_var.set(line)
        elif line.startswith("Candidate pairs:"):
            self.detail_var.set(line)
        elif line.startswith("Scored "):
            self.detail_var.set(line)
        elif line.startswith("Built "):
            self.detail_var.set(line)

    def _refresh_summary_cards(self, output_dir: Optional[Path] = None) -> None:
        if output_dir is None:
            output_dir = self._path_value("output")
        summary = load_run_summary(output_dir) if output_dir else {}
        self.metric_vars["source_count"].set(str(summary.get("source_count", "-")))
        self.metric_vars["group_count"].set(str(summary.get("group_count", "-")))
        self.metric_vars["exported_count"].set(str(summary.get("exported_count", "-")))
        conflicts = summary.get("title_lyric_conflicts")
        if conflicts is None:
            conflicts = self._conflict_count_from_reports(output_dir) if output_dir else "-"
        self.metric_vars["title_lyric_conflicts"].set(str(conflicts))
        timings = summary.get("phase_timings", {})
        elapsed = timings.get("total_seconds") if isinstance(timings, dict) else None
        self.metric_vars["elapsed"].set(self._format_seconds(elapsed) if elapsed is not None else "-")

    def _conflict_count_from_reports(self, output_dir: Optional[Path]) -> Any:
        if output_dir is None:
            return "-"
        conflict_report = output_dir / "reports" / "08_same_title_different_lyrics.csv"
        try:
            with conflict_report.open("r", encoding="utf-8") as handle:
                return max(0, sum(1 for _line in handle) - 1)
        except OSError:
            return "-"

    @staticmethod
    def _format_seconds(value: Any) -> str:
        try:
            seconds = int(float(value))
        except (TypeError, ValueError):
            return "-"
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {sec}s"
        return f"{sec}s"

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _open_output(self) -> None:
        path = self._path_value("output")
        if path:
            path.mkdir(parents=True, exist_ok=True)
            open_in_file_manager(path)

    def _open_reports(self) -> None:
        path = self._path_value("output")
        if path:
            reports = path / "reports"
            reports.mkdir(parents=True, exist_ok=True)
            open_in_file_manager(reports)

    def _open_summary(self) -> None:
        path = self._path_value("output")
        if not path:
            return
        summary = path / "reports" / "run_summary.json"
        if summary.exists():
            webbrowser.open(summary.as_uri())
        else:
            messagebox.showinfo("Summary not found", "Run the builder first to create run_summary.json.", parent=self)

    def _open_review_issues(self) -> None:
        try:
            settings = self._collect_settings()
        except ValueError as exc:
            messagebox.showerror("Check settings", str(exc), parent=self)
            return
        settings.output.mkdir(parents=True, exist_ok=True)
        open_review_resolver_for_settings(self, settings)

    def _convert_to_opensong(self) -> None:
        try:
            settings = self._collect_settings()
            summary = convert_existing_output_to_opensong(settings)
        except Exception as exc:
            messagebox.showerror("Could not convert to OpenSong", str(exc), parent=self)
            return

        self._save_settings(settings)
        self._append_log(
            f"Converted {summary.converted_count} ChordPro file(s) to OpenSong: {summary.output_dir}"
        )
        if summary.error_count:
            self._append_log(f"OpenSong conversion skipped {summary.error_count} file(s):")
            for error in summary.errors[:20]:
                self._append_log(f"  {error}")
            if len(summary.errors) > 20:
                self._append_log(f"  ... {len(summary.errors) - 20} more")
        self.detail_var.set(f"OpenSong export ready: {summary.output_dir}")
        open_in_file_manager(summary.output_dir)


def main() -> None:
    app = SongRepoDashboard()
    app.mainloop()


if __name__ == "__main__":
    main()
