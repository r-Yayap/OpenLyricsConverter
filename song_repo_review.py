#!/usr/bin/env python3
"""Post-processing helpers for manual song repository review decisions."""

from __future__ import annotations

import csv
import difflib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import traceback
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
REVIEW_RESOLVER_CACHE_FILE = Path("reports") / "review_resolver_cache.sqlite"
REVIEW_RESOLVER_DEBUG_FILE = Path("reports") / "review_resolver_debug.log"
SNAPSHOT_VERSION = "review_snapshot.2026.06.14.2"
DIFF_VERSION = "song_diff.2026.06.14.2"
DEFAULT_MAX_FULL_DIFF_LINES = 1200
DEFAULT_MAX_RENDER_DIFF_LINES = 900
DEFAULT_MAX_DIFF_CELLS = 350_000
META_LINE_RE = re.compile(
    r"^\s*(?:\{[^}]+:\s*.*\}|(?:Title|Artist|Author|Key|Original Key|Book|Notes|Scripture):)",
    re.I,
)
CHORD_TOKEN_RE = re.compile(r"\[[A-G](?:#|b)?[A-Za-z0-9/#().+-]*\]")


@dataclass
class ReviewMember:
    source_repo: str
    title: str
    source_path: str


@dataclass
class ComparisonSource:
    marker: str
    source_repo: str
    title: str
    source_path: str
    text: str = ""
    file_hash: str = ""
    snapshot_key: str = ""
    snapshot_cache_hit: bool = False
    snapshot_source: str = ""
    warning: str = ""
    has_chords: bool = False
    chord_count: int = 0
    line_count: int = 0
    completeness: str = ""
    is_export: bool = False


@dataclass
class SelectedComparisonPair:
    source_a: ComparisonSource
    source_b: ComparisonSource
    selection_reason: str
    evidence_row: Dict[str, str] = field(default_factory=dict)
    fallback_to_export: bool = False


@dataclass
class DiffLine:
    left: str
    right: str
    status: str
    note: str = ""


@dataclass
class SongDiffResult:
    lines: List[DiffLine]
    lyric_similarity: float
    chord_difference_count: int
    metadata_difference_count: int
    summary: str
    likely_partial_side: str = ""
    likely_separate_song: bool = False
    simplified: bool = False
    truncated: bool = False


@dataclass
class SourceSnapshot:
    source: ComparisonSource
    text: str
    file_hash: str
    snapshot_key: str
    cache_hit: bool
    snapshot_source: str
    warning: str = ""
    has_chords: bool = False
    chord_count: int = 0
    line_count: int = 0


@dataclass
class CachedDiffResult:
    result: SongDiffResult
    cache_hit: bool
    diff_key: str


@dataclass
class IssueAction:
    action_id: str
    label: str
    chosen_classification: str
    decision_category: str
    description: str
    recommended: bool = False
    requires_source_marker: str = ""


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
    action_label: str = ""
    source_a_repo: str = ""
    source_a_title: str = ""
    source_a_path: str = ""
    source_b_repo: str = ""
    source_b_title: str = ""
    source_b_path: str = ""
    chosen_source_marker: str = ""
    chosen_source_repo: str = ""
    chosen_source_title: str = ""
    chosen_source_path: str = ""
    decision_category: str = ""
    auto_note: str = ""
    user_note: str = ""


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


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _now_perf() -> float:
    return time.perf_counter()


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


class ReviewDebugLogger:
    def __init__(self, out_dir: Path, echo: Optional[bool] = None) -> None:
        self.out_dir = Path(out_dir)
        self.path = self.out_dir / REVIEW_RESOLVER_DEBUG_FILE
        self.echo = bool(os.environ.get("SONG_REPO_REVIEW_DEBUG")) if echo is None else echo
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, stage: str, **fields: Any) -> None:
        payload = {
            "ts": now_iso(),
            "stage": stage,
            **{key: str(value) for key, value in fields.items()},
        }
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        try:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass
        if self.echo:
            print(line, flush=True)

    def exception(self, stage: str, exc: BaseException, **fields: Any) -> None:
        self.log(
            stage,
            error_type=type(exc).__name__,
            error=str(exc),
            traceback=traceback.format_exc(),
            **fields,
        )


def diff_result_to_json(result: SongDiffResult) -> Dict[str, Any]:
    return {
        "lines": [asdict(line) for line in result.lines],
        "lyric_similarity": result.lyric_similarity,
        "chord_difference_count": result.chord_difference_count,
        "metadata_difference_count": result.metadata_difference_count,
        "summary": result.summary,
        "likely_partial_side": result.likely_partial_side,
        "likely_separate_song": result.likely_separate_song,
        "simplified": result.simplified,
        "truncated": result.truncated,
    }


