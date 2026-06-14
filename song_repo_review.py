#!/usr/bin/env python3
"""Post-processing helpers for manual song repository review decisions."""

from __future__ import annotations

import csv
import json
import shutil
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import song_repo_builder as builder


REVIEW_CLASS_KEYS = {
    "needs_review",
    "title_match_lyrics_different",
    "multiple_chorded_sources",
}
DECISIONS_FILE = Path("reports") / "manual_review_decisions.json"


@dataclass
class ReviewCandidate:
    group_id: str
    classification: str
    export_path: Path
    title: str
    artist: str
    author: str
    source_repo: str
    source_format: str
    source_path: str
    file_hash: str
    canonical_reason: str
    members: str = ""
    best_lyric_identity_score: str = ""
    pair_details: List[Dict[str, str]] = field(default_factory=list)
    conflict_details: List[Dict[str, str]] = field(default_factory=list)
    export_text: str = ""
    source_text: str = ""
    status: str = "unresolved"


@dataclass
class ManualReviewDecision:
    decision_id: str
    group_id: str
    original_classification: str
    chosen_classification: str
    original_export_path: Path
    final_export_path: Path
    action: str
    note: str
    file_hash: str
    canonical_source_path: str
    created_at: str
    updated_at: str


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def load_decision_map(out_dir: Path) -> Dict[str, Dict[str, Any]]:
    path = out_dir / DECISIONS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    decisions = data.get("decisions", [])
    return {str(item.get("group_id", "")): item for item in decisions if item.get("group_id")}


def _details_for_title(rows: List[Dict[str, str]], title: str) -> List[Dict[str, str]]:
    if not title:
        return []
    return [
        row
        for row in rows
        if row.get("a_title") == title
        or row.get("b_title") == title
        or row.get("title") == title
        or row.get("canonical_title") == title
    ]


def load_review_candidates(out_dir: Path) -> List[ReviewCandidate]:
    out_dir = Path(out_dir)
    reports_dir = out_dir / "reports"
    canonical_rows = read_csv_rows(reports_dir / "03_canonical_selection.csv")
    group_rows = {row.get("group_id", ""): row for row in read_csv_rows(reports_dir / "02_match_groups.csv")}
    pair_rows = read_csv_rows(reports_dir / "07_group_pair_scores.csv")
    conflict_rows = read_csv_rows(reports_dir / "08_same_title_different_lyrics.csv")
    decisions = load_decision_map(out_dir)
    candidates: List[ReviewCandidate] = []

    for row in canonical_rows:
        class_key = row.get("classification", "")
        if class_key not in REVIEW_CLASS_KEYS:
            continue
        export_path = Path(row.get("export_path", ""))
        if not export_path.is_absolute():
            export_path = out_dir / export_path
        source_path = Path(row.get("source_path", ""))
        group = group_rows.get(row.get("group_id", ""), {})
        title = row.get("title", "")
        decision = decisions.get(row.get("group_id", ""))
        status = "resolved" if decision else "unresolved"
        candidates.append(
            ReviewCandidate(
                group_id=row.get("group_id", ""),
                classification=class_key,
                export_path=export_path,
                title=title,
                artist=row.get("artist", ""),
                author=row.get("author", ""),
                source_repo=row.get("source_repo", ""),
                source_format=row.get("source_format", ""),
                source_path=row.get("source_path", ""),
                file_hash=row.get("file_hash", ""),
                canonical_reason=row.get("canonical_reason", ""),
                members=group.get("members", ""),
                best_lyric_identity_score=group.get("best_lyric_identity_score", ""),
                pair_details=_details_for_title(pair_rows, title),
                conflict_details=_details_for_title(conflict_rows, title),
                export_text=read_text(export_path),
                source_text=read_text(source_path) if source_path.exists() else "",
                status=status,
            )
        )

    known_paths = {candidate.export_path.resolve() for candidate in candidates if candidate.export_path.exists()}
    for class_key in REVIEW_CLASS_KEYS:
        folder = out_dir / builder.CLASS_FOLDERS[class_key]
        for export_path in folder.glob("*.chopro"):
            if export_path.resolve() in known_paths:
                continue
            candidates.append(
                ReviewCandidate(
                    group_id=export_path.stem,
                    classification=class_key,
                    export_path=export_path,
                    title=export_path.stem,
                    artist="",
                    author="",
                    source_repo="",
                    source_format="",
                    source_path="",
                    file_hash="",
                    canonical_reason="Found in review folder without report row",
                    export_text=read_text(export_path),
                    source_text="",
                )
            )
    return candidates


