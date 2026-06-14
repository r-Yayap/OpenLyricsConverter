#!/usr/bin/env python3
"""PySide6 manual review resolver focused on matched-source comparison."""

from __future__ import annotations

import argparse
import html
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

import song_repo_review as review


ISSUE_OPTIONS = (
    ("All issues", "all"),
    ("04 lyrics match, titles differ", "lyric_match_title_different"),
    ("05 same title, different lyrics", "title_match_lyrics_different"),
    ("06 multiple chorded sources", "multiple_chorded_sources"),
    ("08 needs review", "needs_review"),
)
STATUS_OPTIONS = (
    ("Unresolved", "unresolved"),
    ("Resolved", "resolved"),
    ("All statuses", "all"),
)
ISSUE_COLORS = {
    "lyric_match_title_different": QtGui.QColor("#dbeafe"),
    "title_match_lyrics_different": QtGui.QColor("#ffedd5"),
    "multiple_chorded_sources": QtGui.QColor("#dcfce7"),
    "needs_review": QtGui.QColor("#fef9c3"),
}
DIFF_COLORS = {
    "equal": "#f8fafc",
    "metadata": "#dbeafe",
    "chord_change": "#dcfce7",
    "changed": "#e0e7ff",
    "a_only": "#fee2e2",
    "b_only": "#fef3c7",
}


class CandidateDetails:
    def __init__(
        self,
        request_id: int,
        candidate: review.ReviewCandidate,
        pair: review.SelectedComparisonPair,
        diff: review.SongDiffResult,
    ) -> None:
        self.request_id = request_id
        self.candidate = candidate
        self.pair = pair
        self.diff = diff


class DetailsWorker(QtCore.QObject):
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(int, str)

    def __init__(self, request_id: int, out_dir: Path, candidate: review.ReviewCandidate) -> None:
        super().__init__()
        self.request_id = request_id
        self.out_dir = Path(out_dir)
        self.candidate = candidate

    @QtCore.Slot()
    def run(self) -> None:
        try:
            candidate = review.load_candidate_details(self.out_dir, self.candidate)
            pair = review.select_default_comparison_pair(candidate)
            pair = review.load_comparison_pair_texts(self.out_dir, pair)
            if pair.source_b.is_export and not pair.source_b.text:
                pair.source_b.text = candidate.export_text
            diff = review.compute_song_diff(pair.source_a.text, pair.source_b.text, candidate.classification)
            self.finished.emit(CandidateDetails(self.request_id, candidate, pair, diff))
        except Exception as exc:
            self.failed.emit(self.request_id, str(exc))