def diff_result_from_json(data: Dict[str, Any]) -> SongDiffResult:
    return SongDiffResult(
        lines=[
            DiffLine(
                left=str(line.get("left", "")),
                right=str(line.get("right", "")),
                status=str(line.get("status", "")),
                note=str(line.get("note", "")),
            )
            for line in data.get("lines", [])
            if isinstance(line, dict)
        ],
        lyric_similarity=float(data.get("lyric_similarity", 0.0) or 0.0),
        chord_difference_count=int(data.get("chord_difference_count", 0) or 0),
        metadata_difference_count=int(data.get("metadata_difference_count", 0) or 0),
        summary=str(data.get("summary", "")),
        likely_partial_side=str(data.get("likely_partial_side", "")),
        likely_separate_song=bool(data.get("likely_separate_song", False)),
        simplified=bool(data.get("simplified", False)),
        truncated=bool(data.get("truncated", False)),
    )


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


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def detect_song_features(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    chord_count = sum(len(CHORD_TOKEN_RE.findall(line)) for line in lines)
    lyric_lines = [
        line
        for line in lines
        if line.strip()
        and not META_LINE_RE.search(line)
        and _normalize_lyric_line(line)
    ]
    return {
        "has_chords": chord_count > 0,
        "chord_count": chord_count,
        "line_count": len(lines),
        "lyric_line_count": len(lyric_lines),
        "completeness": "likely partial" if 0 < len(lyric_lines) <= 4 else "complete-looking",
    }


def _source_from_row(row: Dict[str, str], side: str) -> ComparisonSource:
    prefix = "a" if side == "A" else "b"
    return ComparisonSource(
        marker=side,
        source_repo=row.get(f"{prefix}_source", ""),
        title=row.get(f"{prefix}_title", ""),
        source_path=row.get(f"{prefix}_path", ""),
    )


def _source_from_member(member: ReviewMember, marker: str) -> ComparisonSource:
    return ComparisonSource(
        marker=marker,
        source_repo=member.source_repo,
        title=member.title,
        source_path=member.source_path,
    )


def _export_source(candidate: ReviewCandidate, marker: str = "B") -> ComparisonSource:
    return ComparisonSource(
        marker=marker,
        source_repo="Exported ChordPro",
        title=candidate.title,
        source_path=str(candidate.export_path),
        text=candidate.export_text,
        is_export=True,
    )


def _titles_differ(row: Dict[str, str]) -> bool:
    a_title = re.sub(r"\s+", " ", row.get("a_title", "").strip().lower())
    b_title = re.sub(r"\s+", " ", row.get("b_title", "").strip().lower())
    return bool(a_title and b_title and a_title != b_title)


def _row_score(row: Dict[str, str], key: str = "lyric_identity_score") -> float:
    return _float_value(row.get(key), 0.0)


def _pair_from_row(row: Dict[str, str], reason: str) -> SelectedComparisonPair:
    return SelectedComparisonPair(
        source_a=_source_from_row(row, "A"),
        source_b=_source_from_row(row, "B"),
        selection_reason=reason,
        evidence_row=dict(row),
    )


def _member_fallback_pair(candidate: ReviewCandidate) -> SelectedComparisonPair:
    members = candidate.members_list or parse_members(candidate.members)
    if len(members) >= 2:
        return SelectedComparisonPair(
            source_a=_source_from_member(members[0], "A"),
            source_b=_source_from_member(members[1], "B"),
            selection_reason="Auto-selected the first two matched sources because no stronger pair-score row was available.",
            fallback_to_export=False,
        )
    if len(members) == 1:
        return SelectedComparisonPair(
            source_a=_source_from_member(members[0], "A"),
            source_b=_export_source(candidate, "B"),
            selection_reason="Only one matched source was reported, so the resolver is comparing it against the exported ChordPro as a fallback.",
            fallback_to_export=True,
        )
    return SelectedComparisonPair(
        source_a=ComparisonSource("A", candidate.source_repo, candidate.title, candidate.source_path),
        source_b=_export_source(candidate, "B"),
        selection_reason="No matched source list was reported, so the resolver is comparing the canonical source against the export as a fallback.",
        fallback_to_export=True,
    )


def select_default_comparison_pair(candidate: ReviewCandidate) -> SelectedComparisonPair:
    if candidate.conflict_details:
        row = min(candidate.conflict_details, key=lambda item: _row_score(item))
        return _pair_from_row(
            row,
            "Auto-selected because these sources have the same title but different lyric bodies.",
        )

    rows = list(candidate.pair_details)
    if candidate.classification == "lyric_match_title_different":
        title_diff_rows = [row for row in rows if _titles_differ(row)]
        if title_diff_rows:
            row = max(title_diff_rows, key=lambda item: _row_score(item))
            return _pair_from_row(
                row,
                "Auto-selected because the lyrics strongly match but the titles differ.",
            )

    if candidate.classification == "title_match_lyrics_different" and rows:
        title_match_rows = [row for row in rows if _float_value(row.get("title_score"), 0.0) >= 0.85]
        row = min(title_match_rows or rows, key=lambda item: _row_score(item))
        return _pair_from_row(
            row,
            "Auto-selected because these sources have the same or similar title and conflicting lyrics.",
        )

    if candidate.classification == "multiple_chorded_sources" and rows:
        def chorded_priority(row: Dict[str, str]) -> tuple[int, float]:
            chorded_count = sum(1 for key in ("a_source", "b_source") if "openlyrics" not in row.get(key, "").lower())
            return chorded_count, _row_score(row)

        row = max(rows, key=chorded_priority)
        return _pair_from_row(
            row,
            "Auto-selected because both sources are likely chorded arrangements that need comparison.",
        )

    if rows:
        row = max(rows, key=lambda item: _float_value(item.get("final_score"), _row_score(item)))
        return _pair_from_row(
            row,
            "Auto-selected the strongest reported pair for this uncertain match.",
        )

    return _member_fallback_pair(candidate)


def load_member_text(out_dir: Path, member: ReviewMember) -> str:
    path = resolve_report_path(Path(out_dir), member.source_path)
    return read_text(path) if path.exists() else ""


def load_comparison_source_text(out_dir: Path, source: ComparisonSource) -> ComparisonSource:
    if source.text:
        features = detect_song_features(source.text)
    else:
        path = resolve_report_path(Path(out_dir), source.source_path)
        source.text = read_text(path) if path.exists() else ""
        features = detect_song_features(source.text)
    source.has_chords = bool(features["has_chords"])
    source.chord_count = int(features["chord_count"])
    source.line_count = int(features["line_count"])
    source.completeness = str(features["completeness"])
    return source


def load_comparison_pair_texts(out_dir: Path, pair: SelectedComparisonPair) -> SelectedComparisonPair:
    load_comparison_source_text(out_dir, pair.source_a)
    load_comparison_source_text(out_dir, pair.source_b)
    return pair


def _normalize_lyric_line(line: str) -> str:
    if META_LINE_RE.search(line):
        return ""
    line = CHORD_TOKEN_RE.sub("", line)
    line = re.sub(r"[^a-z0-9\s']", " ", line.lower())
    return re.sub(r"\s+", " ", line).strip()


def _line_kind(line: str) -> str:
    if META_LINE_RE.search(line):
        return "metadata"
    if CHORD_TOKEN_RE.search(line):
        return "chord"
    return "lyric"


def _chords(line: str) -> List[str]:
    return CHORD_TOKEN_RE.findall(line)


def _line_status(left: str, right: str, left_norm: str, right_norm: str) -> str:
    if left == right:
        return "equal"
    if _line_kind(left) == "metadata" or _line_kind(right) == "metadata":
        return "metadata"
    if _chords(left) != _chords(right) and _normalize_lyric_line(left) == _normalize_lyric_line(right):
        return "chord_change"
    if left_norm == right_norm:
        return "equal"
    return "changed"


def _render_limited_lines(lines: List[DiffLine], max_render_lines: int) -> tuple[List[DiffLine], bool]:
    if max_render_lines <= 0 or len(lines) <= max_render_lines:
        return lines, False
    limited = lines[:max_render_lines]
    limited.append(DiffLine("... diff truncated ...", "... diff truncated ...", "changed", "large diff truncated"))
    return limited, True


def _compute_simplified_song_diff(
    lines_a: List[str],
    lines_b: List[str],
    classification: str,
    max_render_lines: int,
) -> SongDiffResult:
    diff_lines: List[DiffLine] = []
    max_len = max(len(lines_a), len(lines_b))
    for index in range(max_len):
        if max_render_lines > 0 and len(diff_lines) >= max_render_lines:
            break
        left = lines_a[index] if index < len(lines_a) else ""
        right = lines_b[index] if index < len(lines_b) else ""
        if left and right:
            diff_lines.append(DiffLine(left, right, _line_status(left, right, _normalize_lyric_line(left), _normalize_lyric_line(right))))
        elif left:
            diff_lines.append(DiffLine(left, "", "a_only"))
        elif right:
            diff_lines.append(DiffLine("", right, "b_only"))
    truncated = max_len > len(diff_lines)
    if truncated:
        diff_lines.append(DiffLine("... large diff truncated ...", "... large diff truncated ...", "changed"))

    lyric_a = [_normalize_lyric_line(line) for line in lines_a if _normalize_lyric_line(line)]
    lyric_b = [_normalize_lyric_line(line) for line in lines_b if _normalize_lyric_line(line)]
    lyric_similarity = difflib.SequenceMatcher(
        a="\n".join(lyric_a[:200]),
        b="\n".join(lyric_b[:200]),
        autojunk=False,
    ).ratio()
    chord_difference_count = sum(1 for line in diff_lines if line.status == "chord_change")
    metadata_difference_count = sum(1 for line in diff_lines if line.status == "metadata" and line.left != line.right)
    likely_partial_side = ""
    if lyric_a and lyric_b:
        if len(lyric_a) <= max(2, int(len(lyric_b) * 0.55)):
            likely_partial_side = "A"
        elif len(lyric_b) <= max(2, int(len(lyric_a) * 0.55)):
            likely_partial_side = "B"
    likely_separate_song = classification == "title_match_lyrics_different" and lyric_similarity < 0.35
    summary = (
        f"Simplified diff used for large input; lyric similarity {lyric_similarity:.2f}; "
        f"{chord_difference_count} chord-only difference(s); {metadata_difference_count} metadata difference(s)"
    )
    if truncated:
        summary += "; rendered diff truncated"
    return SongDiffResult(
        lines=diff_lines,
        lyric_similarity=lyric_similarity,
        chord_difference_count=chord_difference_count,
        metadata_difference_count=metadata_difference_count,
        summary=summary,
        likely_partial_side=likely_partial_side,
        likely_separate_song=likely_separate_song,
        simplified=True,
        truncated=truncated,
    )


def compute_song_diff(
    text_a: str,
    text_b: str,
    classification: str = "",
    max_full_lines: int = DEFAULT_MAX_FULL_DIFF_LINES,
    max_render_lines: int = DEFAULT_MAX_RENDER_DIFF_LINES,
    max_cells: int = DEFAULT_MAX_DIFF_CELLS,
) -> SongDiffResult:
    lines_a = text_a.splitlines()
    lines_b = text_b.splitlines()
    if (
        len(lines_a) + len(lines_b) > max_full_lines
        or len(lines_a) * max(1, len(lines_b)) > max_cells
    ):
        return _compute_simplified_song_diff(lines_a, lines_b, classification, max_render_lines)

    norm_a = [_normalize_lyric_line(line) or line.strip().lower() for line in lines_a]
    norm_b = [_normalize_lyric_line(line) or line.strip().lower() for line in lines_b]
    matcher = difflib.SequenceMatcher(a=norm_a, b=norm_b, autojunk=False)
    diff_lines: List[DiffLine] = []

    for tag, a_start, a_end, b_start, b_end in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(a_end - a_start):
                left = lines_a[a_start + offset]
                right = lines_b[b_start + offset]
                status = _line_status(left, right, _normalize_lyric_line(left), _normalize_lyric_line(right))
                diff_lines.append(DiffLine(left, right, status))
        elif tag == "delete":
            for index in range(a_start, a_end):
                diff_lines.append(DiffLine(lines_a[index], "", "a_only"))
        elif tag == "insert":
            for index in range(b_start, b_end):
                diff_lines.append(DiffLine("", lines_b[index], "b_only"))
        else:
            max_len = max(a_end - a_start, b_end - b_start)
            for offset in range(max_len):
                left = lines_a[a_start + offset] if a_start + offset < a_end else ""
                right = lines_b[b_start + offset] if b_start + offset < b_end else ""
                if left and right:
                    left_norm = _normalize_lyric_line(left)
                    right_norm = _normalize_lyric_line(right)
                    status = _line_status(left, right, left_norm, right_norm)
                    if status == "changed" and classification != "lyric_match_title_different":
                        diff_lines.append(DiffLine(left, "", "a_only"))
                        diff_lines.append(DiffLine("", right, "b_only"))
                    else:
                        diff_lines.append(DiffLine(left, right, status))
                elif left:
                    diff_lines.append(DiffLine(left, "", "a_only"))
                elif right:
                    diff_lines.append(DiffLine("", right, "b_only"))

    lyric_a = [_normalize_lyric_line(line) for line in lines_a if _normalize_lyric_line(line)]
    lyric_b = [_normalize_lyric_line(line) for line in lines_b if _normalize_lyric_line(line)]
    lyric_similarity = difflib.SequenceMatcher(
        a="\n".join(lyric_a),
        b="\n".join(lyric_b),
        autojunk=False,
    ).ratio()
    chord_difference_count = sum(1 for line in diff_lines if line.status == "chord_change")
    metadata_difference_count = sum(1 for line in diff_lines if line.status == "metadata" and line.left != line.right)
    likely_partial_side = ""
    if lyric_a and lyric_b:
        if len(lyric_a) <= max(2, int(len(lyric_b) * 0.55)):
            likely_partial_side = "A"
        elif len(lyric_b) <= max(2, int(len(lyric_a) * 0.55)):
            likely_partial_side = "B"
    likely_separate_song = classification == "title_match_lyrics_different" and lyric_similarity < 0.35
    summary_parts = [
        f"Lyric similarity {lyric_similarity:.2f}",
        f"{chord_difference_count} chord-only difference(s)",
        f"{metadata_difference_count} metadata difference(s)",
    ]
    if likely_partial_side:
        summary_parts.append(f"Source {likely_partial_side} may be partial")
    if likely_separate_song:
        summary_parts.append("sources may be separate songs")
    diff_lines, truncated = _render_limited_lines(diff_lines, max_render_lines)
    if truncated:
        summary_parts.append("rendered diff truncated")
    return SongDiffResult(
        lines=diff_lines,
        lyric_similarity=lyric_similarity,
        chord_difference_count=chord_difference_count,
        metadata_difference_count=metadata_difference_count,
        summary="; ".join(summary_parts),
        likely_partial_side=likely_partial_side,
        likely_separate_song=likely_separate_song,
        simplified=False,
        truncated=truncated,
    )


def available_issue_actions(candidate: ReviewCandidate) -> List[IssueAction]:
    class_key = candidate.classification
    if class_key == "lyric_match_title_different":
        return [
            IssueAction("use_source_a_title", "Use Title from Source A", "clean_match", "title", "Use Source A's title as canonical.", True, "A"),
            IssueAction("use_source_b_title", "Use Title from Source B", "clean_match", "title", "Use Source B's title as canonical.", False, "B"),
            IssueAction("use_canonical_title", "Use Export/Canonical Title", "clean_match", "title", "Keep the exported canonical title."),
            IssueAction("add_title_alias", "Add Other Title as Alias", class_key, "alias", "Record the alternate title as an alias decision."),
            IssueAction("keep_both_separate_songs", "Keep Both as Separate Songs", class_key, "keep-both", "Keep both titles as separate catalog entries."),
            IssueAction("keep_unresolved", "Keep Unresolved", "needs_review", "unresolved", "Leave this for later review."),
            IssueAction("skip", "Skip", class_key, "skip", "Move to the next item."),
        ]
    if class_key == "title_match_lyrics_different":
        return [
            IssueAction("use_source_a_lyrics", "Use Source A Lyrics", "clean_match", "lyrics", "Use Source A as the preferred lyric body.", True, "A"),
            IssueAction("use_source_b_lyrics", "Use Source B Lyrics", "clean_match", "lyrics", "Use Source B as the preferred lyric body.", False, "B"),
            IssueAction("merge_missing_verses", "Merge Missing Verses", class_key, "merge", "Record that missing sections should be merged."),
            IssueAction("keep_both_versions", "Keep Both as Separate Versions", class_key, "keep-both", "Keep both lyric versions."),
            IssueAction("mark_source_a_partial_wrong", "Mark Source A Partial/Wrong", class_key, "partial/wrong", "Record Source A as partial or wrong.", False, "A"),
            IssueAction("mark_source_b_partial_wrong", "Mark Source B Partial/Wrong", class_key, "partial/wrong", "Record Source B as partial or wrong.", False, "B"),
            IssueAction("split_not_same_song", "Split Match Group / Not Same Song", "needs_review", "split", "Record that these should not be treated as the same song."),
            IssueAction("keep_unresolved", "Keep Unresolved", "needs_review", "unresolved", "Leave this for later review."),
            IssueAction("skip", "Skip", class_key, "skip", "Move to the next item."),
        ]
    if class_key == "multiple_chorded_sources":
        return [
            IssueAction("use_source_a_chords", "Use Source A Chords", "clean_match", "chords", "Use Source A's chord arrangement.", True, "A"),
            IssueAction("use_source_b_chords", "Use Source B Chords", "clean_match", "chords", "Use Source B's chord arrangement.", False, "B"),
            IssueAction("use_canonical_lyrics_source_a_chords", "Use Canonical Lyrics + Source A Chords", "clean_match", "chords", "Record Source A chords over canonical lyrics.", False, "A"),
            IssueAction("use_canonical_lyrics_source_b_chords", "Use Canonical Lyrics + Source B Chords", "clean_match", "chords", "Record Source B chords over canonical lyrics.", False, "B"),
            IssueAction("keep_multiple_arrangements", "Keep Multiple Arrangements", class_key, "keep-both", "Retain more than one valid chord arrangement."),
            IssueAction("mark_source_a_arrangement_preferred", "Mark Source A Arrangement as Preferred", "clean_match", "chords", "Prefer Source A's arrangement.", False, "A"),
            IssueAction("mark_source_b_arrangement_preferred", "Mark Source B Arrangement as Preferred", "clean_match", "chords", "Prefer Source B's arrangement.", False, "B"),
            IssueAction("keep_unresolved", "Keep Unresolved", "needs_review", "unresolved", "Leave this for later review."),
            IssueAction("skip", "Skip", class_key, "skip", "Move to the next item."),
        ]
    return [
        IssueAction("mark_same_song", "Mark Same Song", "clean_match", "lyrics", "Confirm these sources are the same song.", True),
        IssueAction("use_source_a", "Use Source A", "clean_match", "lyrics", "Use Source A as preferred.", False, "A"),
        IssueAction("use_source_b", "Use Source B", "clean_match", "lyrics", "Use Source B as preferred.", False, "B"),
        IssueAction("keep_both_all", "Keep Both/All", class_key, "keep-both", "Keep all visible sources."),
        IssueAction("split_not_same_song", "Split Match Group / Not Same Song", "needs_review", "split", "Record these as not the same song."),
        IssueAction("keep_unresolved", "Keep Unresolved", "needs_review", "unresolved", "Leave this for later review."),
        IssueAction("skip", "Skip", class_key, "skip", "Move to the next item."),
    ]


def build_decision_note(
    candidate: ReviewCandidate,
    action_id: str,
    source_a: Optional[ComparisonSource] = None,
    source_b: Optional[ComparisonSource] = None,
    user_note: str = "",
) -> str:
    label_by_action = {action.action_id: action.label for action in available_issue_actions(candidate)}
    action_label = label_by_action.get(action_id, action_id.replace("_", " "))
    source_bits = []
    if source_a:
        source_bits.append(f"Source A: {source_a.source_repo} | {source_a.title} | {source_a.source_path}")
    if source_b:
        source_bits.append(f"Source B: {source_b.source_repo} | {source_b.title} | {source_b.source_path}")
    parts = [f"{action_label}."]
    parts.extend(source_bits)
    if user_note.strip():
        parts.append(user_note.strip())
    return " | ".join(parts)


def _normalized_title(value: str) -> str:
    try:
        return builder.normalize_title(value)
    except AttributeError:
        value = re.sub(r"[^a-z0-9]+", " ", value.lower())
        return re.sub(r"\s+", " ", value).strip()


def _guess_source_format(path: Path, source_repo: str = "") -> str:
    suffix = path.suffix.lower()
    if suffix == ".xml":
        return "openlyrics"
    if suffix == ".onsong":
        return "onsong"
    return "txt_chordpro"


def _snapshot_text_from_parsed(parsed: Dict[str, Any]) -> str:
    lines: List[str] = []
    for directive, key in (
        ("title", "title"),
        ("artist", "artist"),
        ("author", "author"),
        ("key", "key"),
        ("tempo", "tempo"),
        ("time", "time_signature"),
        ("copyright", "copyright"),
    ):
        value = str(parsed.get(key, "") or "").strip()
        if value:
            lines.append(f"{{{directive}: {value}}}")
    chordpro_body = str(parsed.get("chordpro_body", "") or "").strip()
    plain_lyrics = str(parsed.get("plain_lyrics", "") or "").strip()
    body = chordpro_body if parsed.get("has_chords") and chordpro_body else plain_lyrics
    if body:
        if lines:
            lines.append("")
        lines.extend(body.splitlines())
    return "\n".join(lines).strip()


def _source_features_from_text(text: str) -> Dict[str, Any]:
    return detect_song_features(text)


class ReviewResolverContext:
    def __init__(
        self,
        out_dir: Path,
        builder_cache_db_path: Optional[Path] = None,
        logger: Optional[ReviewDebugLogger] = None,
    ) -> None:
        self.out_dir = Path(out_dir)
        self.reports_dir = self.out_dir / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.out_dir / REVIEW_RESOLVER_CACHE_FILE
        self.builder_cache_db_path = self._resolve_builder_cache_path(builder_cache_db_path)
        self.logger = logger or ReviewDebugLogger(self.out_dir)
        self._lock = threading.RLock()
        self._index_lock = threading.Lock()
        self._closed = False
        self.conn = sqlite3.connect(str(self.cache_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    @classmethod
    def open(
        cls,
        out_dir: Path,
        builder_cache_db_path: Optional[Path] = None,
        logger: Optional[ReviewDebugLogger] = None,
    ) -> "ReviewResolverContext":
        return cls(out_dir, builder_cache_db_path=builder_cache_db_path, logger=logger)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self.conn.commit()
            self.conn.close()
            self._closed = True

    def __enter__(self) -> "ReviewResolverContext":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def _resolve_builder_cache_path(self, configured: Optional[Path]) -> Optional[Path]:
        candidates: List[Path] = []
        if configured:
            candidates.append(Path(configured))
        candidates.extend(
            [
                self.out_dir / "song_repo_cache.sqlite",
                self.out_dir / "_cache" / "song_repo_cache.sqlite",
            ]
        )
        for path in candidates:
            if path.exists():
                return path
        return Path(configured) if configured else None

    def _init_schema(self) -> None:
        with self._lock:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS report_index_meta (
                    report_name TEXT PRIMARY KEY,
                    report_path TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    row_count INTEGER NOT NULL,
                    indexed_at TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS report_rows (
                    report_name TEXT NOT NULL,
                    norm_title TEXT NOT NULL,
                    row_json TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_report_rows_lookup ON report_rows(report_name, norm_title)"
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_snapshots (
                    snapshot_key TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    snapshot_version TEXT NOT NULL,
                    source_repo TEXT NOT NULL,
                    title TEXT NOT NULL,
                    text TEXT NOT NULL,
                    has_chords INTEGER NOT NULL,
                    chord_count INTEGER NOT NULL,
                    line_count INTEGER NOT NULL,
                    snapshot_source TEXT NOT NULL,
                    warning TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS diff_cache (
                    diff_key TEXT PRIMARY KEY,
                    source_a_key TEXT NOT NULL,
                    source_b_key TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    diff_version TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            self.conn.commit()

    def ensure_indexes(self) -> None:
        with self._index_lock:
            start = _now_perf()
            for report_name, filename in (
                ("pair_scores", "07_group_pair_scores.csv"),
                ("title_conflicts", "08_same_title_different_lyrics.csv"),
            ):
                self._ensure_report_index(report_name, self.reports_dir / filename)
            self.logger.log("ensure_indexes", elapsed_ms=_elapsed_ms(start), cache_path=self.cache_path)

    def _report_is_indexed(self, report_name: str, path: Path) -> bool:
        if not path.exists():
            return True
        stat = path.stat()
        with self._lock:
            row = self.conn.execute(
                "SELECT size, mtime_ns FROM report_index_meta WHERE report_name = ?",
                (report_name,),
            ).fetchone()
        return bool(row and int(row["size"]) == stat.st_size and int(row["mtime_ns"]) == stat.st_mtime_ns)

    def _ensure_report_index(self, report_name: str, path: Path) -> None:
        if self._report_is_indexed(report_name, path):
            self.logger.log("report_index_hit", report_name=report_name, path=path)
            return
        start = _now_perf()
        row_count = 0
        indexed_rows: List[tuple[str, str, str]] = []
        if path.exists():
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    row_count += 1
                    row_json = json.dumps(row, ensure_ascii=False)
                    titles = {
                        _normalized_title(str(row.get("a_title", "") or "")),
                        _normalized_title(str(row.get("b_title", "") or "")),
                        _normalized_title(str(row.get("title", "") or "")),
                        _normalized_title(str(row.get("canonical_title", "") or "")),
                    }
                    for title in titles:
                        if title:
                            indexed_rows.append((report_name, title, row_json))
        stat_size = path.stat().st_size if path.exists() else 0
        stat_mtime = path.stat().st_mtime_ns if path.exists() else 0
        with self._lock:
            self.conn.execute("DELETE FROM report_rows WHERE report_name = ?", (report_name,))
            if indexed_rows:
                self.conn.executemany(
                    "INSERT INTO report_rows (report_name, norm_title, row_json) VALUES (?, ?, ?)",
                    indexed_rows,
                )
            self.conn.execute(
                """
                INSERT INTO report_index_meta (report_name, report_path, size, mtime_ns, row_count, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_name) DO UPDATE SET
                    report_path = excluded.report_path,
                    size = excluded.size,
                    mtime_ns = excluded.mtime_ns,
                    row_count = excluded.row_count,
                    indexed_at = excluded.indexed_at
                """,
                (report_name, str(path), stat_size, stat_mtime, row_count, now_iso()),
            )
            self.conn.commit()
        self.logger.log(
            "report_index_build",
            report_name=report_name,
            path=path,
            row_count=row_count,
            indexed_entries=len(indexed_rows),
            elapsed_ms=_elapsed_ms(start),
        )

    def _indexed_rows_for_title(self, report_name: str, title: str) -> List[Dict[str, str]]:
        norm_title = _normalized_title(title)
        if not norm_title:
            return []
        start = _now_perf()
        with self._lock:
            rows = self.conn.execute(
                "SELECT row_json FROM report_rows WHERE report_name = ? AND norm_title = ?",
                (report_name, norm_title),
            ).fetchall()
        parsed = [json.loads(row["row_json"]) for row in rows]
        self.logger.log(
            "index_lookup",
            report_name=report_name,
            title=title,
            rows=len(parsed),
            elapsed_ms=_elapsed_ms(start),
        )
        return parsed

    def load_candidate_details(self, candidate: ReviewCandidate) -> ReviewCandidate:
        start = _now_perf()
        self.ensure_indexes()
        source_path = resolve_report_path(self.out_dir, candidate.source_path)
        candidate.export_text = read_text(candidate.export_path)
        candidate.source_text = read_text(source_path) if source_path.exists() else ""
        candidate.pair_details = self._indexed_rows_for_title("pair_scores", candidate.title)
        candidate.conflict_details = self._indexed_rows_for_title("title_conflicts", candidate.title)
        self.logger.log(
            "load_candidate_details",
            group_id=candidate.group_id,
            title=candidate.title,
            pair_rows=len(candidate.pair_details),
            conflict_rows=len(candidate.conflict_details),
            elapsed_ms=_elapsed_ms(start),
        )
        return candidate

    def _builder_cache_lookup(self, path: Path, file_hash: str) -> Optional[Dict[str, Any]]:
        if not self.builder_cache_db_path or not self.builder_cache_db_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(self.builder_cache_db_path))
            conn.row_factory = sqlite3.Row
            try:
                if not file_hash:
                    row = conn.execute(
                        "SELECT file_hash FROM source_files WHERE path = ?",
                        (str(path),),
                    ).fetchone()
                    if row:
                        file_hash = str(row["file_hash"])
                if not file_hash:
                    return None
                parsed = conn.execute(
                    """
                    SELECT * FROM parsed_songs
                    WHERE file_hash = ? AND parser_version = ?
                    """,
                    (file_hash, builder.PARSER_VERSION),
                ).fetchone()
                if not parsed:
                    return None
                data = dict(parsed)
                data["lyric_lines"] = json.loads(data.pop("lyric_lines_json") or "[]")
                data["source_meta"] = json.loads(data.pop("source_meta_json") or "{}")
                data["file_hash"] = file_hash
                return data
            finally:
                conn.close()
        except sqlite3.Error as exc:
            self.logger.exception("builder_cache_lookup_error", exc, path=path)
            return None

    def _source_file_hash(self, path: Path, configured_hash: str = "") -> str:
        if configured_hash:
            return configured_hash
        if self.builder_cache_db_path and self.builder_cache_db_path.exists():
            try:
                conn = sqlite3.connect(str(self.builder_cache_db_path))
                try:
                    row = conn.execute("SELECT file_hash FROM source_files WHERE path = ?", (str(path),)).fetchone()
                    if row:
                        return str(row[0])
                finally:
                    conn.close()
            except sqlite3.Error:
                pass
        if path.exists():
            return builder.file_sha256(path)
        return _hash_text(f"missing|{path}")

    def _snapshot_key(self, path: Path, file_hash: str) -> str:
        return _hash_text(f"{path}|{file_hash}|{builder.PARSER_VERSION}|{SNAPSHOT_VERSION}")

    def load_source_snapshot(self, source: ComparisonSource) -> SourceSnapshot:
        start = _now_perf()
        path = resolve_report_path(self.out_dir, source.source_path)
        file_hash = self._source_file_hash(path, source.file_hash)
        snapshot_key = self._snapshot_key(path, file_hash)
        with self._lock:
            cached = self.conn.execute(
                "SELECT * FROM source_snapshots WHERE snapshot_key = ?",
                (snapshot_key,),
            ).fetchone()
        if cached:
            snapshot = self._snapshot_from_row(source, cached, cache_hit=True)
            self._apply_snapshot_to_source(source, snapshot)
            self.logger.log(
                "source_snapshot",
                cache_hit=1,
                source_path=path,
                source=source.snapshot_source,
                elapsed_ms=_elapsed_ms(start),
            )
            return snapshot

        parsed = self._builder_cache_lookup(path, file_hash)
        warning = ""
        if parsed:
            text = _snapshot_text_from_parsed(parsed)
            snapshot_source = "builder_cache"
            file_hash = str(parsed.get("file_hash") or file_hash)
        elif path.exists():
            try:
                source_format = _guess_source_format(path, source.source_repo)
                parsed = builder.parse_file(path, source_format)
                text = _snapshot_text_from_parsed(parsed)
                snapshot_source = "parsed_source"
            except Exception as exc:
                self.logger.exception("source_snapshot_parse_error", exc, source_path=path)
                text = read_text(path)
                warning = f"WARNING: Parsed snapshot failed; showing raw source text. {type(exc).__name__}: {exc}"
                text = f"{warning}\n\n{text}"
                snapshot_source = "raw_fallback"
        else:
            warning = "WARNING: Source file is missing; no review snapshot is available."
            text = warning
            snapshot_source = "missing"

        features = _source_features_from_text(text)
        snapshot = SourceSnapshot(
            source=source,
            text=text,
            file_hash=file_hash,
            snapshot_key=snapshot_key,
            cache_hit=snapshot_source in {"builder_cache"},
            snapshot_source=snapshot_source,
            warning=warning,
            has_chords=bool(features["has_chords"]),
            chord_count=int(features["chord_count"]),
            line_count=int(features["line_count"]),
        )
        self._save_source_snapshot(path, source, snapshot)
        self._apply_snapshot_to_source(source, snapshot)
        self.logger.log(
            "source_snapshot",
            cache_hit=int(snapshot.cache_hit),
            source_path=path,
            source=snapshot.snapshot_source,
            elapsed_ms=_elapsed_ms(start),
        )
        return snapshot

    def _snapshot_from_row(self, source: ComparisonSource, row: sqlite3.Row, cache_hit: bool) -> SourceSnapshot:
        return SourceSnapshot(
            source=source,
            text=str(row["text"]),
            file_hash=str(row["file_hash"]),
            snapshot_key=str(row["snapshot_key"]),
            cache_hit=cache_hit,
            snapshot_source=str(row["snapshot_source"]),
            warning=str(row["warning"]),
            has_chords=bool(row["has_chords"]),
            chord_count=int(row["chord_count"]),
            line_count=int(row["line_count"]),
        )

    def _save_source_snapshot(self, path: Path, source: ComparisonSource, snapshot: SourceSnapshot) -> None:
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO source_snapshots (
                    snapshot_key, source_path, file_hash, parser_version, snapshot_version,
                    source_repo, title, text, has_chords, chord_count, line_count,
                    snapshot_source, warning, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_key) DO UPDATE SET
                    text = excluded.text,
                    has_chords = excluded.has_chords,
                    chord_count = excluded.chord_count,
                    line_count = excluded.line_count,
                    snapshot_source = excluded.snapshot_source,
                    warning = excluded.warning
                """,
                (
                    snapshot.snapshot_key,
                    str(path),
                    snapshot.file_hash,
                    builder.PARSER_VERSION,
                    SNAPSHOT_VERSION,
                    source.source_repo,
                    source.title,
                    snapshot.text,
                    int(snapshot.has_chords),
                    snapshot.chord_count,
                    snapshot.line_count,
                    snapshot.snapshot_source,
                    snapshot.warning,
                    now_iso(),
                ),
            )
            self.conn.commit()

    def _apply_snapshot_to_source(self, source: ComparisonSource, snapshot: SourceSnapshot) -> None:
        source.text = snapshot.text
        source.file_hash = snapshot.file_hash
        source.snapshot_key = snapshot.snapshot_key
        source.snapshot_cache_hit = snapshot.cache_hit
        source.snapshot_source = snapshot.snapshot_source
        source.warning = snapshot.warning
        source.has_chords = snapshot.has_chords
        source.chord_count = snapshot.chord_count
        source.line_count = snapshot.line_count
        source.completeness = _source_features_from_text(snapshot.text).get("completeness", "")

    def compute_or_load_diff(self, pair: SelectedComparisonPair, classification: str) -> CachedDiffResult:
        start = _now_perf()
        source_a_key = pair.source_a.file_hash or pair.source_a.snapshot_key or _hash_text(pair.source_a.text)
        source_b_key = pair.source_b.file_hash or pair.source_b.snapshot_key or _hash_text(pair.source_b.text)
        diff_key = _hash_text(f"{source_a_key}|{source_b_key}|{classification}|{DIFF_VERSION}")
        with self._lock:
            cached = self.conn.execute(
                "SELECT result_json FROM diff_cache WHERE diff_key = ?",
                (diff_key,),
            ).fetchone()
        if cached:
            result = diff_result_from_json(json.loads(cached["result_json"]))
            self.logger.log(
                "diff_cache_lookup",
                cache_hit=1,
                diff_key=diff_key,
                elapsed_ms=_elapsed_ms(start),
            )
            return CachedDiffResult(result=result, cache_hit=True, diff_key=diff_key)

        result = compute_song_diff(pair.source_a.text, pair.source_b.text, classification)
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO diff_cache (
                    diff_key, source_a_key, source_b_key, classification, diff_version, result_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(diff_key) DO UPDATE SET
                    result_json = excluded.result_json,
                    created_at = excluded.created_at
                """,
                (
                    diff_key,
                    source_a_key,
                    source_b_key,
                    classification,
                    DIFF_VERSION,
                    json.dumps(diff_result_to_json(result), ensure_ascii=False),
                    now_iso(),
                ),
            )
            self.conn.commit()
        self.logger.log(
            "diff_compute",
            cache_hit=0,
            diff_key=diff_key,
            simplified=int(result.simplified),
            truncated=int(result.truncated),
            elapsed_ms=_elapsed_ms(start),
        )
        return CachedDiffResult(result=result, cache_hit=False, diff_key=diff_key)


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
                updated_at TEXT NOT NULL,
                action_label TEXT NOT NULL DEFAULT '',
                source_a_repo TEXT NOT NULL DEFAULT '',
                source_a_title TEXT NOT NULL DEFAULT '',
                source_a_path TEXT NOT NULL DEFAULT '',
                source_b_repo TEXT NOT NULL DEFAULT '',
                source_b_title TEXT NOT NULL DEFAULT '',
                source_b_path TEXT NOT NULL DEFAULT '',
                chosen_source_marker TEXT NOT NULL DEFAULT '',
                chosen_source_repo TEXT NOT NULL DEFAULT '',
                chosen_source_title TEXT NOT NULL DEFAULT '',
                chosen_source_path TEXT NOT NULL DEFAULT '',
                decision_category TEXT NOT NULL DEFAULT '',
                auto_note TEXT NOT NULL DEFAULT '',
                user_note TEXT NOT NULL DEFAULT ''
            )
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(manual_review_decisions)")}
        extra_columns = {
            "action_label": "TEXT NOT NULL DEFAULT ''",
            "source_a_repo": "TEXT NOT NULL DEFAULT ''",
            "source_a_title": "TEXT NOT NULL DEFAULT ''",
            "source_a_path": "TEXT NOT NULL DEFAULT ''",
            "source_b_repo": "TEXT NOT NULL DEFAULT ''",
            "source_b_title": "TEXT NOT NULL DEFAULT ''",
            "source_b_path": "TEXT NOT NULL DEFAULT ''",
            "chosen_source_marker": "TEXT NOT NULL DEFAULT ''",
            "chosen_source_repo": "TEXT NOT NULL DEFAULT ''",
            "chosen_source_title": "TEXT NOT NULL DEFAULT ''",
            "chosen_source_path": "TEXT NOT NULL DEFAULT ''",
            "decision_category": "TEXT NOT NULL DEFAULT ''",
            "auto_note": "TEXT NOT NULL DEFAULT ''",
            "user_note": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in extra_columns.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE manual_review_decisions ADD COLUMN {column} {definition}")
        conn.execute(
            """
            INSERT INTO manual_review_decisions (
                group_id, decision_id, original_classification, chosen_classification,
                original_export_path, final_export_path, action, note, file_hash,
                canonical_source_path, created_at, updated_at, action_label,
                source_a_repo, source_a_title, source_a_path, source_b_repo,
                source_b_title, source_b_path, chosen_source_marker,
                chosen_source_repo, chosen_source_title, chosen_source_path,
                decision_category, auto_note, user_note
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                updated_at = excluded.updated_at,
                action_label = excluded.action_label,
                source_a_repo = excluded.source_a_repo,
                source_a_title = excluded.source_a_title,
                source_a_path = excluded.source_a_path,
                source_b_repo = excluded.source_b_repo,
                source_b_title = excluded.source_b_title,
                source_b_path = excluded.source_b_path,
                chosen_source_marker = excluded.chosen_source_marker,
                chosen_source_repo = excluded.chosen_source_repo,
                chosen_source_title = excluded.chosen_source_title,
                chosen_source_path = excluded.chosen_source_path,
                decision_category = excluded.decision_category,
                auto_note = excluded.auto_note,
                user_note = excluded.user_note
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
                decision.action_label,
                decision.source_a_repo,
                decision.source_a_title,
                decision.source_a_path,
                decision.source_b_repo,
                decision.source_b_title,
                decision.source_b_path,
                decision.chosen_source_marker,
                decision.chosen_source_repo,
                decision.chosen_source_title,
                decision.chosen_source_path,
                decision.decision_category,
                decision.auto_note,
                decision.user_note,
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
    *,
    action_label: str = "",
    source_a: Optional[ComparisonSource] = None,
    source_b: Optional[ComparisonSource] = None,
    chosen_source: Optional[ComparisonSource] = None,
    decision_category: str = "",
    auto_note: str = "",
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

    user_note = note.strip()
    final_note = " | ".join(part for part in (auto_note.strip(), user_note) if part)
    timestamp = now_iso()
    decision = ManualReviewDecision(
        decision_id=f"{candidate.group_id}-{int(time.time())}",
        group_id=candidate.group_id,
        original_classification=candidate.classification,
        chosen_classification=chosen_classification,
        original_export_path=original,
        final_export_path=final_path,
        action=action,
        note=final_note,
        file_hash=candidate.file_hash,
        canonical_source_path=candidate.source_path,
        created_at=timestamp,
        updated_at=timestamp,
        action_label=action_label,
        source_a_repo=source_a.source_repo if source_a else "",
        source_a_title=source_a.title if source_a else "",
        source_a_path=source_a.source_path if source_a else "",
        source_b_repo=source_b.source_repo if source_b else "",
        source_b_title=source_b.title if source_b else "",
        source_b_path=source_b.source_path if source_b else "",
        chosen_source_marker=chosen_source.marker if chosen_source else "",
        chosen_source_repo=chosen_source.source_repo if chosen_source else "",
        chosen_source_title=chosen_source.title if chosen_source else "",
        chosen_source_path=chosen_source.source_path if chosen_source else "",
        decision_category=decision_category,
        auto_note=auto_note,
        user_note=user_note,
    )
    save_manual_decision(out_dir, decision)
    update_manual_decision_db(cache_db_path, decision)
    return decision
