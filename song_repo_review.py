#!/usr/bin/env python3
"""Post-processing helpers for manual song repository review decisions."""

from __future__ import annotations

import csv
import json
import re
import shutil
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import song_repo_builder as builder


REVIEW_CLASS_KEYS = {
    "lyric_match_title_different",
    "needs_review",
    "title_match_lyrics_different",
    "multiple_chorded_sources",
}
DECISIONS_FILE = Path("reports") / "manual_review_decisions.json"


@dataclass
class ReviewMember:
    source_repo: str
    title: str
    source_path: str


@dataclass
class IssueExplanation:
    label: str
    why: str
    what_to_check: str
    evidence: str
    suggested_action: str
    severity: str = "review"


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
    classification_reason: str = ""
    members: str = ""
    members_list: List[ReviewMember] = field(default_factory=list)
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
        return builder.safe_read_text(path)
    except (OSError, UnicodeError):
        return ""


def parse_members(text: str) -> List[ReviewMember]:
    members: List[ReviewMember] = []
    for chunk in [part.strip() for part in text.split(" || ") if part.strip()]:
        match = re.match(r"^(?P<source>.*?): (?P<title>.*?) \[(?P<path>.*)\]$", chunk)
        if not match:
            members.append(ReviewMember(source_repo="", title=chunk, source_path=""))
            continue
        members.append(
            ReviewMember(
                source_repo=match.group("source").strip(),
                title=match.group("title").strip(),
                source_path=match.group("path").strip(),
            )
        )
    return members


def _member_count(candidate: ReviewCandidate) -> int:
    if candidate.members_list:
        return len(candidate.members_list)
    return len(parse_members(candidate.members))


def _evidence(candidate: ReviewCandidate) -> str:
    parts = [
        f"Title: {candidate.title or '(missing title)'}",
        f"Sources: {_member_count(candidate)}",
    ]
    if candidate.best_lyric_identity_score:
        parts.append(f"Best lyric identity score: {candidate.best_lyric_identity_score}")
    reason = candidate.classification_reason or candidate.canonical_reason
    if reason:
        parts.append(f"Report reason: {reason}")
    return " | ".join(parts)


def issue_explanation(candidate: ReviewCandidate) -> IssueExplanation:
    class_key = candidate.classification
    if class_key == "lyric_match_title_different":
        return IssueExplanation(
            label="Lyrics match, titles differ",
            why="The lyrics match strongly, but the titles differ between sources.",
            what_to_check="Compare the titles and source names. Use the best title/source when they are the same song, or keep both titles if they are real separate versions.",
            evidence=_evidence(candidate),
            suggested_action="Use This Title, Use Selected Source, or Keep Both/All.",
            severity="title",
        )
    if class_key == "title_match_lyrics_different":
        return IssueExplanation(
            label="Same title, different lyrics",
            why="The same title appears with different lyrics, so the matching process could not safely choose one version.",
            what_to_check="Compare the lyric bodies side by side. Decide whether one source is wrong, one is partial, or the versions should remain separate.",
            evidence=_evidence(candidate),
            suggested_action="Use Selected Source for the version to keep, or Keep Both/All when the versions are valid separate entries.",
            severity="lyrics",
        )
    if class_key == "multiple_chorded_sources":
        return IssueExplanation(
            label="Multiple chorded sources",
            why="Multiple chorded sources matched this song, so the best chord arrangement needs a manual choice.",
            what_to_check="Compare chord placement, key, completeness, and source reliability. Choose the arrangement you want in the clean output or retain all valid versions.",
            evidence=_evidence(candidate),
            suggested_action="Use Selected Source for the preferred arrangement, or Keep Both/All if more than one arrangement should remain.",
            severity="chords",
        )
    return IssueExplanation(
        label="Needs review",
        why="Lyrics matched, but below clean threshold or without enough confidence for automatic cleanup.",
        what_to_check="Check whether the matched sources are the same song, a partial song, an alternate version, or a bad match.",
        evidence=_evidence(candidate),
        suggested_action="Use Selected Source, Mark Same Song, Keep Both/All, or Keep Unresolved.",
        severity="review",
    )


def available_actions(candidate: ReviewCandidate) -> List[Dict[str, str]]:
    common_skip = {
        "label": "Skip",
        "action": "skip",
        "chosen_classification": candidate.classification,
        "description": "Leave this item unchanged for now.",
    }
    keep_unresolved = {
        "label": "Keep Unresolved",
        "action": "keep_unresolved",
        "chosen_classification": "needs_review",
        "description": "Keep this in the needs-review bucket.",
    }
    use_selected = {
        "label": "Use Selected Source",
        "action": "use_selected_source",
        "chosen_classification": "clean_match",
        "description": "Record the selected source/member as the preferred version.",
    }
    keep_all = {
        "label": "Keep Both/All",
        "action": "retain_all",
        "chosen_classification": candidate.classification,
        "description": "Record that the visible versions should remain separate or all be retained.",
    }

    if candidate.classification == "lyric_match_title_different":
        return [
            {
                "label": "Use This Title",
                "action": "use_this_title",
                "chosen_classification": "clean_match",
                "description": "Use the candidate title as the preferred title for this song.",
            },
            use_selected,
            {
                "label": "Keep Both/All",
                "action": "keep_both",
                "chosen_classification": candidate.classification,
                "description": "Keep both titles as separate valid catalog entries.",
            },
            keep_unresolved,
            common_skip,
        ]
    if candidate.classification == "title_match_lyrics_different":
        return [use_selected, keep_all, keep_unresolved, common_skip]
    if candidate.classification == "multiple_chorded_sources":
        return [use_selected, keep_all, keep_unresolved, common_skip]
    return [
        use_selected,
        {
            "label": "Mark Same Song",
            "action": "mark_same_song",
            "chosen_classification": "clean_match",
            "description": "Record that the reviewed sources are the same song.",
        },
        keep_all,
        keep_unresolved,
        common_skip,
    ]


