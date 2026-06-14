#!/usr/bin/env python3
"""Post-processing helpers for manual song repository review decisions."""

from __future__ import annotations

import csv
import difflib
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


def compute_song_diff(text_a: str, text_b: str, classification: str = "") -> SongDiffResult:
    lines_a = text_a.splitlines()
    lines_b = text_b.splitlines()
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
    return SongDiffResult(
        lines=diff_lines,
        lyric_similarity=lyric_similarity,
        chord_difference_count=chord_difference_count,
        metadata_difference_count=metadata_difference_count,
        summary="; ".join(summary_parts),
        likely_partial_side=likely_partial_side,
        likely_separate_song=likely_separate_song,
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
