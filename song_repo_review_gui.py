#!/usr/bin/env python3
"""Tkinter window for post-processing song repository review items."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import messagebox, ttk

import song_repo_review as review


ISSUE_FILTER_OPTIONS = (
    ("All issues", "all"),
    ("04 lyrics match, titles differ", "lyric_match_title_different"),
    ("05 same title, different lyrics", "title_match_lyrics_different"),
    ("06 multiple chorded sources", "multiple_chorded_sources"),
    ("08 needs review", "needs_review"),
)
STATUS_FILTER_OPTIONS = (
    ("All statuses", "all"),
    ("Unresolved", "unresolved"),
    ("Resolved", "resolved"),
)
ACTION_BUTTONS = (
    ("use_selected_source", "Use Selected Source", ("use_selected_source",)),
    ("use_this_title", "Use This Title", ("use_this_title",)),
    ("keep_all", "Keep Both/All", ("keep_both", "retain_all")),
    ("mark_same_song", "Mark Same Song", ("mark_same_song",)),
    ("keep_unresolved", "Keep Unresolved", ("keep_unresolved",)),
    ("skip", "Skip", ("skip",)),
)
META_LINE_RE = re.compile(r"^\s*(?:\{[^}]+:\s*.*\}|(?:Title|Artist|Author|Key|Original Key|Book|Notes|Scripture):)", re.I)
CHORD_RE = re.compile(r"\[[A-G](?:#|b)?[A-Za-z0-9/#().+-]*\]")


def open_path(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def _option_value(options: tuple[tuple[str, str], ...], label: str) -> str:
    for option_label, value in options:
        if option_label == label:
            return value
    return "all"


def _line_norm(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip().lower())


class ReviewResolverWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc, output_dir: Path, cache_db_path: Optional[Path] = None) -> None:
        super().__init__(parent)
        self.output_dir = Path(output_dir)
        self.cache_db_path = cache_db_path
        self.all_candidates: List[review.ReviewCandidate] = []
        self.candidates: List[review.ReviewCandidate] = []
        self.candidate_by_item: Dict[str, review.ReviewCandidate] = {}
        self.member_by_item: Dict[str, review.ReviewMember] = {}
        self.action_by_button: Dict[str, Dict[str, str]] = {}
        self.sort_column = "type"
        self.sort_reverse = False

        self.title("Manual Review Resolver")
        self.geometry("1280x820")
        self.minsize(1040, 680)
        self.configure(bg="#f4f6f8")

        self.status_var = tk.StringVar(value="Ready")
        self.issue_filter_var = tk.StringVar(value=ISSUE_FILTER_OPTIONS[0][0])
        self.review_status_filter_var = tk.StringVar(value=STATUS_FILTER_OPTIONS[1][0])
        self.search_var = tk.StringVar()
        self.note_var = tk.StringVar()

        self._build_layout()
        self.refresh()

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)
        root.grid_columnconfigure(1, weight=1)

        toolbar = ttk.Frame(root)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        toolbar.grid_columnconfigure(0, weight=1)
        ttk.Label(toolbar, text=f"Output: {self.output_dir}").grid(row=0, column=0, sticky="w")
        ttk.Button(toolbar, text="Refresh", command=self.refresh).grid(row=0, column=1, padx=(12, 0))
        ttk.Button(toolbar, text="Open file", command=self.open_selected_file).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(toolbar, text="Open folder", command=self.open_selected_folder).grid(row=0, column=3, padx=(8, 0))

        filters = ttk.Frame(root)
        filters.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        filters.grid_columnconfigure(5, weight=1)
        ttk.Label(filters, text="Issue").grid(row=0, column=0, sticky="w")
        issue_combo = ttk.Combobox(
            filters,
            textvariable=self.issue_filter_var,
            values=[label for label, _value in ISSUE_FILTER_OPTIONS],
            state="readonly",
            width=32,
        )
        issue_combo.grid(row=0, column=1, sticky="w", padx=(6, 14))
        issue_combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_filters())

        ttk.Label(filters, text="Status").grid(row=0, column=2, sticky="w")
        status_combo = ttk.Combobox(
            filters,
            textvariable=self.review_status_filter_var,
            values=[label for label, _value in STATUS_FILTER_OPTIONS],
            state="readonly",
            width=16,
        )
        status_combo.grid(row=0, column=3, sticky="w", padx=(6, 14))
        status_combo.bind("<<ComboboxSelected>>", lambda _event: self.apply_filters())

        ttk.Label(filters, text="Search").grid(row=0, column=4, sticky="w")
        search_entry = ttk.Entry(filters, textvariable=self.search_var)
        search_entry.grid(row=0, column=5, sticky="ew", padx=(6, 8))
        self.search_var.trace_add("write", lambda *_args: self.apply_filters())
        ttk.Button(filters, text="Clear", command=self.clear_filters).grid(row=0, column=6, sticky="e")

        self.tree = ttk.Treeview(
            root,
            columns=("type", "title", "creator", "score", "sources", "status", "reason"),
            show="headings",
            height=20,
        )
        for column, label, width in (
            ("type", "Issue", 155),
            ("title", "Title", 220),
            ("creator", "Artist/Author", 150),
            ("score", "Score", 65),
            ("sources", "Sources", 65),
            ("status", "Status", 85),
            ("reason", "Reason", 260),
        ):
            self.tree.heading(column, text=label, command=lambda col=column: self.sort_by(col))
            self.tree.column(column, width=width, anchor="w")
        self.tree.grid(row=2, column=0, sticky="nsew", padx=(0, 10))
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.show_selected())
        self.tree.tag_configure("lyric_match_title_different", background="#eef6ff")
        self.tree.tag_configure("title_match_lyrics_different", background="#fff7ed")
        self.tree.tag_configure("multiple_chorded_sources", background="#ecfdf5")
        self.tree.tag_configure("needs_review", background="#fefce8")

        right = ttk.Frame(root)
        right.grid(row=2, column=1, sticky="nsew")
        right.grid_rowconfigure(3, weight=1)
        right.grid_columnconfigure(0, weight=1)

        issue_frame = ttk.LabelFrame(right, text="Issue details")
        issue_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        issue_frame.grid_columnconfigure(0, weight=1)
        self.summary = tk.Text(issue_frame, height=10, wrap="word")
        self.summary.grid(row=0, column=0, sticky="ew")
        self.summary.tag_configure("section", foreground="#111827", font=("TkDefaultFont", 9, "bold"))
        self.summary.tag_configure("muted", foreground="#4b5563")
        self.summary.configure(state="disabled")

        members_frame = ttk.LabelFrame(right, text="Matched sources")
        members_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        members_frame.grid_columnconfigure(0, weight=1)
        self.member_tree = ttk.Treeview(
            members_frame,
            columns=("source", "title", "path"),
            show="headings",
            height=4,
        )
        for column, label, width in (
            ("source", "Source", 150),
            ("title", "Title", 190),
            ("path", "Path", 420),
        ):
            self.member_tree.heading(column, text=label)
            self.member_tree.column(column, width=width, anchor="w")
        self.member_tree.grid(row=0, column=0, sticky="ew")

        controls = ttk.Frame(right)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        controls.grid_columnconfigure(1, weight=1)
        ttk.Label(controls, text="Note").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.note_var).grid(row=0, column=1, sticky="ew", padx=(8, 12))
        self.action_buttons: Dict[str, ttk.Button] = {}
        for index, (button_id, label, _actions) in enumerate(ACTION_BUTTONS):
            button = ttk.Button(controls, text=label, command=lambda key=button_id: self.apply_button_action(key))
            button.grid(row=0, column=2 + index, sticky="e", padx=(0 if index == 0 else 6, 0))
            self.action_buttons[button_id] = button

        panes = ttk.PanedWindow(right, orient="horizontal")
        panes.grid(row=3, column=0, sticky="nsew")
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
        self._configure_text_tags(self.source_text, "source_only")
        self._configure_text_tags(self.export_text, "export_only")
        self.source_text.configure(state="disabled")
        self.export_text.configure(state="disabled")
        panes.add(source_frame, weight=1)
        panes.add(export_frame, weight=1)

        ttk.Label(root, textvariable=self.status_var).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self._update_action_buttons(None)

    def _configure_text_tags(self, widget: tk.Text, side_only_tag: str) -> None:
        widget.tag_configure("meta", foreground="#1d4ed8")
        widget.tag_configure("chord", foreground="#047857")
        widget.tag_configure("match", foreground="#111827")
        widget.tag_configure("missing", foreground="#6b7280")
        widget.tag_configure(side_only_tag, background="#fee2e2" if side_only_tag == "source_only" else "#fef3c7")

    def refresh(self) -> None:
        selected = self.selected_candidate()
        selected_group = selected.group_id if selected else ""
        self.status_var.set("Loading review items...")
        self.update_idletasks()
        try:
            self.all_candidates = review.load_review_candidates(
                self.output_dir,
                include_text=False,
                include_details=False,
            )
        except Exception as exc:
            self.all_candidates = []
            self.candidates = []
            self.status_var.set(f"Could not load review items: {exc}")
            messagebox.showerror("Could not load review items", str(exc), parent=self)
            self._populate_tree("")
            return
        self.apply_filters(selected_group=selected_group)

    def clear_filters(self) -> None:
        self.issue_filter_var.set(ISSUE_FILTER_OPTIONS[0][0])
        self.review_status_filter_var.set(STATUS_FILTER_OPTIONS[1][0])
        self.search_var.set("")
        self.apply_filters()

    def issue_filter_key(self) -> str:
        return _option_value(ISSUE_FILTER_OPTIONS, self.issue_filter_var.get())

    def status_filter_key(self) -> str:
        return _option_value(STATUS_FILTER_OPTIONS, self.review_status_filter_var.get())

    def apply_filters(self, selected_group: str = "") -> None:
        selected = self.selected_candidate()
        selected_group = selected_group or (selected.group_id if selected else "")
        self.candidates = review.filter_candidates(
            self.all_candidates,
            self.issue_filter_key(),
            self.status_filter_key(),
            self.search_var.get(),
        )
        self.candidates.sort(key=lambda item: self._sort_value(item, self.sort_column), reverse=self.sort_reverse)
        self._populate_tree(selected_group)

    def sort_by(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self.apply_filters()

    def _sort_value(self, candidate: review.ReviewCandidate, column: str) -> object:
        if column == "type":
            return candidate.classification
        if column == "title":
            return candidate.title.lower()
        if column == "creator":
            return (candidate.artist or candidate.author).lower()
        if column == "score":
            try:
                return float(candidate.best_lyric_identity_score or 0.0)
            except ValueError:
                return 0.0
        if column == "sources":
            return len(candidate.members_list or review.parse_members(candidate.members))
        if column == "status":
            return candidate.status
        if column == "reason":
            return (candidate.classification_reason or candidate.canonical_reason).lower()
        return candidate.group_id

    def _populate_tree(self, selected_group: str) -> None:
        self.candidate_by_item.clear()
        for item in self.tree.get_children():
            self.tree.delete(item)

        item_to_select = ""
        for candidate in self.candidates:
            creator = candidate.artist or candidate.author
            source_count = len(candidate.members_list or review.parse_members(candidate.members))
            reason = candidate.classification_reason or candidate.canonical_reason
            explanation = review.issue_explanation(candidate)
            item = self.tree.insert(
                "",
                "end",
                values=(
                    explanation.label,
                    candidate.title,
                    creator,
                    candidate.best_lyric_identity_score,
                    source_count,
                    candidate.status,
                    reason,
                ),
                tags=(candidate.classification,),
            )
            self.candidate_by_item[item] = candidate
            if selected_group and candidate.group_id == selected_group:
                item_to_select = item

        children = self.tree.get_children()
        if children:
            self.tree.selection_set(item_to_select or children[0])
            self.tree.focus(item_to_select or children[0])
            self.show_selected()
        else:
            self.clear_details()
        total = len(self.all_candidates)
        shown = len(self.candidates)
        self.status_var.set(f"Showing {shown} of {total} review item(s).")

    def selected_candidate(self) -> Optional[review.ReviewCandidate]:
        selected = self.tree.selection()
        if not selected:
            return None
        return self.candidate_by_item.get(selected[0])

    def selected_member(self) -> Optional[review.ReviewMember]:
        selected = self.member_tree.selection()
        if not selected:
            return None
        return self.member_by_item.get(selected[0])

    def clear_details(self) -> None:
        self._set_summary_text("")
        self._populate_members(None)
        self._set_text(self.source_text, "", "", "source_only", "Source text unavailable.")
        self._set_text(self.export_text, "", "", "export_only", "Exported ChordPro unavailable.")
        self._update_action_buttons(None)

    def show_selected(self) -> None:
        candidate = self.selected_candidate()
        if not candidate:
            self.clear_details()
            return
        if not candidate.export_text and candidate.export_path.exists():
            self.status_var.set(f"Loading {candidate.title}...")
            self.update_idletasks()
            try:
                candidate = review.load_candidate_details(self.output_dir, candidate)
            except Exception as exc:
                self.status_var.set(f"Could not load item details: {exc}")
                messagebox.showerror("Could not load item details", str(exc), parent=self)
                return
        self._set_summary(candidate)
        self._populate_members(candidate)
        self._set_text(self.source_text, candidate.source_text, candidate.export_text, "source_only", "Source text unavailable.")
        self._set_text(self.export_text, candidate.export_text, candidate.source_text, "export_only", "Exported ChordPro unavailable.")
        self._update_action_buttons(candidate)

    def _set_summary_text(self, text: str) -> None:
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        if text:
            self.summary.insert("end", text)
        self.summary.configure(state="disabled")

    def _set_summary(self, candidate: review.ReviewCandidate) -> None:
        explanation = review.issue_explanation(candidate)
        details = [
            ("WHY THIS IS HERE", explanation.why),
            ("WHAT TO CHECK", explanation.what_to_check),
            ("EVIDENCE", explanation.evidence),
            ("SUGGESTED ACTION", explanation.suggested_action),
            (
                "FILES",
                f"Export: {candidate.export_path}\n"
                f"Canonical source: {candidate.source_path or '(not reported)'}\n"
                f"Pair score rows: {len(candidate.pair_details)} | Conflict rows: {len(candidate.conflict_details)}",
            ),
        ]
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        for heading, body in details:
            self.summary.insert("end", f"{heading}\n", "section")
            self.summary.insert("end", f"{body}\n\n", "muted")
        self.summary.configure(state="disabled")

    def _populate_members(self, candidate: Optional[review.ReviewCandidate]) -> None:
        self.member_by_item.clear()
        for item in self.member_tree.get_children():
            self.member_tree.delete(item)
        if not candidate:
            return
        members = candidate.members_list or review.parse_members(candidate.members)
        for member in members:
            item = self.member_tree.insert("", "end", values=(member.source_repo, member.title, member.source_path))
            self.member_by_item[item] = member
        children = self.member_tree.get_children()
        if children:
            self.member_tree.selection_set(children[0])
            self.member_tree.focus(children[0])

    def _set_text(self, widget: tk.Text, text: str, other_text: str, side_only_tag: str, empty_message: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if not text:
            widget.insert("end", empty_message, "missing")
            widget.configure(state="disabled")
            return
        other_lines = {_line_norm(line) for line in other_text.splitlines() if _line_norm(line)}
        for line in text.splitlines():
            norm = _line_norm(line)
            tag = self._line_tag(line, norm, other_lines, side_only_tag)
            widget.insert("end", line + "\n", tag)
        widget.configure(state="disabled")

    def _line_tag(self, line: str, norm: str, other_lines: set[str], side_only_tag: str) -> str:
        if norm and norm not in other_lines:
            return side_only_tag
        if META_LINE_RE.search(line):
            return "meta"
        if CHORD_RE.search(line):
            return "chord"
        return "match"

    def _update_action_buttons(self, candidate: Optional[review.ReviewCandidate]) -> None:
        self.action_by_button.clear()
        if not candidate:
            for button in self.action_buttons.values():
                button.configure(state="disabled")
            return
        available = {action["action"]: action for action in review.available_actions(candidate)}
        for button_id, _label, action_keys in ACTION_BUTTONS:
            action = next((available[key] for key in action_keys if key in available), None)
            if action:
                self.action_by_button[button_id] = action
                self.action_buttons[button_id].configure(state="normal")
            else:
                self.action_buttons[button_id].configure(state="disabled")

    def _note_for_action(self, candidate: review.ReviewCandidate, action: str) -> str:
        notes = []
        user_note = self.note_var.get().strip()
        if user_note:
            notes.append(user_note)
        if action == "use_selected_source":
            member = self.selected_member()
            if member:
                notes.append(
                    f"selected_source={member.source_repo}; selected_title={member.title}; selected_path={member.source_path}"
                )
            elif candidate.members_list:
                raise ValueError("Select a source row before using 'Use Selected Source'.")
            elif candidate.source_path:
                notes.append(f"selected_source={candidate.source_repo}; selected_path={candidate.source_path}")
        elif action == "use_this_title":
            notes.append(f"selected_title={candidate.title}")
        return " | ".join(notes)

    def apply_button_action(self, button_id: str) -> None:
        candidate = self.selected_candidate()
        if not candidate:
            messagebox.showinfo("No item selected", "Select a review item first.", parent=self)
            return
        action = self.action_by_button.get(button_id)
        if not action:
            messagebox.showinfo("Action unavailable", "That action is not available for this issue type.", parent=self)
            return
        if action["action"] == "skip":
            self.select_next_item()
            self.status_var.set(f"Skipped {candidate.title}.")
            return
        try:
            note = self._note_for_action(candidate, action["action"])
            decision = review.apply_review_decision(
                self.output_dir,
                candidate,
                chosen_classification=action["chosen_classification"],
                action=action["action"],
                note=note,
                cache_db_path=self.cache_db_path,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not apply decision", str(exc), parent=self)
            return
        self.note_var.set("")
        self.status_var.set(f"Saved {decision.group_id}: {decision.action} -> {decision.chosen_classification}")
        self.refresh()

    def select_next_item(self) -> None:
        children = list(self.tree.get_children())
        if not children:
            return
        selected = self.tree.selection()
        if not selected:
            self.tree.selection_set(children[0])
            self.tree.focus(children[0])
            return
        index = children.index(selected[0])
        next_item = children[index + 1] if index + 1 < len(children) else children[0]
        self.tree.selection_set(next_item)
        self.tree.focus(next_item)
        self.tree.see(next_item)
        self.show_selected()

    def open_selected_file(self) -> None:
        candidate = self.selected_candidate()
        if not candidate:
            messagebox.showinfo("No item selected", "Select a review item first.", parent=self)
            return
        if not candidate.export_path.exists():
            messagebox.showinfo("File not found", f"Could not find:\n{candidate.export_path}", parent=self)
            return
        open_path(candidate.export_path)

    def open_selected_folder(self) -> None:
        candidate = self.selected_candidate()
        if not candidate:
            messagebox.showinfo("No item selected", "Select a review item first.", parent=self)
            return
        open_path(candidate.export_path.parent)


def open_review_resolver(
    parent: tk.Misc,
    output_dir: Path,
    cache_db_path: Optional[Path] = None,
) -> ReviewResolverWindow:
    return ReviewResolverWindow(parent, output_dir, cache_db_path=cache_db_path)