def filter_candidates(
    candidates: List[ReviewCandidate],
    issue_type: str = "",
    status: str = "",
    search: str = "",
) -> List[ReviewCandidate]:
    normalized_issue = issue_type.strip().lower()
    normalized_status = status.strip().lower()
    terms = [part for part in search.strip().lower().split() if part]

    def matches(candidate: ReviewCandidate) -> bool:
        if normalized_issue and normalized_issue != "all" and candidate.classification.lower() != normalized_issue:
            return False
        if normalized_status and normalized_status != "all" and candidate.status.lower() != normalized_status:
            return False
        haystack = " ".join(
            [
                candidate.group_id,
                candidate.classification,
                candidate.title,
                candidate.artist,
                candidate.author,
                candidate.source_repo,
                candidate.source_format,
                candidate.source_path,
                candidate.canonical_reason,
                candidate.classification_reason,
                candidate.members,
            ]
        ).lower()
        return all(term in haystack for term in terms)

    return [candidate for candidate in candidates if matches(candidate)]


def resolve_report_path(out_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if not raw_path:
        return path
    if path.is_absolute():
        return path
    if path.exists():
        return path
    if path.parts and path.parts[0].lower() == out_dir.name.lower():
        parent_candidate = out_dir.parent / path
        if parent_candidate.exists():
            return parent_candidate
        return parent_candidate
    out_candidate = out_dir / path
    if out_candidate.exists():
        return out_candidate
    return out_candidate


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


def read_detail_rows_for_title(path: Path, title: str) -> List[Dict[str, str]]:
    if not title:
        return []
    rows: List[Dict[str, str]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if (
                    row.get("a_title") == title
                    or row.get("b_title") == title
                    or row.get("title") == title
                    or row.get("canonical_title") == title
                ):
                    rows.append(row)
    except OSError:
        return []
    return rows


def load_candidate_details(out_dir: Path, candidate: ReviewCandidate) -> ReviewCandidate:
    out_dir = Path(out_dir)
    reports_dir = out_dir / "reports"
    source_path = resolve_report_path(out_dir, candidate.source_path)
    candidate.export_text = read_text(candidate.export_path)
    candidate.source_text = read_text(source_path) if source_path.exists() else ""
    candidate.pair_details = read_detail_rows_for_title(reports_dir / "07_group_pair_scores.csv", candidate.title)
    candidate.conflict_details = read_detail_rows_for_title(
        reports_dir / "08_same_title_different_lyrics.csv",
        candidate.title,
    )
    return candidate


def load_review_candidates(
    out_dir: Path,
    include_text: bool = True,
    include_details: bool = True,
) -> List[ReviewCandidate]:
    out_dir = Path(out_dir)
    reports_dir = out_dir / "reports"
    canonical_rows = read_csv_rows(reports_dir / "03_canonical_selection.csv")
    group_rows = {row.get("group_id", ""): row for row in read_csv_rows(reports_dir / "02_match_groups.csv")}
    pair_rows = read_csv_rows(reports_dir / "07_group_pair_scores.csv") if include_details else []
    conflict_rows = read_csv_rows(reports_dir / "08_same_title_different_lyrics.csv") if include_details else []
    decisions = load_decision_map(out_dir)
    candidates: List[ReviewCandidate] = []

    for row in canonical_rows:
        class_key = row.get("classification", "")
        if class_key not in REVIEW_CLASS_KEYS:
            continue
        export_path = resolve_report_path(out_dir, row.get("export_path", ""))
        source_path = resolve_report_path(out_dir, row.get("source_path", ""))
        group = group_rows.get(row.get("group_id", ""), {})
        title = row.get("title", "")
        decision = decisions.get(row.get("group_id", ""))
        status = "resolved" if decision else "unresolved"
        members = group.get("members", "")
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
                classification_reason=group.get("classification_reason", ""),
                members=members,
                members_list=parse_members(members),
                best_lyric_identity_score=group.get("best_lyric_identity_score", ""),
                pair_details=_details_for_title(pair_rows, title) if include_details else [],
                conflict_details=_details_for_title(conflict_rows, title) if include_details else [],
                export_text=read_text(export_path) if include_text else "",
                source_text=read_text(source_path) if include_text and source_path.exists() else "",
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
                    classification_reason="Found in review folder without report row",
                    export_text=read_text(export_path) if include_text else "",
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
