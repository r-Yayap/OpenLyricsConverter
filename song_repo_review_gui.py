#!/usr/bin/env python3
"""Tkinter window for post-processing song repository review items."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import messagebox, ttk

import song_repo_review as review


ACTION_CHOICES = (
    ("Accept current", "accept_current", None),
    ("Move to clean match", "move_to_clean_match", "clean_match"),
    ("Move to lyrics only", "move_to_lyrics_only", "lyrics_only"),
    ("Move to title conflict", "move_to_title_match_lyrics_different", "title_match_lyrics_different"),
    ("Move to multiple chorded", "move_to_multiple_chorded_sources", "multiple_chorded_sources"),
    ("Keep needs review", "keep_needs_review", "needs_review"),
    ("Skip", "skip", None),
)


def open_path(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class ReviewResolverWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, output_dir: Path, cache_db_path: Optional[Path] = None) -> None:
        super().__init__(parent)
        self.output_dir = Path(output_dir)
        self.cache_db_path = cache_db_path
        self.candidates: List[review.ReviewCandidate] = []
        self.candidate_by_item: Dict[str, review.ReviewCandidate] = {}

        self.title("Manual Review Resolver")
        self.geometry("1180x760")
        self.minsize(980, 620)
        self.configure(bg="#f4f6f8")

        self.status_var = tk.StringVar(value="Ready")
        self.action_var = tk.StringVar(value=ACTION_CHOICES[0][0])
        self.note_var = tk.StringVar()

        self._build_layout()
        self.refresh()

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(1, weight=1)

        toolbar = ttk.Frame(root)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        toolbar.grid_columnconfigure(0, weight=1)
        ttk.Label(toolbar, text=f"Output: {self.output_dir}").grid(row=0, column=0, sticky="w")
        ttk.Button(toolbar, text="Refresh", command=self.refresh).grid(row=0, column=1, padx=(12, 0))
        ttk.Button(toolbar, text="Open file", command=self.open_selected_file).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(toolbar, text="Open folder", command=self.open_selected_folder).grid(row=0, column=3, padx=(8, 0))

        self.tree = ttk.Treeview(
            root,
            columns=("group", "class", "title", "creator", "score", "status"),
            show="headings",
            height=20,
        )
        for column, label, width in (
            ("group", "Group", 80),
            ("class", "Classification", 170),
            ("title", "Title", 220),
            ("creator", "Artist/Author", 160),
            ("score", "Score", 70),
            ("status", "Status", 90),
        ):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, anchor="w")
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.show_selected())

        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew")
        right.grid_rowconfigure(2, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.summary = tk.Text(right, height=8, wrap="word")
        self.summary.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.summary.configure(state="disabled")

        controls = ttk.Frame(right)
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(controls, text="Action").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            controls,
            textvariable=self.action_var,
            values=[choice[0] for choice in ACTION_CHOICES],
            state="readonly",
            width=28,
        ).grid(row=0, column=1, sticky="w", padx=(8, 12))
        ttk.Label(controls, text="Note").grid(row=0, column=2, sticky="w")
        ttk.Entry(controls, textvariable=self.note_var, width=36).grid(row=0, column=3, sticky="ew", padx=(8, 12))
        controls.grid_columnconfigure(3, weight=1)
        ttk.Button(controls, text="Apply", command=self.apply_selected_action).grid(row=0, column=4, sticky="e")

        panes = ttk.PanedWindow(right, orient="horizontal")
        panes.grid(row=2, column=0, sticky="nsew")
        source_frame = ttk.Frame(panes)
        export_frame = ttk.Frame(panes)
        source_frame.grid_rowconfigure(1, weight=1)
        source_frame.grid_columnconfigure(0, weight=1)
        export_frame.grid_rowconfigure(1, weight=1)
        export_frame.grid_columnconfigure(0, weight=1)
        ttk.Label(source_frame, text="Source text").grid(row=0, column=0, sticky="w")
        ttk.Label(export_frame, text="Exported ChordPro").grid(row=0, column=0, sticky="w")
        self.source_text = tk.Text(source_frame, wrap="none", font=("Consolas", 10))
        self.export_text = tk.Text(export_frame, wrap="none", font=("Consolas", 10))
        self.source_text.grid(row=1, column=0, sticky="nsew")
        self.export_text.grid(row=1, column=0, sticky="nsew")
        self.source_text.configure(state="disabled")
        self.export_text.configure(state="disabled")
        panes.add(source_frame, weight=1)
        panes.add(export_frame, weight=1)

        ttk.Label(root, textvariable=self.status_var).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def refresh(self) -> None:
        self.candidates = review.load_review_candidates(self.output_dir)
        self.candidate_by_item.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)
        for candidate in self.candidates:
            creator = candidate.artist or candidate.author
            item = self.tree.insert(
                "",
                "end",
                values=(
                    candidate.group_id,
                    candidate.classification,
                    candidate.title,
                    creator,
                    candidate.best_lyric_identity_score,
                    candidate.status,
                ),
            )
            self.candidate_by_item[item] = candidate
        self.status_var.set(f"Loaded {len(self.candidates)} review item(s).")

    def selected_candidate(self) -> Optional[review.ReviewCandidate]:
        selected = self.tree.selection()
        if not selected:
            return None
        return self.candidate_by_item.get(selected[0])

    def show_selected(self) -> None:
        candidate = self.selected_candidate()
        if not candidate:
            return
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert(
            "end",
            f"Group: {candidate.group_id}\n"
            f"Classification: {candidate.classification}\n"
            f"Title: {candidate.title}\n"
            f"Artist: {candidate.artist}\n"
            f"Author: {candidate.author}\n"
            f"Reason: {candidate.canonical_reason}\n"
            f"Members: {candidate.members}\n"
            f"Pair details: {len(candidate.pair_details)} row(s)\n"
            f"Conflict details: {len(candidate.conflict_details)} row(s)\n"
            f"Export: {candidate.export_path}\n",
        )
        self.summary.configure(state="disabled")

        self.source_text.configure(state="normal")
        self.source_text.delete("1.0", "end")
        self.source_text.insert("end", candidate.source_text or "Source text unavailable.")
        self.source_text.configure(state="disabled")

        self.export_text.configure(state="normal")
        self.export_text.delete("1.0", "end")
        self.export_text.insert("end", candidate.export_text)
        self.export_text.configure(state="disabled")

    def action_choice(self) -> tuple[str, str]:
        label = self.action_var.get()
        for choice_label, action, class_key in ACTION_CHOICES:
            if choice_label == label:
                return action, class_key or ""
        return "accept_current", ""

    def apply_selected_action(self) -> None:
        candidate = self.selected_candidate()
        if not candidate:
            messagebox.showinfo("No item selected", "Select a review item first.", parent=self)
            return
        action, class_key = self.action_choice()
        chosen = class_key or candidate.classification
        try:
            decision = review.apply_review_decision(
                self.output_dir,
                candidate,
                chosen_classification=chosen,
                action=action,
                note=self.note_var.get().strip(),
                cache_db_path=self.cache_db_path,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not apply decision", str(exc), parent=self)
            return
        self.status_var.set(f"Saved {decision.group_id}: {decision.chosen_classification}")
        self.refresh()

    def open_selected_file(self) -> None:
        candidate = self.selected_candidate()
        if candidate and candidate.export_path.exists():
            open_path(candidate.export_path)

    def open_selected_folder(self) -> None:
        candidate = self.selected_candidate()
        if candidate:
            open_path(candidate.export_path.parent)


def open_review_resolver(
    parent: tk.Misc,
    output_dir: Path,
    cache_db_path: Optional[Path] = None,
) -> ReviewResolverWindow:
    return ReviewResolverWindow(parent, output_dir, cache_db_path=cache_db_path)