def decision_to_json(decision: ManualReviewDecision) -> Dict[str, Any]:
    data = asdict(decision)
    data["original_export_path"] = str(decision.original_export_path)
    data["final_export_path"] = str(decision.final_export_path)
    return data


def save_manual_decision(out_dir: Path, decision: ManualReviewDecision) -> None:
    path = Path(out_dir) / DECISIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"decisions": []}
    decisions = [item for item in data.get("decisions", []) if item.get("group_id") != decision.group_id]
    decisions.append(decision_to_json(decision))
    data["decisions"] = decisions
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def update_manual_decision_db(cache_db_path: Optional[Path], decision: ManualReviewDecision) -> bool:
    if not cache_db_path:
        return False
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(cache_db_path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_review_decisions (
                group_id TEXT PRIMARY KEY,
                decision_id TEXT NOT NULL,
                original_classification TEXT NOT NULL,
                chosen_classification TEXT NOT NULL,
                original_export_path TEXT NOT NULL,
                final_export_path TEXT NOT NULL,
                action TEXT NOT NULL,
                note TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                canonical_source_path TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO manual_review_decisions (
                group_id, decision_id, original_classification, chosen_classification,
                original_export_path, final_export_path, action, note, file_hash,
                canonical_source_path, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                decision_id = excluded.decision_id,
                original_classification = excluded.original_classification,
                chosen_classification = excluded.chosen_classification,
                original_export_path = excluded.original_export_path,
                final_export_path = excluded.final_export_path,
                action = excluded.action,
                note = excluded.note,
                file_hash = excluded.file_hash,
                canonical_source_path = excluded.canonical_source_path,
                updated_at = excluded.updated_at
            """,
            (
                decision.group_id,
                decision.decision_id,
                decision.original_classification,
                decision.chosen_classification,
                str(decision.original_export_path),
                str(decision.final_export_path),
                decision.action,
                decision.note,
                decision.file_hash,
                decision.canonical_source_path,
                decision.created_at,
                decision.updated_at,
            ),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        return False
    finally:
        if conn is not None:
            conn.close()


def apply_review_decision(
    out_dir: Path,
    candidate: ReviewCandidate,
    chosen_classification: str,
    action: str,
    note: str = "",
    cache_db_path: Optional[Path] = None,
) -> ManualReviewDecision:
    out_dir = Path(out_dir)
    if chosen_classification not in builder.CLASS_FOLDERS:
        raise ValueError(f"Unknown classification: {chosen_classification}")

    original = Path(candidate.export_path)
    final_path = original
    if action != "skip" and chosen_classification != candidate.classification:
        target_dir = out_dir / builder.CLASS_FOLDERS[chosen_classification]
        target_dir.mkdir(parents=True, exist_ok=True)
        final_path = builder.unique_path(target_dir, original.stem, original.suffix)
        shutil.move(str(original), str(final_path))

    timestamp = now_iso()
    decision = ManualReviewDecision(
        decision_id=f"{candidate.group_id}-{int(time.time())}",
        group_id=candidate.group_id,
        original_classification=candidate.classification,
        chosen_classification=chosen_classification,
        original_export_path=original,
        final_export_path=final_path,
        action=action,
        note=note,
        file_hash=candidate.file_hash,
        canonical_source_path=candidate.source_path,
        created_at=timestamp,
        updated_at=timestamp,
    )
    save_manual_decision(out_dir, decision)
    update_manual_decision_db(cache_db_path, decision)
    return decision