class ReviewResolverWindow(QtWidgets.QMainWindow):
    def __init__(self, output_dir: Path, cache_db_path: Optional[Path] = None) -> None:
        super().__init__()
        self.output_dir = Path(output_dir)
        self.cache_db_path = cache_db_path
        self.all_candidates: List[review.ReviewCandidate] = []
        self.candidates: List[review.ReviewCandidate] = []
        self.current_candidate: Optional[review.ReviewCandidate] = None
        self.current_pair: Optional[review.SelectedComparisonPair] = None
        self.current_diff: Optional[review.SongDiffResult] = None
        self.source_rows: List[review.ComparisonSource] = []
        self._preferred_row_after_refresh: Optional[int] = None
        self._request_id = 0
        self._worker_threads: List[QtCore.QThread] = []
        self._workers: List[DetailsWorker] = []
        self._action_buttons: List[QtWidgets.QPushButton] = []

        self.setWindowTitle("Manual Review Resolver")
        self.resize(1440, 900)
        self.setMinimumSize(1160, 720)
        self._build_ui()
        self._apply_style()
        self.refresh()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(central)
        layout.setContentsMargins(14, 14, 14, 10)
        layout.setSpacing(10)
        self.setCentralWidget(central)

        header = QtWidgets.QHBoxLayout()
        self.output_label = QtWidgets.QLabel(f"Output: {self.output_dir}")
        self.output_label.setObjectName("muted")
        refresh_btn = QtWidgets.QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        open_output_btn = QtWidgets.QPushButton("Open Output")
        open_output_btn.clicked.connect(lambda: self._open_path(self.output_dir))
        open_reports_btn = QtWidgets.QPushButton("Open Reports")
        open_reports_btn.clicked.connect(lambda: self._open_path(self.output_dir / "reports"))
        header.addWidget(self.output_label, 1)
        header.addWidget(refresh_btn)
        header.addWidget(open_output_btn)
        header.addWidget(open_reports_btn)
        layout.addLayout(header)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        layout.addWidget(splitter, 1)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([430, 1000])

        self.status_label = QtWidgets.QLabel("Ready")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

    def _build_left_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        filters = QtWidgets.QGridLayout()
        self.issue_filter = QtWidgets.QComboBox()
        for label, value in ISSUE_OPTIONS:
            self.issue_filter.addItem(label, value)
        self.status_filter = QtWidgets.QComboBox()
        for label, value in STATUS_OPTIONS:
            self.status_filter.addItem(label, value)
        self.search_box = QtWidgets.QLineEdit()
        self.search_box.setPlaceholderText("Search title, source, reason...")
        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self.clear_filters)
        self.issue_filter.currentIndexChanged.connect(self.apply_filters)
        self.status_filter.currentIndexChanged.connect(self.apply_filters)
        self.search_box.textChanged.connect(self.apply_filters)
        filters.addWidget(QtWidgets.QLabel("Issue"), 0, 0)
        filters.addWidget(self.issue_filter, 0, 1)
        filters.addWidget(QtWidgets.QLabel("Status"), 1, 0)
        filters.addWidget(self.status_filter, 1, 1)
        filters.addWidget(self.search_box, 2, 0, 1, 2)
        filters.addWidget(clear_btn, 3, 0, 1, 2)
        layout.addLayout(filters)

        self.issue_table = QtWidgets.QTableWidget(0, 6)
        self.issue_table.setHorizontalHeaderLabels(["Issue", "Title", "Score", "Sources", "Status", "Reason"])
        self.issue_table.verticalHeader().setVisible(False)
        self.issue_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.issue_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.issue_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.issue_table.setSortingEnabled(True)
        self.issue_table.horizontalHeader().setStretchLastSection(True)
        self.issue_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.issue_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        self.issue_table.itemSelectionChanged.connect(self.on_issue_selected)
        layout.addWidget(self.issue_table, 1)
        return panel

    def _build_right_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.summary_frame = QtWidgets.QFrame()
        self.summary_frame.setObjectName("summaryCard")
        summary_layout = QtWidgets.QGridLayout(self.summary_frame)
        self.issue_badge = QtWidgets.QLabel("No item selected")
        self.issue_badge.setObjectName("badge")
        self.summary_reason = QtWidgets.QLabel("")
        self.summary_reason.setWordWrap(True)
        self.summary_meta = QtWidgets.QLabel("")
        self.summary_meta.setWordWrap(True)
        self.recommended_label = QtWidgets.QLabel("")
        self.recommended_label.setObjectName("recommendation")
        self.recommended_label.setWordWrap(True)
        summary_layout.addWidget(self.issue_badge, 0, 0)
        summary_layout.addWidget(self.summary_reason, 1, 0)
        summary_layout.addWidget(self.summary_meta, 2, 0)
        summary_layout.addWidget(self.recommended_label, 3, 0)
        layout.addWidget(self.summary_frame)

        source_controls = QtWidgets.QHBoxLayout()
        source_controls.addWidget(QtWidgets.QLabel("Matched sources"))
        source_controls.addStretch(1)
        set_a_btn = QtWidgets.QPushButton("Set Selected as Source A")
        set_b_btn = QtWidgets.QPushButton("Set Selected as Source B")
        set_a_btn.clicked.connect(lambda: self.set_selected_source_marker("A"))
        set_b_btn.clicked.connect(lambda: self.set_selected_source_marker("B"))
        source_controls.addWidget(set_a_btn)
        source_controls.addWidget(set_b_btn)
        layout.addLayout(source_controls)

        self.sources_table = QtWidgets.QTableWidget(0, 6)
        self.sources_table.setHorizontalHeaderLabels(["Compare", "Source repo", "Title", "Path", "Chords", "Completeness"])
        self.sources_table.verticalHeader().setVisible(False)
        self.sources_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.sources_table.horizontalHeader().setStretchLastSection(True)
        self.sources_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        self.sources_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        self.sources_table.setMaximumHeight(150)
        layout.addWidget(self.sources_table)

        self.selection_reason = QtWidgets.QLabel("")
        self.selection_reason.setObjectName("selectionReason")
        self.selection_reason.setWordWrap(True)
        layout.addWidget(self.selection_reason)

        diff_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.source_a_title = QtWidgets.QLabel("Source A")
        self.source_b_title = QtWidgets.QLabel("Source B")
        self.source_a_text = self._text_view()
        self.source_b_text = self._text_view()
        diff_splitter.addWidget(self._labeled_text(self.source_a_title, self.source_a_text))
        diff_splitter.addWidget(self._labeled_text(self.source_b_title, self.source_b_text))
        diff_splitter.setSizes([500, 500])
        layout.addWidget(diff_splitter, 1)

        export_group = QtWidgets.QGroupBox("Secondary Export Preview")
        export_group.setCheckable(True)
        export_group.setChecked(False)
        export_layout = QtWidgets.QVBoxLayout(export_group)
        self.export_meta = QtWidgets.QLabel("")
        self.export_meta.setWordWrap(True)
        self.export_text = self._text_view()
        self.export_text.setMaximumHeight(150)
        export_layout.addWidget(self.export_meta)
        export_layout.addWidget(self.export_text)
        export_group.toggled.connect(self.export_text.setVisible)
        export_group.toggled.connect(self.export_meta.setVisible)
        self.export_text.setVisible(False)
        self.export_meta.setVisible(False)
        layout.addWidget(export_group)

        self.note_box = QtWidgets.QLineEdit()
        self.note_box.setPlaceholderText("Optional note")
        layout.addWidget(self.note_box)

        self.actions_layout = QtWidgets.QGridLayout()
        layout.addLayout(self.actions_layout)
        return panel

    def _text_view(self) -> QtWidgets.QTextEdit:
        widget = QtWidgets.QTextEdit()
        widget.setReadOnly(True)
        widget.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        widget.setFont(QtGui.QFont("Consolas", 10))
        return widget

    def _labeled_text(self, label: QtWidgets.QLabel, text: QtWidgets.QTextEdit) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        label.setObjectName("paneTitle")
        layout.addWidget(label)
        layout.addWidget(text, 1)
        return widget

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f4f6f8; color: #111827; font-family: Segoe UI; }
            QTableWidget, QTextEdit, QLineEdit, QComboBox { background: #ffffff; border: 1px solid #d0d5dd; border-radius: 5px; }
            QHeaderView::section { background: #eef2f7; padding: 6px; border: 0; border-right: 1px solid #d0d5dd; font-weight: 600; }
            QPushButton { background: #ffffff; border: 1px solid #98a2b3; border-radius: 5px; padding: 7px 10px; }
            QPushButton:hover { background: #f9fafb; }
            QPushButton[recommended="true"] { background: #1d4ed8; color: white; border-color: #1d4ed8; font-weight: 700; }
            QPushButton[recommended="true"]:hover { background: #1e40af; }
            QLabel#muted { color: #667085; }
            QLabel#status { color: #475467; padding: 4px; }
            QLabel#badge { font-size: 15px; font-weight: 700; padding: 4px 8px; border-radius: 5px; background: #e0e7ff; }
            QLabel#recommendation { color: #1d4ed8; font-weight: 600; }
            QLabel#selectionReason { color: #344054; background: #fff7ed; border: 1px solid #fed7aa; border-radius: 5px; padding: 7px; }
            QLabel#paneTitle { font-weight: 700; color: #344054; }
            QFrame#summaryCard { background: #ffffff; border: 1px solid #d0d5dd; border-radius: 8px; }
            QGroupBox { font-weight: 700; border: 1px solid #d0d5dd; border-radius: 6px; margin-top: 8px; padding: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
            """
        )

    def refresh(self) -> None:
        self.status_label.setText("Loading review items...")
        QtWidgets.QApplication.processEvents()
        try:
            self.all_candidates = review.load_review_candidates(self.output_dir, include_text=False, include_details=False)
        except Exception as exc:
            self.all_candidates = []
            self.status_label.setText(f"Could not load review items: {exc}")
            return
        self.apply_filters()

    def clear_filters(self) -> None:
        self.issue_filter.setCurrentIndex(0)
        self.status_filter.setCurrentIndex(0)
        self.search_box.clear()
        self.apply_filters()

    def apply_filters(self) -> None:
        issue_type = self.issue_filter.currentData() or "all"
        status = self.status_filter.currentData() or "all"
        self.candidates = review.filter_candidates(self.all_candidates, issue_type, status, self.search_box.text())
        self.populate_issue_table()

    def populate_issue_table(self) -> None:
        self.issue_table.setSortingEnabled(False)
        self.issue_table.setRowCount(len(self.candidates))
        for row, candidate in enumerate(self.candidates):
            explanation = review.issue_explanation(candidate)
            source_count = len(candidate.members_list or review.parse_members(candidate.members))
            reason = candidate.classification_reason or candidate.canonical_reason
            values = [
                explanation.label,
                candidate.title,
                candidate.best_lyric_identity_score,
                str(source_count),
                candidate.status,
                reason,
            ]
            color = ISSUE_COLORS.get(candidate.classification, QtGui.QColor("#ffffff"))
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, candidate.group_id)
                item.setBackground(color)
                self.issue_table.setItem(row, column, item)
        self.issue_table.setSortingEnabled(True)
        if self.candidates:
            row_to_select = self._preferred_row_after_refresh if self._preferred_row_after_refresh is not None else 0
            self._preferred_row_after_refresh = None
            row_to_select = min(max(row_to_select, 0), len(self.candidates) - 1)
            self.issue_table.selectRow(row_to_select)
            QtCore.QTimer.singleShot(0, self.on_issue_selected)
            self.status_label.setText(f"Showing {len(self.candidates)} of {len(self.all_candidates)} review item(s).")
        else:
            self.clear_details()
            self.status_label.setText("No review items match the filters.")

    def selected_candidate(self) -> Optional[review.ReviewCandidate]:
        row = self.issue_table.currentRow()
        if row < 0:
            return None
        group_item = self.issue_table.item(row, 0)
        if not group_item:
            return None
        group_id = group_item.data(QtCore.Qt.UserRole)
        return next((candidate for candidate in self.candidates if candidate.group_id == group_id), None)

    def on_issue_selected(self) -> None:
        candidate = self.selected_candidate()
        if not candidate:
            return
        self.current_candidate = candidate
        self._request_id += 1
        request_id = self._request_id
        self.set_loading_state(candidate)
        worker = DetailsWorker(request_id, self.output_dir, candidate)
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self.on_details_loaded)
        worker.failed.connect(self.on_details_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(lambda: self._remove_worker_thread(thread))
        thread.finished.connect(lambda: self._remove_worker(worker))
        thread.finished.connect(thread.deleteLater)
        self._worker_threads.append(thread)
        self._workers.append(worker)
        thread.start()

    def _remove_worker_thread(self, thread: QtCore.QThread) -> None:
        if thread in self._worker_threads:
            self._worker_threads.remove(thread)

    def _remove_worker(self, worker: DetailsWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 - Qt override name
        self._request_id += 1
        for thread in list(self._worker_threads):
            thread.quit()
            thread.wait(3000)
        self._worker_threads.clear()
        self._workers.clear()
        super().closeEvent(event)

    def set_loading_state(self, candidate: review.ReviewCandidate) -> None:
        self.issue_badge.setText(f"Loading: {candidate.title}")
        self.summary_reason.setText("Reading matched sources and computing diff...")
        self.selection_reason.setText("")
        self.source_a_text.setPlainText("")
        self.source_b_text.setPlainText("")
        self.export_text.setPlainText("")
        self.status_label.setText(f"Loading {candidate.title}...")
        self._clear_actions()

    @QtCore.Slot(object)
    def on_details_loaded(self, details: CandidateDetails) -> None:
        if details.request_id != self._request_id:
            return
        self.current_candidate = details.candidate
        self.current_pair = details.pair
        self.current_diff = details.diff
        self.render_details(details.candidate, details.pair, details.diff)
        self.status_label.setText(f"Loaded {details.candidate.title}.")

    @QtCore.Slot(int, str)
    def on_details_failed(self, request_id: int, message: str) -> None:
        if request_id != self._request_id:
            return
        self.status_label.setText(f"Could not load details: {message}")

    def render_details(
        self,
        candidate: review.ReviewCandidate,
        pair: review.SelectedComparisonPair,
        diff: review.SongDiffResult,
    ) -> None:
        explanation = review.issue_explanation(candidate)
        self.issue_badge.setText(f"{explanation.label}  |  {candidate.classification}")
        self.summary_reason.setText(explanation.why)
        self.summary_meta.setText(
            f"Score: {candidate.best_lyric_identity_score or '-'} | "
            f"Sources: {len(candidate.members_list or review.parse_members(candidate.members))} | "
            f"Reason: {candidate.classification_reason or candidate.canonical_reason} | "
            f"Diff: {diff.summary}"
        )
        self.recommended_label.setText(f"Recommended next action: {self._recommended_action_label(candidate)}")
        self.selection_reason.setText(pair.selection_reason)
        self.populate_sources(candidate, pair)
        self.source_a_title.setText(self._source_label(pair.source_a))
        self.source_b_title.setText(self._source_label(pair.source_b))
        self.source_a_text.setHtml(self._diff_html(diff, "left"))
        self.source_b_text.setHtml(self._diff_html(diff, "right"))
        self.export_meta.setText(f"Canonical source: {candidate.source_path or '-'}\nExport path: {candidate.export_path}")
        self.export_text.setPlainText(candidate.export_text or "Exported ChordPro unavailable.")
        self.render_actions(candidate, pair)

    def _recommended_action_label(self, candidate: review.ReviewCandidate) -> str:
        for action in review.available_issue_actions(candidate):
            if action.recommended:
                return action.label
        return "Review manually"

    def _source_label(self, source: review.ComparisonSource) -> str:
        return f"Source {source.marker}: {source.source_repo} | {source.title}"

    def populate_sources(self, candidate: review.ReviewCandidate, pair: review.SelectedComparisonPair) -> None:
        members = candidate.members_list or review.parse_members(candidate.members)
        rows: List[review.ComparisonSource] = [
            review.ComparisonSource("", member.source_repo, member.title, member.source_path)
            for member in members
        ]
        for selected in (pair.source_a, pair.source_b):
            existing = next((row for row in rows if row.source_path.lower() == selected.source_path.lower()), None)
            if existing:
                existing.marker = selected.marker
                existing.text = selected.text
                existing.has_chords = selected.has_chords
                existing.chord_count = selected.chord_count
                existing.line_count = selected.line_count
                existing.completeness = selected.completeness
                existing.is_export = selected.is_export
            else:
                rows.append(selected)
        self.source_rows = rows
        self.sources_table.setRowCount(len(rows))
        for row_index, source in enumerate(rows):
            chords = "Yes" if source.has_chords else ("No" if source.marker else "")
            self._set_source_row(
                row_index,
                source.marker,
                source.source_repo,
                source.title,
                source.source_path,
                chords,
                source.completeness,
            )
        for row_index, source in enumerate(rows):
            if source.marker == "A":
                self.sources_table.selectRow(row_index)
                break

    def set_selected_source_marker(self, marker: str) -> None:
        row = self.sources_table.currentRow()
        if row < 0 or row >= len(self.source_rows) or not self.current_pair or not self.current_candidate:
            return
        selected = self.source_rows[row]
        selected.marker = marker
        selected = review.load_comparison_source_text(self.output_dir, selected)
        if marker == "A":
            selected.marker = "A"
            self.current_pair.source_a = selected
        else:
            selected.marker = "B"
            self.current_pair.source_b = selected
        self.current_pair.selection_reason = f"Manual override: {selected.title} was set as Source {marker}."
        self.current_pair.fallback_to_export = self.current_pair.source_a.is_export or self.current_pair.source_b.is_export
        self.current_diff = review.compute_song_diff(
            self.current_pair.source_a.text,
            self.current_pair.source_b.text,
            self.current_candidate.classification,
        )
        self.render_details(self.current_candidate, self.current_pair, self.current_diff)

    def _set_source_row(self, row: int, marker: str, repo: str, title: str, path: str, chords: str, complete: str) -> None:
        values = [marker, repo, title, path, chords, complete]
        for column, value in enumerate(values):
            item = QtWidgets.QTableWidgetItem(value)
            if marker == "A":
                item.setBackground(QtGui.QColor("#dbeafe"))
            elif marker == "B":
                item.setBackground(QtGui.QColor("#fef3c7"))
            self.sources_table.setItem(row, column, item)

    def _diff_html(self, diff: review.SongDiffResult, side: str) -> str:
        lines = []
        for diff_line in diff.lines:
            text = diff_line.left if side == "left" else diff_line.right
            if not text and diff_line.status in ("a_only", "b_only"):
                text = ""
            escaped = html.escape(text)
            color = DIFF_COLORS.get(diff_line.status, "#ffffff")
            lines.append(f'<div style="background:{color}; padding:1px 4px; white-space:pre;">{escaped}</div>')
        return "<html><body style='font-family:Consolas, monospace; font-size:10pt;'>" + "".join(lines) + "</body></html>"

    def render_actions(self, candidate: review.ReviewCandidate, pair: review.SelectedComparisonPair) -> None:
        self._clear_actions()
        for index, action in enumerate(review.available_issue_actions(candidate)):
            button = QtWidgets.QPushButton(action.label)
            button.setToolTip(action.description)
            button.setProperty("recommended", "true" if action.recommended else "false")
            button.style().unpolish(button)
            button.style().polish(button)
            button.clicked.connect(lambda _checked=False, item=action: self.apply_action(item))
            self.actions_layout.addWidget(button, index // 4, index % 4)
            self._action_buttons.append(button)

    def _clear_actions(self) -> None:
        for button in self._action_buttons:
            self.actions_layout.removeWidget(button)
            button.deleteLater()
        self._action_buttons = []

    def apply_action(self, action: review.IssueAction) -> None:
        candidate = self.current_candidate
        pair = self.current_pair
        if not candidate or not pair:
            return
        if action.action_id == "skip":
            self.select_next_row()
            return
        source = self._chosen_source_for_action(action, pair)
        user_note = self.note_box.text().strip()
        auto_note = review.build_decision_note(candidate, action.action_id, pair.source_a, pair.source_b, user_note="")
        for button in self._action_buttons:
            button.setEnabled(False)
        self._preferred_row_after_refresh = max(0, self.issue_table.currentRow())
        try:
            review.apply_review_decision(
                self.output_dir,
                candidate,
                chosen_classification=action.chosen_classification,
                action=action.action_id,
                note=user_note,
                cache_db_path=self.cache_db_path,
                action_label=action.label,
                source_a=pair.source_a,
                source_b=pair.source_b,
                chosen_source=source,
                decision_category=action.decision_category,
                auto_note=auto_note,
            )
        except Exception as exc:
            self.status_label.setText(f"Could not save decision: {exc}")
            for button in self._action_buttons:
                button.setEnabled(True)
            return
        self.note_box.clear()
        self.status_label.setText(f"Saved: {action.label} for {candidate.title}")
        self.refresh()

    def _chosen_source_for_action(
        self,
        action: review.IssueAction,
        pair: review.SelectedComparisonPair,
    ) -> Optional[review.ComparisonSource]:
        marker = action.requires_source_marker
        if marker == "A":
            return pair.source_a
        if marker == "B":
            return pair.source_b
        if action.action_id.endswith("_a") or "_source_a" in action.action_id:
            return pair.source_a
        if action.action_id.endswith("_b") or "_source_b" in action.action_id:
            return pair.source_b
        return None

    def select_next_row(self) -> None:
        row = self.issue_table.currentRow()
        if self.issue_table.rowCount() == 0:
            return
        next_row = row + 1 if row + 1 < self.issue_table.rowCount() else 0
        self.issue_table.selectRow(next_row)

    def clear_details(self) -> None:
        self.issue_badge.setText("No item selected")
        self.summary_reason.setText("")
        self.summary_meta.setText("")
        self.recommended_label.setText("")
        self.selection_reason.setText("")
        self.sources_table.setRowCount(0)
        self.source_a_text.clear()
        self.source_b_text.clear()
        self.export_text.clear()
        self._clear_actions()

    def _open_path(self, path: Path) -> None:
        path = Path(path)
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


def run_app(output_dir: Path, cache_db_path: Optional[Path] = None) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    window = ReviewResolverWindow(output_dir, cache_db_path=cache_db_path)
    window.show()
    return app.exec()


def open_review_resolver(_parent: object, output_dir: Path, cache_db_path: Optional[Path] = None) -> subprocess.Popen[str]:
    command = [sys.executable, str(Path(__file__).resolve()), "--out", str(output_dir)]
    if cache_db_path:
        command.extend(["--cache-db", str(cache_db_path)])
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    return subprocess.Popen(command, cwd=str(Path(__file__).resolve().parent), creationflags=creationflags)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open the PySide6 manual review resolver.")
    parser.add_argument("--out", required=True, type=Path, help="Builder output folder")
    parser.add_argument("--cache-db", type=Path, default=None, help="Optional song repo cache database")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    return run_app(args.out, cache_db_path=args.cache_db)


if __name__ == "__main__":
    raise SystemExit(main())
