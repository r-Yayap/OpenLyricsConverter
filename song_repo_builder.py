#!/usr/bin/env python3
"""
Build a classified ChordPro song repository with hash-based caching.

Main goals:
- Use lyrics as the defining identity criteria.
- Ignore case, punctuation, spacing, and most chord-only/instrumental lines.
- Match clean lyrics against fuller chorded arrangements using line coverage.
- Save source hashes, parsed songs, and pair scores in SQLite for fast reruns.
- Export each canonical group to exactly one classification folder.

This script intentionally uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


SCRIPT_VERSION = "2026.06.13.cached.4"
PARSER_VERSION = "parser.2026.06.13.2"
MATCHER_VERSION = "matcher.lyric_identity.2026.06.13.3"


CLASS_FOLDERS = {
    "clean_match": "01_clean_match_chordpro",
    "single_source": "02_single_source_chordpro",
    "lyrics_only": "03_lyrics_only_chordpro",
    "lyric_match_title_different": "04_lyric_match_title_different_chordpro",
    "title_match_lyrics_different": "05_title_match_lyrics_different_chordpro",
    "multiple_chorded_sources": "06_multiple_chorded_sources_chordpro",
    "unmatched_non_openlyrics": "07_unmatched_non_openlyrics_chordpro",
    "needs_review": "08_needs_review_chordpro",
}


OPENLYRICS_REPO = "01_FreeWorship_OpenLyrics"
ONSONG_REPO = "02_OnSong"
TXT_REPO = "03_TXT_ChordPro"


SECTION_WORDS = {
    "intro",
    "verse",
    "v",
    "chorus",
    "c",
    "prechorus",
    "pre chorus",
    "pre-chorus",
    "pc",
    "bridge",
    "b",
    "tag",
    "ending",
    "end",
    "outro",
    "instrumental",
    "interlude",
    "refrain",
    "repeat",
}


COMMON_WEAK_LINES = {
    "amen",
    "hallelujah",
    "alleluia",
    "oh",
    "o",
    "yeah",
    "yes",
    "lord",
    "jesus",
}


CHORD_TOKEN_RE = re.compile(
    r"""
    ^
    [A-G](?:\#|b)?
    (?:
        m|maj|min|dim|aug|sus|add|mmaj
    )?
    (?:\d+)?
    (?:\([^)]+\))?
    (?:/[A-G](?:\#|b)?)?
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)

INLINE_CHORD_RE = re.compile(r"\[([^\[\]\n]{1,32})\]")
METADATA_RE = re.compile(r"^\s*\{([^:{}]+)\s*:\s*(.*?)\s*\}\s*$")
COMMENT_RE = re.compile(r"^\s*\{(?:c|comment)\s*:\s*(.*?)\s*\}\s*$", re.IGNORECASE)

OPENLYRICS_STRUCTURE_NOTATION = {
    "major": "",
    "maj": "",
    "min": "m",
    "minor": "m",
    "dom7": "7",
    "maj7": "maj7",
    "min7": "m7",
    "dim": "dim",
    "aug": "aug",
    "sus4": "sus4",
    "sus2": "sus2",
    "add9": "add9",
}


@dataclass
class Song:
    index: int
    source_repo: str
    source_format: str
    source_path: str
    file_hash: str
    size: int
    mtime_ns: int
    title: str = ""
    artist: str = ""
    key: str = ""
    tempo: str = ""
    time_signature: str = ""
    copyright: str = ""
    has_chords: bool = False
    chord_count: int = 0
    line_count: int = 0
    plain_lyrics: str = ""
    normalized_lyrics: str = ""
    lyric_lines: List[str] = field(default_factory=list)
    chordpro_body: str = ""
    parse_status: str = "ok"
    parse_error: str = ""
    normalized_title: str = field(init=False)
    compact_title: str = field(init=False)
    title_tokens: List[str] = field(init=False)
    lyric_shingles: List[str] = field(init=False)

    def __post_init__(self) -> None:
        self.normalized_title = normalize_title(self.title)
        self.compact_title = compact_title(self.title)
        self.title_tokens = title_tokens(self.title)
        self.lyric_shingles = lyric_shingles(self.normalized_lyrics)

    @property
    def source_uid(self) -> str:
        raw = f"{self.source_repo}|{self.source_path}|{self.file_hash}"
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


@dataclass
class PairScore:
    a: int
    b: int
    title_score: float
    lyric_score: float
    line_coverage_a_in_b: float
    line_coverage_b_in_a: float
    line_coverage_max: float
    line_coverage_min: float
    lyric_identity_score: float
    final_score: float
    shared_line_count: int
    decision_hint: str
    cache_hit: bool = False


class UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
        elif self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            self.rank[ra] += 1


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def default_worker_count() -> int:
    cpu_count = os.cpu_count() or 2
    if cpu_count <= 1:
        return 1
    return max(1, min(8, cpu_count - 1))


def normalized_worker_count(value: int) -> int:
    return max(1, int(value or 1))


def norm_space(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def safe_read_text(path: Path) -> str:
    raw = path.read_bytes()

    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass

    for encoding in ("utf-8-sig", "utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def is_valid_chord_token(token: str) -> bool:
    token = token.strip()
    if not token:
        return False
    if " " in token:
        return False
    return bool(CHORD_TOKEN_RE.match(token))


def is_chord_only_or_instrumental_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True

    without_chords = INLINE_CHORD_RE.sub(" ", stripped)
    without_chords = re.sub(r"[.\-_/|,:;()\s]+", " ", without_chords)
    words = [w for w in without_chords.split() if w]
    if not words and INLINE_CHORD_RE.search(stripped):
        return True

    raw_tokens = re.split(r"\s+", stripped.replace(".", " "))
    chordish = 0
    non_empty = 0
    for token in raw_tokens:
        token = token.strip("[](){}.,;:|")
        if not token:
            continue
        non_empty += 1
        if is_valid_chord_token(token):
            chordish += 1
    return non_empty > 0 and chordish == non_empty


def bracket_section_label(line: str) -> Optional[str]:
    stripped = line.strip()
    match = re.fullmatch(r"\[([^\[\]]{1,40})\]", stripped)
    if not match:
        return None

    label = norm_space(match.group(1)).lower()
    if is_valid_chord_token(label):
        return None

    label_base = re.sub(r"\d+", "", label)
    label_base = norm_space(label_base.replace("-", " "))
    if label in SECTION_WORDS or label_base in SECTION_WORDS:
        return match.group(1).strip()
    return None


def strip_inline_chords(line: str) -> Tuple[str, int]:
    chord_count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal chord_count
        token = match.group(1).strip()
        if is_valid_chord_token(token):
            chord_count += 1
            return ""
        return match.group(0)

    return INLINE_CHORD_RE.sub(replace, line), chord_count


def normalize_title(text: str) -> str:
    text = text or ""
    text = text.lower().replace("&", " and ")
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    text = re.sub(r"\b0+(\d+)\b", r"\1", text)
    return norm_space(text)


def compact_title(text: str) -> str:
    return re.sub(r"\s+", "", normalize_title(text))


def normalize_lyric_text(text: str) -> str:
    text = text or ""
    text = text.lower().replace("&", " and ")
    text = re.sub(r"(?<=\d),(?=\d)", "", text)
    text = re.sub(r"\b10\s+000\b", "10000", text)
    text = re.sub(r"([a-z])\s*-\s*([a-z])", r"\1\2", text)
    text = re.sub(r"\boh\b", "o", text)
    text = re.sub(r"\byeah\b", " ", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return norm_space(text)


def normalize_lyric_line(line: str) -> str:
    stripped, _ = strip_inline_chords(line)
    return normalize_lyric_text(stripped)


def meaningful_lines_from_text(text: str) -> List[str]:
    lines: List[str] = []
    seen: Set[str] = set()
    for raw_line in (text or "").splitlines():
        line = normalize_lyric_line(raw_line)
        if not is_meaningful_line(line):
            continue
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines


def is_meaningful_line(line: str) -> bool:
    line = norm_space(line)
    if not line:
        return False
    if line in COMMON_WEAK_LINES:
        return False
    words = line.split()
    if len(words) <= 1 and len(line) < 8:
        return False
    if len(line) < 5:
        return False
    return True


def title_from_filename(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    # Common pattern: Artist - Title
    if " - " in stem:
        parts = [p.strip() for p in stem.split(" - ") if p.strip()]
        if len(parts) >= 2:
            return parts[-1]
    return stem


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class CacheDB:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS source_files (
                path TEXT PRIMARY KEY,
                source_repo TEXT NOT NULL,
                source_format TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                file_hash TEXT NOT NULL,
                last_seen_run TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS parsed_songs (
                file_hash TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                title TEXT,
                artist TEXT,
                key TEXT,
                tempo TEXT,
                time_signature TEXT,
                copyright TEXT,
                has_chords INTEGER NOT NULL,
                chord_count INTEGER NOT NULL,
                line_count INTEGER NOT NULL,
                plain_lyrics TEXT NOT NULL,
                normalized_lyrics TEXT NOT NULL,
                lyric_lines_json TEXT NOT NULL,
                chordpro_body TEXT NOT NULL,
                parse_status TEXT NOT NULL,
                parse_error TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (file_hash, parser_version)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pair_scores (
                hash_a TEXT NOT NULL,
                hash_b TEXT NOT NULL,
                matcher_version TEXT NOT NULL,
                matcher_settings_hash TEXT NOT NULL,
                title_score REAL NOT NULL,
                lyric_score REAL NOT NULL,
                line_coverage_a_in_b REAL NOT NULL,
                line_coverage_b_in_a REAL NOT NULL,
                line_coverage_max REAL NOT NULL,
                line_coverage_min REAL NOT NULL,
                lyric_identity_score REAL NOT NULL,
                final_score REAL NOT NULL,
                shared_line_count INTEGER NOT NULL,
                decision_hint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (hash_a, hash_b, matcher_version, matcher_settings_hash)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS run_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def get_cached_hash(
        self,
        path: Path,
        source_repo: str,
        source_format: str,
        size: int,
        mtime_ns: int,
        run_id: str,
        verify_hashes: bool,
    ) -> Tuple[str, bool]:
        path_s = str(path)
        row = self.conn.execute(
            "SELECT size, mtime_ns, file_hash FROM source_files WHERE path = ?",
            (path_s,),
        ).fetchone()
        if (
            row
            and not verify_hashes
            and int(row["size"]) == size
            and int(row["mtime_ns"]) == mtime_ns
        ):
            file_hash = str(row["file_hash"])
            cache_hit = True
        else:
            file_hash = file_sha256(path)
            cache_hit = False

        self.conn.execute(
            """
            INSERT INTO source_files
                (path, source_repo, source_format, size, mtime_ns, file_hash, last_seen_run, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                source_repo = excluded.source_repo,
                source_format = excluded.source_format,
                size = excluded.size,
                mtime_ns = excluded.mtime_ns,
                file_hash = excluded.file_hash,
                last_seen_run = excluded.last_seen_run,
                updated_at = excluded.updated_at
            """,
            (path_s, source_repo, source_format, size, mtime_ns, file_hash, run_id, now_iso()),
        )
        return file_hash, cache_hit

    def get_parsed_song(self, file_hash: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            """
            SELECT * FROM parsed_songs
            WHERE file_hash = ? AND parser_version = ?
            """,
            (file_hash, PARSER_VERSION),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["lyric_lines"] = json.loads(data.pop("lyric_lines_json") or "[]")
        return data

    def save_parsed_song(self, file_hash: str, data: Dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO parsed_songs (
                file_hash, parser_version, title, artist, key, tempo, time_signature, copyright,
                has_chords, chord_count, line_count, plain_lyrics, normalized_lyrics,
                lyric_lines_json, chordpro_body, parse_status, parse_error, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_hash, parser_version) DO UPDATE SET
                title = excluded.title,
                artist = excluded.artist,
                key = excluded.key,
                tempo = excluded.tempo,
                time_signature = excluded.time_signature,
                copyright = excluded.copyright,
                has_chords = excluded.has_chords,
                chord_count = excluded.chord_count,
                line_count = excluded.line_count,
                plain_lyrics = excluded.plain_lyrics,
                normalized_lyrics = excluded.normalized_lyrics,
                lyric_lines_json = excluded.lyric_lines_json,
                chordpro_body = excluded.chordpro_body,
                parse_status = excluded.parse_status,
                parse_error = excluded.parse_error,
                created_at = excluded.created_at
            """,
            (
                file_hash,
                PARSER_VERSION,
                data.get("title", ""),
                data.get("artist", ""),
                data.get("key", ""),
                data.get("tempo", ""),
                data.get("time_signature", ""),
                data.get("copyright", ""),
                1 if data.get("has_chords") else 0,
                int(data.get("chord_count") or 0),
                int(data.get("line_count") or 0),
                data.get("plain_lyrics", ""),
                data.get("normalized_lyrics", ""),
                json.dumps(data.get("lyric_lines", []), ensure_ascii=False),
                data.get("chordpro_body", ""),
                data.get("parse_status", "ok"),
                data.get("parse_error", ""),
                now_iso(),
            ),
        )

    def pair_key(self, hash_a: str, hash_b: str) -> Tuple[str, str]:
        return tuple(sorted((hash_a, hash_b)))  # type: ignore[return-value]

    def get_pair_score(
        self,
        hash_a: str,
        hash_b: str,
        matcher_settings_hash: str,
    ) -> Optional[Dict[str, Any]]:
        ha, hb = self.pair_key(hash_a, hash_b)
        row = self.conn.execute(
            """
            SELECT * FROM pair_scores
            WHERE hash_a = ?
              AND hash_b = ?
              AND matcher_version = ?
              AND matcher_settings_hash = ?
            """,
            (ha, hb, MATCHER_VERSION, matcher_settings_hash),
        ).fetchone()
        return dict(row) if row else None

    def save_pair_score(
        self,
        hash_a: str,
        hash_b: str,
        matcher_settings_hash: str,
        score: Dict[str, Any],
    ) -> None:
        ha, hb = self.pair_key(hash_a, hash_b)
        self.conn.execute(
            """
            INSERT INTO pair_scores (
                hash_a, hash_b, matcher_version, matcher_settings_hash,
                title_score, lyric_score, line_coverage_a_in_b, line_coverage_b_in_a,
                line_coverage_max, line_coverage_min, lyric_identity_score, final_score,
                shared_line_count, decision_hint, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hash_a, hash_b, matcher_version, matcher_settings_hash) DO UPDATE SET
                title_score = excluded.title_score,
                lyric_score = excluded.lyric_score,
                line_coverage_a_in_b = excluded.line_coverage_a_in_b,
                line_coverage_b_in_a = excluded.line_coverage_b_in_a,
                line_coverage_max = excluded.line_coverage_max,
                line_coverage_min = excluded.line_coverage_min,
                lyric_identity_score = excluded.lyric_identity_score,
                final_score = excluded.final_score,
                shared_line_count = excluded.shared_line_count,
                decision_hint = excluded.decision_hint,
                created_at = excluded.created_at
            """,
            (
                ha,
                hb,
                MATCHER_VERSION,
                matcher_settings_hash,
                float(score["title_score"]),
                float(score["lyric_score"]),
                float(score["line_coverage_a_in_b"]),
                float(score["line_coverage_b_in_a"]),
                float(score["line_coverage_max"]),
                float(score["line_coverage_min"]),
                float(score["lyric_identity_score"]),
                float(score["final_score"]),
                int(score["shared_line_count"]),
                score["decision_hint"],
                now_iso(),
            ),
        )

    def save_pair_scores_many(
        self,
        entries: List[Tuple[str, str, str, Dict[str, Any]]],
    ) -> None:
        if not entries:
            return
        rows = []
        created_at = now_iso()
        for hash_a, hash_b, matcher_settings_hash, score in entries:
            ha, hb = self.pair_key(hash_a, hash_b)
            rows.append(
                (
                    ha,
                    hb,
                    MATCHER_VERSION,
                    matcher_settings_hash,
                    float(score["title_score"]),
                    float(score["lyric_score"]),
                    float(score["line_coverage_a_in_b"]),
                    float(score["line_coverage_b_in_a"]),
                    float(score["line_coverage_max"]),
                    float(score["line_coverage_min"]),
                    float(score["lyric_identity_score"]),
                    float(score["final_score"]),
                    int(score["shared_line_count"]),
                    score["decision_hint"],
                    created_at,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO pair_scores (
                hash_a, hash_b, matcher_version, matcher_settings_hash,
                title_score, lyric_score, line_coverage_a_in_b, line_coverage_b_in_a,
                line_coverage_max, line_coverage_min, lyric_identity_score, final_score,
                shared_line_count, decision_hint, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hash_a, hash_b, matcher_version, matcher_settings_hash) DO UPDATE SET
                title_score = excluded.title_score,
                lyric_score = excluded.lyric_score,
                line_coverage_a_in_b = excluded.line_coverage_a_in_b,
                line_coverage_b_in_a = excluded.line_coverage_b_in_a,
                line_coverage_max = excluded.line_coverage_max,
                line_coverage_min = excluded.line_coverage_min,
                lyric_identity_score = excluded.lyric_identity_score,
                final_score = excluded.final_score,
                shared_line_count = excluded.shared_line_count,
                decision_hint = excluded.decision_hint,
                created_at = excluded.created_at
            """,
            rows,
        )

    def set_state(self, key: str, value: Dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO run_state (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False), now_iso()),
        )
        self.conn.commit()


@dataclass
class OpenLyricsLine:
    plain_parts: List[str] = field(default_factory=list)
    chordpro_parts: List[str] = field(default_factory=list)

    def append_text(self, text: Optional[str]) -> None:
        if not text:
            return
        self.plain_parts.append(text)
        self.chordpro_parts.append(text)

    def append_chord(self, chord: str) -> None:
        if chord:
            self.chordpro_parts.append(f"[{chord}]")

    @property
    def plain(self) -> str:
        return norm_space("".join(self.plain_parts))

    @property
    def chordpro(self) -> str:
        return norm_space("".join(self.chordpro_parts))


def format_openlyrics_chord_name(attributes: Dict[str, str]) -> str:
    old_name = (attributes.get("name") or "").strip()
    if old_name:
        return old_name

    root = (attributes.get("root") or "").strip()
    if not root:
        return ""

    structure = (attributes.get("structure") or "").strip()
    suffix = OPENLYRICS_STRUCTURE_NOTATION.get(structure, structure)
    bass = (attributes.get("bass") or "").strip()
    chord = f"{root}{suffix}"
    return f"{chord}/{bass}" if bass else chord


def parse_openlyrics_lines(elem: ET.Element) -> List[Tuple[str, str]]:
    lines = [OpenLyricsLine()]

    def current_line() -> OpenLyricsLine:
        return lines[-1]

    def new_line() -> None:
        if current_line().plain or current_line().chordpro:
            lines.append(OpenLyricsLine())

    def walk(node: ET.Element) -> None:
        if local_name(node.tag) != "br":
            current_line().append_text(node.text)
        for child in list(node):
            tag = local_name(child.tag)
            if tag == "br":
                new_line()
            elif tag == "line":
                walk(child)
                new_line()
            elif tag == "chord":
                current_line().append_chord(format_openlyrics_chord_name(child.attrib))
                walk(child)
            else:
                walk(child)
            current_line().append_text(child.tail)

    walk(elem)
    return [(line.plain, line.chordpro) for line in lines if line.plain or line.chordpro]


def first_xml_text(root: ET.Element, names: Sequence[str]) -> str:
    wanted = {n.lower() for n in names}
    for elem in root.iter():
        if local_name(elem.tag) in wanted:
            text = norm_space("".join(elem.itertext()))
            if text:
                return text
    return ""


def parse_openlyrics(path: Path) -> Dict[str, Any]:
    try:
        text = safe_read_text(path)
        root = ET.fromstring(text)
    except Exception as exc:
        return parse_plain_chordpro(path, forced_error=f"XML parse failed: {exc}")

    title = first_xml_text(root, ("title", "songtitle")) or title_from_filename(path)
    artist = first_xml_text(root, ("author", "artist", "composer", "writer"))
    key = first_xml_text(root, ("key",))
    copyright_text = first_xml_text(root, ("copyright",))

    chord_count = 0
    lines: List[str] = []
    body_lines: List[str] = []

    for elem in root.iter():
        if local_name(elem.tag) == "chord":
            chord_count += 1

    verse_found = False
    for verse in root.iter():
        if local_name(verse.tag) != "verse":
            continue
        verse_found = True
        name = verse.attrib.get("name") or verse.attrib.get("label") or "Verse"
        body_lines.append(f"{{comment: {name}}}")
        verse_line_parts: List[Tuple[str, str]] = []
        lines_elems = [child for child in verse if local_name(child.tag) == "lines"]
        if lines_elems:
            for line_elem in lines_elems:
                verse_line_parts.extend(parse_openlyrics_lines(line_elem))
        else:
            verse_line_parts.extend(parse_openlyrics_lines(verse))

        for plain_line, chorded_line in verse_line_parts:
            clean = norm_space(plain_line)
            if not clean:
                continue
            lines.append(clean)
            body_lines.append(chorded_line or clean)
        body_lines.append("")

    if not verse_found:
        for elem in root.iter():
            if local_name(elem.tag) == "lyrics":
                lyric_line_parts = parse_openlyrics_lines(elem)
                break
        else:
            lyric_line_parts = []
        for plain_line, chorded_line in lyric_line_parts:
            clean = norm_space(plain_line)
            if clean:
                lines.append(clean)
                body_lines.append(chorded_line or clean)

    plain_lyrics = "\n".join(lines)
    lyric_lines = meaningful_lines_from_text(plain_lyrics)
    normalized_lyrics = normalize_lyric_text(plain_lyrics)

    return {
        "title": title,
        "artist": artist,
        "key": key,
        "tempo": "",
        "time_signature": "",
        "copyright": copyright_text,
        "has_chords": chord_count > 0,
        "chord_count": chord_count,
        "line_count": len(lyric_lines),
        "plain_lyrics": plain_lyrics,
        "normalized_lyrics": normalized_lyrics,
        "lyric_lines": lyric_lines,
        "chordpro_body": "\n".join(body_lines).strip(),
        "parse_status": "ok",
        "parse_error": "",
    }


def parse_plain_chordpro(path: Path, forced_error: str = "") -> Dict[str, Any]:
    text = safe_read_text(path)
    title = ""
    artist = ""
    key = ""
    tempo = ""
    time_signature = ""
    copyright_text = ""
    chord_count = 0
    plain_lines: List[str] = []
    body_lines: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\n\r")
        stripped = line.strip()

        meta = METADATA_RE.match(stripped)
        if meta:
            name = meta.group(1).strip().lower()
            value = norm_space(meta.group(2))
            if name in {"title", "t"}:
                title = value
            elif name in {"artist", "subtitle", "st"}:
                artist = value
            elif name == "key":
                key = value
            elif name == "tempo":
                tempo = value
            elif name in {"time", "time_signature"}:
                time_signature = value
            elif name == "copyright":
                copyright_text = value
            body_lines.append(line)
            continue

        comment = COMMENT_RE.match(stripped)
        if comment:
            body_lines.append(line)
            continue

        section = bracket_section_label(stripped)
        if section:
            body_lines.append(f"{{comment: {section}}}")
            continue

        no_chords, found_chords = strip_inline_chords(line)
        chord_count += found_chords
        body_lines.append(line)

        if is_chord_only_or_instrumental_line(line):
            continue

        lyric_line = norm_space(no_chords)
        if lyric_line:
            plain_lines.append(lyric_line)

    if not title:
        title = title_from_filename(path)

    plain_lyrics = "\n".join(plain_lines)
    lyric_lines = meaningful_lines_from_text(plain_lyrics)
    normalized_lyrics = normalize_lyric_text(plain_lyrics)

    parse_status = "warning" if forced_error else "ok"
    parse_error = forced_error

    return {
        "title": title,
        "artist": artist,
        "key": key,
        "tempo": tempo,
        "time_signature": time_signature,
        "copyright": copyright_text,
        "has_chords": chord_count > 0,
        "chord_count": chord_count,
        "line_count": len(lyric_lines),
        "plain_lyrics": plain_lyrics,
        "normalized_lyrics": normalized_lyrics,
        "lyric_lines": lyric_lines,
        "chordpro_body": "\n".join(body_lines).strip(),
        "parse_status": parse_status,
        "parse_error": parse_error,
    }


def parse_file(path: Path, source_format: str) -> Dict[str, Any]:
    if source_format == "openlyrics":
        return parse_openlyrics(path)
    return parse_plain_chordpro(path)


def parse_file_worker(task: Tuple[int, str, str]) -> Tuple[int, Dict[str, Any]]:
    idx, path_s, source_format = task
    return idx, parse_file(Path(path_s), source_format)


def discover_files(openlyrics: Optional[Path], onsong: Optional[Path], txt: Optional[Path]) -> List[Tuple[Path, str, str]]:
    discovered: List[Tuple[Path, str, str]] = []

    def add_from(root: Optional[Path], repo: str, fmt: str, suffixes: Set[str]) -> None:
        if not root:
            return
        if not root.exists():
            print(f"Warning: source folder does not exist: {root}", file=sys.stderr)
            return
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() in suffixes:
                discovered.append((path, repo, fmt))

    add_from(openlyrics, OPENLYRICS_REPO, "openlyrics", {".xml"})
    add_from(onsong, ONSONG_REPO, "onsong", {".onsong", ".txt", ".chopro", ".cho", ".pro"})
    add_from(txt, TXT_REPO, "txt_chordpro", {".txt", ".chopro", ".cho", ".pro"})
    return discovered


def song_from_parsed(
    index: int,
    path: Path,
    source_repo: str,
    source_format: str,
    file_hash: str,
    size: int,
    mtime_ns: int,
    parsed: Dict[str, Any],
) -> Song:
    return Song(
        index=index,
        source_repo=source_repo,
        source_format=source_format,
        source_path=str(path),
        file_hash=file_hash,
        size=size,
        mtime_ns=mtime_ns,
        title=parsed.get("title", "") or title_from_filename(path),
        artist=parsed.get("artist", "") or "",
        key=parsed.get("key", "") or "",
        tempo=parsed.get("tempo", "") or "",
        time_signature=parsed.get("time_signature", "") or "",
        copyright=parsed.get("copyright", "") or "",
        has_chords=bool(parsed.get("has_chords")),
        chord_count=int(parsed.get("chord_count") or 0),
        line_count=int(parsed.get("line_count") or 0),
        plain_lyrics=parsed.get("plain_lyrics", "") or "",
        normalized_lyrics=parsed.get("normalized_lyrics", "") or "",
        lyric_lines=list(parsed.get("lyric_lines") or []),
        chordpro_body=parsed.get("chordpro_body", "") or "",
        parse_status=parsed.get("parse_status", "ok") or "ok",
        parse_error=parsed.get("parse_error", "") or "",
    )


def parse_sources(
    files: List[Tuple[Path, str, str]],
    cache: CacheDB,
    run_id: str,
    verify_hashes: bool,
    force_reparse: bool,
    progress_every: int,
    parse_workers: int = 1,
    parallel_parse_threshold: int = 100,
) -> Tuple[List[Song], Dict[str, int]]:
    parse_workers = normalized_worker_count(parse_workers)
    song_slots: List[Optional[Song]] = [None] * len(files)
    file_info: Dict[int, Tuple[Path, str, str, str, int, int]] = {}
    pending: List[Tuple[int, str, str]] = []
    stats = {
        "files": 0,
        "hash_cache_hits": 0,
        "hash_computed": 0,
        "parse_cache_hits": 0,
        "parsed_now": 0,
        "parse_workers": parse_workers,
        "parallel_parse_threshold": max(1, parallel_parse_threshold),
        "parallel_parse_used": 0,
    }
    total = len(files)
    start = time.time()
    done = 0

    def report_progress() -> None:
        if not progress_every or not total:
            return
        if done != total and done % progress_every != 0:
            return
        elapsed = time.time() - start
        print(
            f"Parsing songs: {done}/{total} elapsed {format_duration(elapsed)} "
            f"| hash_cached={stats['hash_cache_hits']} parsed_cached={stats['parse_cache_hits']} "
            f"parsed={stats['parsed_now']}"
        )
        cache.conn.commit()
        cache.set_state("parse_progress", {"done": done, "total": total, "updated_at": now_iso()})

    for idx, (path, source_repo, source_format) in enumerate(files):
        st = path.stat()
        file_hash, hash_hit = cache.get_cached_hash(
            path,
            source_repo,
            source_format,
            st.st_size,
            st.st_mtime_ns,
            run_id,
            verify_hashes,
        )
        stats["files"] += 1
        stats["hash_cache_hits" if hash_hit else "hash_computed"] += 1

        parsed = None if force_reparse else cache.get_parsed_song(file_hash)
        if parsed:
            stats["parse_cache_hits"] += 1
            song_slots[idx] = song_from_parsed(
                idx,
                path,
                source_repo,
                source_format,
                file_hash,
                st.st_size,
                st.st_mtime_ns,
                parsed,
            )
            done += 1
            report_progress()
        else:
            file_info[idx] = (path, source_repo, source_format, file_hash, st.st_size, st.st_mtime_ns)
            pending.append((idx, str(path), source_format))

    use_parallel = parse_workers > 1 and len(pending) >= max(1, parallel_parse_threshold)

    def save_parsed_result(idx: int, parsed: Dict[str, Any]) -> None:
        nonlocal done
        if song_slots[idx] is not None:
            return
        path, source_repo, source_format, file_hash, size, mtime_ns = file_info[idx]
        cache.save_parsed_song(file_hash, parsed)
        stats["parsed_now"] += 1
        song_slots[idx] = song_from_parsed(idx, path, source_repo, source_format, file_hash, size, mtime_ns, parsed)
        done += 1
        report_progress()

    if pending and use_parallel:
        stats["parallel_parse_used"] = 1
        try:
            with ProcessPoolExecutor(max_workers=parse_workers) as executor:
                futures = [executor.submit(parse_file_worker, task) for task in pending]
                for future in as_completed(futures):
                    idx, parsed = future.result()
                    save_parsed_result(idx, parsed)
        except Exception as exc:
            print(f"Warning: parallel parsing failed; retrying sequentially: {exc}", file=sys.stderr)
            stats["parallel_parse_used"] = 0
            for task in pending:
                idx, parsed = parse_file_worker(task)
                save_parsed_result(idx, parsed)
    else:
        for task in pending:
            idx, parsed = parse_file_worker(task)
            save_parsed_result(idx, parsed)

    cache.conn.commit()
    missing = [str(files[idx][0]) for idx, song in enumerate(song_slots) if song is None]
    if missing:
        raise RuntimeError(f"Parsing did not produce songs for {len(missing)} files: {missing[:3]}")
    songs = [song for song in song_slots if song is not None]
    return songs, stats


def sequence_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def token_containment_score(a: str, b: str) -> float:
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a)


def line_match_score(line_a: str, line_b: str) -> float:
    if line_a == line_b:
        return 1.0
    seq = sequence_ratio(line_a, line_b)
    contain = token_containment_score(line_a, line_b)
    reverse_contain = token_containment_score(line_b, line_a)
    return max(seq, contain, reverse_contain * 0.95)


def line_coverage(
    lines_a: Sequence[str],
    lines_b: Sequence[str],
    line_match_threshold: float,
    score_cache: Optional[Dict[Tuple[str, str], float]] = None,
) -> float:
    if not lines_a or not lines_b:
        return 0.0
    exact_b = set(lines_b)
    matched = 0
    for line_a in lines_a:
        if line_a in exact_b:
            matched += 1
            continue
        best = 0.0
        for line_b in lines_b:
            if score_cache is not None:
                key = (line_a, line_b)
                score = score_cache.get(key)
                if score is None:
                    score = line_match_score(line_a, line_b)
                    score_cache[key] = score
            else:
                score = line_match_score(line_a, line_b)
            if score > best:
                best = score
            if best >= 1.0:
                break
        if best >= line_match_threshold:
            matched += 1
    return matched / len(lines_a)


def shared_exact_line_count(lines_a: Sequence[str], lines_b: Sequence[str]) -> int:
    return len(set(lines_a) & set(lines_b))


def exact_lyric_identity_score(a: Song, b: Song) -> Dict[str, Any]:
    same_title = bool(
        (a.normalized_title and a.normalized_title == b.normalized_title)
        or (a.compact_title and a.compact_title == b.compact_title)
    )
    return {
        "title_score": 1.0 if same_title else 0.0,
        "lyric_score": 1.0,
        "line_coverage_a_in_b": 1.0,
        "line_coverage_b_in_a": 1.0,
        "line_coverage_max": 1.0,
        "line_coverage_min": 1.0,
        "lyric_identity_score": 1.0,
        "final_score": 1.0,
        "shared_line_count": shared_exact_line_count(a.lyric_lines, b.lyric_lines),
        "decision_hint": "strong_lyric_identity",
        "score_method": "exact_normalized_lyrics",
    }


def score_pair_raw(a: Song, b: Song, line_match_threshold: float) -> Dict[str, Any]:
    if a.normalized_lyrics and a.normalized_lyrics == b.normalized_lyrics:
        return exact_lyric_identity_score(a, b)

    title_a = a.normalized_title
    title_b = b.normalized_title
    title_score = max(
        sequence_ratio(title_a, title_b),
        sequence_ratio(a.compact_title, b.compact_title),
    )
    lyric_score = sequence_ratio(a.normalized_lyrics, b.normalized_lyrics)
    line_score_cache: Dict[Tuple[str, str], float] = {}
    coverage_ab = line_coverage(a.lyric_lines, b.lyric_lines, line_match_threshold, line_score_cache)
    coverage_ba = line_coverage(b.lyric_lines, a.lyric_lines, line_match_threshold, line_score_cache)
    cov_max = max(coverage_ab, coverage_ba)
    cov_min = min(coverage_ab, coverage_ba)
    shared_lines = shared_exact_line_count(a.lyric_lines, b.lyric_lines)

    meaningful_count = min(len(a.lyric_lines), len(b.lyric_lines))
    if meaningful_count < 3:
        identity_score = (lyric_score * 0.65) + (cov_max * 0.20) + (title_score * 0.15)
    else:
        identity_score = (cov_max * 0.60) + (cov_min * 0.20) + (lyric_score * 0.15) + (title_score * 0.05)

    same_normalized_title = bool(title_a and title_a == title_b)
    if same_normalized_title and meaningful_count >= 4 and cov_max >= 0.88 and lyric_score >= 0.72:
        title_aligned_score = (cov_max * 0.70) + (lyric_score * 0.20) + (title_score * 0.10)
        identity_score = max(identity_score, title_aligned_score)

    final_score = max(identity_score, (lyric_score * 0.85) + (title_score * 0.15))

    if identity_score >= 0.86:
        hint = "strong_lyric_identity"
    elif same_normalized_title and identity_score < 0.65:
        hint = "same_title_different_lyrics"
    elif identity_score >= 0.65:
        hint = "possible_lyric_identity"
    else:
        hint = "weak_or_no_match"

    return {
        "title_score": title_score,
        "lyric_score": lyric_score,
        "line_coverage_a_in_b": coverage_ab,
        "line_coverage_b_in_a": coverage_ba,
        "line_coverage_max": cov_max,
        "line_coverage_min": cov_min,
        "lyric_identity_score": identity_score,
        "final_score": final_score,
        "shared_line_count": shared_lines,
        "decision_hint": hint,
    }


def matcher_settings_hash(args: argparse.Namespace) -> str:
    relevant = {
        "line_match_threshold": args.line_match_threshold,
        "matcher_version": MATCHER_VERSION,
        "normalization": "case_punct_spacing_chords_syllable_hyphen_v1",
    }
    raw = json.dumps(relevant, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def pair_score_from_row(a_idx: int, b_idx: int, row: Dict[str, Any], cache_hit: bool) -> PairScore:
    return PairScore(
        a=a_idx,
        b=b_idx,
        title_score=float(row["title_score"]),
        lyric_score=float(row["lyric_score"]),
        line_coverage_a_in_b=float(row["line_coverage_a_in_b"]),
        line_coverage_b_in_a=float(row["line_coverage_b_in_a"]),
        line_coverage_max=float(row["line_coverage_max"]),
        line_coverage_min=float(row["line_coverage_min"]),
        lyric_identity_score=float(row["lyric_identity_score"]),
        final_score=float(row["final_score"]),
        shared_line_count=int(row["shared_line_count"]),
        decision_hint=str(row["decision_hint"]),
        cache_hit=cache_hit,
    )


def score_pair(
    a: Song,
    b: Song,
    cache: CacheDB,
    settings_hash: str,
    line_match_threshold: float,
    force_rescore: bool,
) -> PairScore:
    cached = None if force_rescore else cache.get_pair_score(a.file_hash, b.file_hash, settings_hash)
    if cached:
        return pair_score_from_row(a.index, b.index, cached, cache_hit=True)

    raw = score_pair_raw(a, b, line_match_threshold)
    cache.save_pair_score(a.file_hash, b.file_hash, settings_hash, raw)
    return pair_score_from_row(a.index, b.index, {**raw, "decision_hint": raw["decision_hint"]}, cache_hit=False)


_SCORE_WORKER_SONGS: List[Song] = []
_SCORE_WORKER_LINE_MATCH_THRESHOLD = 0.82


def init_score_worker(songs: List[Song], line_match_threshold: float) -> None:
    global _SCORE_WORKER_SONGS, _SCORE_WORKER_LINE_MATCH_THRESHOLD
    _SCORE_WORKER_SONGS = songs
    _SCORE_WORKER_LINE_MATCH_THRESHOLD = line_match_threshold


def score_pair_worker(pair: Tuple[int, int]) -> Tuple[int, int, Dict[str, Any]]:
    a_idx, b_idx = pair
    raw = score_pair_raw(
        _SCORE_WORKER_SONGS[a_idx],
        _SCORE_WORKER_SONGS[b_idx],
        _SCORE_WORKER_LINE_MATCH_THRESHOLD,
    )
    return a_idx, b_idx, raw


def score_pair_batch_worker(pairs: List[Tuple[int, int]]) -> List[Tuple[int, int, Dict[str, Any]]]:
    return [score_pair_worker(pair) for pair in pairs]


def add_pair(pairs: Set[Tuple[int, int]], a: int, b: int) -> None:
    if a == b:
        return
    if a > b:
        a, b = b, a
    pairs.add((a, b))


def title_tokens(title: str) -> List[str]:
    stop = {"the", "a", "an", "of", "to", "and", "in", "on", "for", "my", "your"}
    return [t for t in normalize_title(title).split() if len(t) >= 3 and t not in stop]


def lyric_shingles(normalized_lyrics: str, size: int = 5) -> List[str]:
    tokens = [t for t in normalized_lyrics.split() if t]
    if len(tokens) < size:
        return []

    shingles: List[str] = []
    seen: Set[str] = set()
    for idx in range(0, len(tokens) - size + 1):
        shingle = " ".join(tokens[idx : idx + size])
        if shingle in seen:
            continue
        seen.add(shingle)
        shingles.append(shingle)
    return shingles


def generate_candidate_pairs(songs: List[Song], max_line_bucket: int, max_title_bucket: int) -> Set[Tuple[int, int]]:
    pairs: Set[Tuple[int, int]] = set()

    exact_title_index: Dict[str, List[int]] = {}
    compact_title_index: Dict[str, List[int]] = {}
    token_title_index: Dict[str, List[int]] = {}
    line_index: Dict[str, List[int]] = {}
    lyric_shingle_index: Dict[str, List[int]] = {}

    for song in songs:
        nt = song.normalized_title
        ct = song.compact_title
        if nt:
            exact_title_index.setdefault(nt, []).append(song.index)
        if ct:
            compact_title_index.setdefault(ct, []).append(song.index)
        for tok in song.title_tokens:
            token_title_index.setdefault(tok, []).append(song.index)
        for line in song.lyric_lines:
            if len(line) >= 10 and line not in COMMON_WEAK_LINES:
                line_index.setdefault(line, []).append(song.index)
        for shingle in song.lyric_shingles:
            lyric_shingle_index.setdefault(shingle, []).append(song.index)

    def pair_bucket(bucket: List[int], max_bucket: int) -> None:
        if len(bucket) < 2 or len(bucket) > max_bucket:
            return
        for i, a in enumerate(bucket):
            for b in bucket[i + 1 :]:
                add_pair(pairs, a, b)

    for bucket in exact_title_index.values():
        pair_bucket(bucket, max_title_bucket)
    for bucket in compact_title_index.values():
        pair_bucket(bucket, max_title_bucket)
    for bucket in token_title_index.values():
        pair_bucket(bucket, max_title_bucket)
    for bucket in line_index.values():
        pair_bucket(bucket, max_line_bucket)
    for bucket in lyric_shingle_index.values():
        pair_bucket(bucket, max_line_bucket)

    return pairs


def score_candidates(
    songs: List[Song],
    pairs: Set[Tuple[int, int]],
    cache: CacheDB,
    settings_hash: str,
    args: argparse.Namespace,
) -> Tuple[List[PairScore], Dict[str, int]]:
    score_workers = normalized_worker_count(getattr(args, "score_workers", 1))
    parallel_score_threshold = max(1, int(getattr(args, "parallel_score_threshold", 1000)))
    score_batch_size = max(1, int(getattr(args, "score_batch_size", 250)))
    cache_write_batch_size = max(1, int(getattr(args, "cache_write_batch_size", 500)))
    scores_by_pair: Dict[Tuple[int, int], PairScore] = {}
    pending: List[Tuple[int, int]] = []
    pending_cache_writes: List[Tuple[str, str, str, Dict[str, Any]]] = []
    stats = {
        "pairs": len(pairs),
        "score_cache_hits": 0,
        "scored_now": 0,
        "score_workers": score_workers,
        "parallel_score_threshold": parallel_score_threshold,
        "parallel_score_used": 0,
        "score_batch_size": score_batch_size,
        "score_batches_submitted": 0,
        "cache_write_batch_size": cache_write_batch_size,
        "score_cache_write_batches": 0,
        "exact_lyric_fast_path": 0,
    }
    total = len(pairs)
    start = time.time()
    sorted_pairs = sorted(pairs)
    done = 0

    def flush_score_cache_writes() -> None:
        if not pending_cache_writes:
            return
        cache.save_pair_scores_many(pending_cache_writes)
        pending_cache_writes.clear()
        stats["score_cache_write_batches"] += 1

    def report_progress() -> None:
        if not args.progress_every or not total:
            return
        if done != total and done % args.progress_every != 0:
            return
        flush_score_cache_writes()
        elapsed = time.time() - start
        eta = estimate_eta(elapsed, done, total)
        print(
            f"Scoring pairs: {done}/{total} ({done / total * 100:5.1f}%) "
            f"elapsed {format_duration(elapsed)} eta {eta} "
            f"| cached={stats['score_cache_hits']} scored={stats['scored_now']}"
        )
        cache.conn.commit()
        cache.set_state("score_progress", {"done": done, "total": total, "updated_at": now_iso()})

    for a_idx, b_idx in sorted_pairs:
        cached = None if args.force_rescore else cache.get_pair_score(
            songs[a_idx].file_hash,
            songs[b_idx].file_hash,
            settings_hash,
        )
        pair = tuple(sorted((a_idx, b_idx)))
        if cached:
            scores_by_pair[pair] = pair_score_from_row(a_idx, b_idx, cached, cache_hit=True)
            stats["score_cache_hits"] += 1
            done += 1
            report_progress()
        else:
            pending.append(pair)

    use_parallel = score_workers > 1 and len(pending) >= parallel_score_threshold

    def save_score_result(a_idx: int, b_idx: int, raw: Dict[str, Any]) -> None:
        nonlocal done
        pair = tuple(sorted((a_idx, b_idx)))
        if pair in scores_by_pair:
            return
        a = songs[a_idx]
        b = songs[b_idx]
        pending_cache_writes.append((a.file_hash, b.file_hash, settings_hash, raw))
        scores_by_pair[pair] = pair_score_from_row(a_idx, b_idx, raw, cache_hit=False)
        stats["scored_now"] += 1
        if raw.get("score_method") == "exact_normalized_lyrics":
            stats["exact_lyric_fast_path"] += 1
        if len(pending_cache_writes) >= cache_write_batch_size:
            flush_score_cache_writes()
        done += 1
        report_progress()

    if pending and use_parallel:
        stats["parallel_score_used"] = 1
        try:
            with ProcessPoolExecutor(
                max_workers=score_workers,
                initializer=init_score_worker,
                initargs=(songs, args.line_match_threshold),
            ) as executor:
                score_batches = batched(pending, score_batch_size)
                stats["score_batches_submitted"] = len(score_batches)
                futures = [executor.submit(score_pair_batch_worker, batch) for batch in score_batches]
                for future in as_completed(futures):
                    for a_idx, b_idx, raw in future.result():
                        save_score_result(a_idx, b_idx, raw)
        except Exception as exc:
            print(f"Warning: parallel scoring failed; retrying sequentially: {exc}", file=sys.stderr)
            stats["parallel_score_used"] = 0
            for a_idx, b_idx in pending:
                raw = score_pair_raw(songs[a_idx], songs[b_idx], args.line_match_threshold)
                save_score_result(a_idx, b_idx, raw)
    else:
        for a_idx, b_idx in pending:
            raw = score_pair_raw(songs[a_idx], songs[b_idx], args.line_match_threshold)
            save_score_result(a_idx, b_idx, raw)

    flush_score_cache_writes()
    cache.conn.commit()
    scores = [scores_by_pair[pair] for pair in sorted_pairs]
    return scores, stats


def build_groups(songs: List[Song], scores: List[PairScore], auto_identity_threshold: float) -> List[List[int]]:
    uf = UnionFind(len(songs))
    for score in scores:
        if score.lyric_identity_score >= auto_identity_threshold:
            uf.union(score.a, score.b)

    groups_by_root: Dict[int, List[int]] = {}
    for song in songs:
        groups_by_root.setdefault(uf.find(song.index), []).append(song.index)
    return [sorted(v) for v in groups_by_root.values()]


def pair_lookup(scores: List[PairScore]) -> Dict[Tuple[int, int], PairScore]:
    lookup = {}
    for score in scores:
        a, b = sorted((score.a, score.b))
        lookup[(a, b)] = score
    return lookup


def same_title_different_lyrics(scores: List[PairScore], songs: List[Song], review_threshold: float) -> Set[int]:
    flagged: Set[int] = set()
    for score in scores:
        a = songs[score.a]
        b = songs[score.b]
        if normalize_title(a.title) and normalize_title(a.title) == normalize_title(b.title):
            if score.lyric_identity_score < review_threshold:
                flagged.add(score.a)
                flagged.add(score.b)
    return flagged


def group_best_pair(group: List[int], lookup: Dict[Tuple[int, int], PairScore]) -> Optional[PairScore]:
    best: Optional[PairScore] = None
    for i, a in enumerate(group):
        for b in group[i + 1 :]:
            score = lookup.get(tuple(sorted((a, b))))
            if score and (best is None or score.lyric_identity_score > best.lyric_identity_score):
                best = score
    return best


def choose_canonical_song(group: List[int], songs: List[Song]) -> Tuple[Song, str]:
    members = [songs[i] for i in group]
    chorded = [s for s in members if s.has_chords]
    if chorded:
        chosen = sorted(
            chorded,
            key=lambda s: (
                s.chord_count,
                s.line_count,
                len(s.normalized_lyrics),
                1 if s.source_repo == TXT_REPO else 0,
            ),
            reverse=True,
        )[0]
        return chosen, "Best available chorded source"

    openlyrics = [s for s in members if s.source_repo == OPENLYRICS_REPO]
    if openlyrics:
        chosen = sorted(openlyrics, key=lambda s: (s.line_count, len(s.normalized_lyrics)), reverse=True)[0]
        return chosen, "Primary OpenLyrics lyrics source; no chords found"

    chosen = sorted(members, key=lambda s: (s.line_count, len(s.normalized_lyrics)), reverse=True)[0]
    return chosen, "Best available lyrics-only source"


def classify_group(
    group: List[int],
    songs: List[Song],
    lookup: Dict[Tuple[int, int], PairScore],
    title_lyric_conflict_ids: Set[int],
    clean_lyric_threshold: float,
    review_lyric_threshold: float,
) -> Tuple[str, str]:
    members = [songs[i] for i in group]
    best = group_best_pair(group, lookup)
    chorded_count = sum(1 for s in members if s.has_chords)
    has_openlyrics = any(s.source_repo == OPENLYRICS_REPO for s in members)
    title_norms = {normalize_title(s.title) for s in members if normalize_title(s.title)}
    has_title_diff = len(title_norms) > 1
    has_conflict = any(i in title_lyric_conflict_ids for i in group)

    if has_conflict and (not best or best.lyric_identity_score < clean_lyric_threshold):
        return "title_match_lyrics_different", "Same title exists with different lyrics; needs review"

    if len(group) == 1:
        s = members[0]
        if s.has_chords and s.source_repo != OPENLYRICS_REPO:
            return "unmatched_non_openlyrics", "Chorded source did not match an OpenLyrics source"
        if s.has_chords:
            return "single_source", "Single chorded source"
        return "lyrics_only", "Lyrics-only source; no matched chorded source"

    if chorded_count > 1:
        return "multiple_chorded_sources", "Multiple chorded sources matched; verify preferred arrangement"

    if not best:
        return "needs_review", "Grouped without a stored pair score"

    if best.lyric_identity_score >= clean_lyric_threshold:
        if has_title_diff:
            return "lyric_match_title_different", "Lyrics match strongly but titles differ"
        return "clean_match", "Strong lyric identity match"

    if best.lyric_identity_score >= review_lyric_threshold:
        return "needs_review", "Possible lyric identity match below clean threshold"

    if has_openlyrics and chorded_count == 0:
        return "lyrics_only", "OpenLyrics group with no chorded source"

    return "needs_review", "No confident classification"


def sanitize_filename(name: str) -> str:
    name = norm_space(name) or "Untitled"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name)
    name = name.strip(" ._")
    return name[:160] or "Untitled"


def unique_path(folder: Path, stem: str, suffix: str) -> Path:
    candidate = folder / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = folder / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def chordpro_escape(value: str) -> str:
    return (value or "").replace("\n", " ").strip()


def build_export_text(
    canonical: Song,
    group_id: str,
    canonical_reason: str,
    members: List[Song],
) -> str:
    lines: List[str] = []

    def add_meta(key: str, value: str) -> None:
        if value:
            lines.append(f"{{{key}: {chordpro_escape(value)}}}")

    add_meta("title", canonical.title)
    add_meta("artist", canonical.artist)
    add_meta("key", canonical.key)
    add_meta("tempo", canonical.tempo)
    add_meta("time", canonical.time_signature)
    add_meta("copyright", canonical.copyright)
    lines.append(f"{{meta: canonical_group_id {group_id}}}")
    lines.append(f"{{meta: canonical_reason {canonical_reason}}}")
    lines.append(f"{{meta: canonical_source_repo {canonical.source_repo}}}")
    lines.append(f"{{meta: canonical_source_file {canonical.source_path}}}")
    for member in members:
        lines.append(f"{{meta: member_source {member.source_repo} | {member.source_format} | {member.source_path}}}")
    lines.append("")

    body = canonical.chordpro_body.strip()
    if body:
        # Avoid duplicated title header when the source already has ChordPro metadata.
        filtered: List[str] = []
        for raw in body.splitlines():
            meta = METADATA_RE.match(raw.strip())
            if meta and meta.group(1).strip().lower() in {
                "title",
                "t",
                "artist",
                "subtitle",
                "st",
                "key",
                "tempo",
                "time",
                "time_signature",
                "copyright",
            }:
                continue
            filtered.append(raw)
        lines.extend(filtered)
    else:
        lines.extend(canonical.plain_lyrics.splitlines())

    return "\n".join(lines).rstrip() + "\n"


def clean_managed_output(out_dir: Path) -> None:
    for folder_name in list(CLASS_FOLDERS.values()) + ["reports"]:
        target = out_dir / folder_name
        if target.exists() and target.is_dir():
            shutil.rmtree(target)


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def export_results(
    songs: List[Song],
    groups: List[List[int]],
    scores: List[PairScore],
    args: argparse.Namespace,
    cache_stats: Dict[str, Any],
) -> Dict[str, Any]:
    export_start = time.time()
    out_dir = Path(args.out)
    reports_dir = out_dir / "reports"
    lookup = pair_lookup(scores)
    title_conflicts = same_title_different_lyrics(scores, songs, args.review_lyric_threshold)

    if args.clean_output and not args.dry_run:
        clean_managed_output(out_dir)

    for folder_name in CLASS_FOLDERS.values():
        if not args.dry_run:
            (out_dir / folder_name).mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    inventory_rows: List[Dict[str, Any]] = []
    for song in songs:
        inventory_rows.append(
            {
                "index": song.index,
                "title": song.title,
                "artist": song.artist,
                "source_repo": song.source_repo,
                "source_format": song.source_format,
                "source_path": song.source_path,
                "file_hash": song.file_hash,
                "has_chords": int(song.has_chords),
                "chord_count": song.chord_count,
                "line_count": song.line_count,
                "parse_status": song.parse_status,
                "parse_error": song.parse_error,
            }
        )

    group_rows: List[Dict[str, Any]] = []
    canonical_rows: List[Dict[str, Any]] = []
    exported_count = 0
    class_counts: Dict[str, int] = {name: 0 for name in CLASS_FOLDERS}

    for group_num, group in enumerate(sorted(groups, key=lambda g: min(g)), start=1):
        group_id = f"G{group_num:05d}"
        members = [songs[i] for i in group]
        canonical, canonical_reason = choose_canonical_song(group, songs)
        class_key, class_reason = classify_group(
            group,
            songs,
            lookup,
            title_conflicts,
            args.clean_lyric_threshold,
            args.review_lyric_threshold,
        )
        class_counts[class_key] += 1
        folder = out_dir / CLASS_FOLDERS[class_key]
        stem = sanitize_filename(canonical.title)
        export_path = folder / f"{stem}.chopro"
        if not args.dry_run:
            export_path = unique_path(folder, stem, ".chopro")
            export_text = build_export_text(canonical, group_id, canonical_reason, members)
            export_path.write_text(export_text, encoding="utf-8")
        exported_count += 1

        best = group_best_pair(group, lookup)
        group_rows.append(
            {
                "group_id": group_id,
                "classification": class_key,
                "classification_folder": CLASS_FOLDERS[class_key],
                "classification_reason": class_reason,
                "member_count": len(group),
                "canonical_title": canonical.title,
                "canonical_source_repo": canonical.source_repo,
                "canonical_source_file": canonical.source_path,
                "best_lyric_identity_score": round(best.lyric_identity_score, 6) if best else "",
                "best_lyric_score": round(best.lyric_score, 6) if best else "",
                "best_line_coverage_max": round(best.line_coverage_max, 6) if best else "",
                "members": " || ".join(f"{m.source_repo}: {m.title} [{m.source_path}]" for m in members),
            }
        )
        canonical_rows.append(
            {
                "group_id": group_id,
                "classification": class_key,
                "export_path": str(export_path),
                "canonical_reason": canonical_reason,
                "title": canonical.title,
                "artist": canonical.artist,
                "source_repo": canonical.source_repo,
                "source_format": canonical.source_format,
                "source_path": canonical.source_path,
                "file_hash": canonical.file_hash,
                "has_chords": int(canonical.has_chords),
                "chord_count": canonical.chord_count,
                "line_count": canonical.line_count,
            }
        )

    pair_rows: List[Dict[str, Any]] = []
    conflict_rows: List[Dict[str, Any]] = []
    for score in scores:
        a = songs[score.a]
        b = songs[score.b]
        row = {
            "a_index": score.a,
            "b_index": score.b,
            "a_title": a.title,
            "b_title": b.title,
            "a_source": a.source_repo,
            "b_source": b.source_repo,
            "a_path": a.source_path,
            "b_path": b.source_path,
            "title_score": round(score.title_score, 6),
            "lyric_score": round(score.lyric_score, 6),
            "line_coverage_a_in_b": round(score.line_coverage_a_in_b, 6),
            "line_coverage_b_in_a": round(score.line_coverage_b_in_a, 6),
            "line_coverage_max": round(score.line_coverage_max, 6),
            "line_coverage_min": round(score.line_coverage_min, 6),
            "lyric_identity_score": round(score.lyric_identity_score, 6),
            "final_score": round(score.final_score, 6),
            "shared_line_count": score.shared_line_count,
            "decision_hint": score.decision_hint,
            "cache_hit": int(score.cache_hit),
        }
        pair_rows.append(row)
        if normalize_title(a.title) and normalize_title(a.title) == normalize_title(b.title):
            if score.lyric_identity_score < args.review_lyric_threshold:
                conflict_rows.append(row)

    write_csv(
        reports_dir / "01_inventory_all.csv",
        inventory_rows,
        [
            "index",
            "title",
            "artist",
            "source_repo",
            "source_format",
            "source_path",
            "file_hash",
            "has_chords",
            "chord_count",
            "line_count",
            "parse_status",
            "parse_error",
        ],
    )
    write_csv(
        reports_dir / "02_match_groups.csv",
        group_rows,
        [
            "group_id",
            "classification",
            "classification_folder",
            "classification_reason",
            "member_count",
            "canonical_title",
            "canonical_source_repo",
            "canonical_source_file",
            "best_lyric_identity_score",
            "best_lyric_score",
            "best_line_coverage_max",
            "members",
        ],
    )
    write_csv(
        reports_dir / "03_canonical_selection.csv",
        canonical_rows,
        [
            "group_id",
            "classification",
            "export_path",
            "canonical_reason",
            "title",
            "artist",
            "source_repo",
            "source_format",
            "source_path",
            "file_hash",
            "has_chords",
            "chord_count",
            "line_count",
        ],
    )
    write_csv(
        reports_dir / "07_group_pair_scores.csv",
        pair_rows,
        [
            "a_index",
            "b_index",
            "a_title",
            "b_title",
            "a_source",
            "b_source",
            "a_path",
            "b_path",
            "title_score",
            "lyric_score",
            "line_coverage_a_in_b",
            "line_coverage_b_in_a",
            "line_coverage_max",
            "line_coverage_min",
            "lyric_identity_score",
            "final_score",
            "shared_line_count",
            "decision_hint",
            "cache_hit",
        ],
    )
    write_csv(
        reports_dir / "08_same_title_different_lyrics.csv",
        conflict_rows,
        [
            "a_index",
            "b_index",
            "a_title",
            "b_title",
            "a_source",
            "b_source",
            "a_path",
            "b_path",
            "title_score",
            "lyric_score",
            "line_coverage_a_in_b",
            "line_coverage_b_in_a",
            "line_coverage_max",
            "line_coverage_min",
            "lyric_identity_score",
            "final_score",
            "shared_line_count",
            "decision_hint",
            "cache_hit",
        ],
    )

    phase_timings = dict(cache_stats.get("phase_timings", {}))
    phase_timings["export_seconds"] = round(time.time() - export_start, 3)
    if "pre_export_seconds" in phase_timings:
        phase_timings["total_seconds"] = round(
            float(phase_timings["pre_export_seconds"]) + phase_timings["export_seconds"],
            3,
        )

    summary = {
        "script_version": SCRIPT_VERSION,
        "parser_version": PARSER_VERSION,
        "matcher_version": MATCHER_VERSION,
        "dry_run": bool(args.dry_run),
        "source_count": len(songs),
        "group_count": len(groups),
        "exported_count": exported_count,
        "classification_counts": class_counts,
        "cache_stats": cache_stats,
        "phase_timings": phase_timings,
        "thresholds": {
            "auto_identity_threshold": args.auto_identity_threshold,
            "clean_lyric_threshold": args.clean_lyric_threshold,
            "review_lyric_threshold": args.review_lyric_threshold,
            "line_match_threshold": args.line_match_threshold,
        },
        "created_at": now_iso(),
    }
    (reports_dir / "run_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    return {
        "exported_count": exported_count,
        "group_count": len(groups),
        "classification_counts": class_counts,
        "title_lyric_conflicts": len(conflict_rows),
    }


def format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def estimate_eta(elapsed: float, done: int, total: int) -> str:
    if done <= 0 or total <= 0:
        return "?"
    remaining = elapsed / done * (total - done)
    return format_duration(remaining)


def batched(items: Sequence[Tuple[int, int]], batch_size: int) -> List[List[Tuple[int, int]]]:
    size = max(1, int(batch_size or 1))
    return [list(items[idx : idx + size]) for idx in range(0, len(items), size)]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a classified ChordPro repo using lyric-first matching and hash caching."
    )
    parser.add_argument("--openlyrics", type=Path, help="Folder containing OpenLyrics XML files.")
    parser.add_argument("--onsong", type=Path, help="Folder containing OnSong or ChordPro-like files.")
    parser.add_argument("--txt", type=Path, help="Folder containing TXT/ChordPro files.")
    parser.add_argument("--out", type=Path, required=True, help="Output folder.")
    parser.add_argument("--cache-dir", type=Path, help="Cache folder. Defaults to OUT/_cache.")
    parser.add_argument("--dry-run", action="store_true", help="Run parsing/matching and reports without exporting .chopro files.")
    parser.add_argument(
        "--clean-output",
        action="store_true",
        help="Delete managed output classification/report folders before exporting. Use only when OUT is dedicated to this script.",
    )
    parser.add_argument("--verify-hashes", action="store_true", help="Recalculate all file hashes even when size/mtime match.")
    parser.add_argument("--force-reparse", action="store_true", help="Ignore parsed-song cache and parse all files again.")
    parser.add_argument("--force-rescore", action="store_true", help="Ignore pair-score cache and rescore all candidate pairs.")
    parser.add_argument(
        "--auto-identity-threshold",
        type=float,
        default=0.86,
        help="Lyric identity score required to group songs automatically.",
    )
    parser.add_argument(
        "--clean-lyric-threshold",
        type=float,
        default=0.96,
        help="Lyric identity score required for clean match classification.",
    )
    parser.add_argument(
        "--review-lyric-threshold",
        type=float,
        default=0.65,
        help="Below this score, same-title pairs are treated as different lyrics.",
    )
    parser.add_argument(
        "--line-match-threshold",
        type=float,
        default=0.82,
        help="Per-line fuzzy match threshold used in lyric line coverage.",
    )
    parser.add_argument(
        "--max-line-bucket",
        type=int,
        default=250,
        help="Skip exact lyric-line buckets larger than this to avoid very common lines.",
    )
    parser.add_argument(
        "--max-title-bucket",
        type=int,
        default=500,
        help="Skip title/token buckets larger than this to avoid very broad comparisons.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print and save progress every N files/pairs. Use 0 to disable progress logging.",
    )
    parser.add_argument(
        "--parse-workers",
        type=int,
        default=default_worker_count(),
        help="Worker processes for uncached parsing. Use 1 to disable parse multiprocessing.",
    )
    parser.add_argument(
        "--score-workers",
        type=int,
        default=default_worker_count(),
        help="Worker processes for uncached pair scoring. Use 1 to disable score multiprocessing.",
    )
    parser.add_argument(
        "--parallel-parse-threshold",
        type=int,
        default=100,
        help="Minimum uncached files before parse multiprocessing is used.",
    )
    parser.add_argument(
        "--parallel-score-threshold",
        type=int,
        default=1000,
        help="Minimum uncached candidate pairs before score multiprocessing is used.",
    )
    parser.add_argument(
        "--score-batch-size",
        type=int,
        default=250,
        help="Candidate pairs per multiprocessing score task. Larger values reduce Windows process-pool overhead.",
    )
    parser.add_argument(
        "--cache-write-batch-size",
        type=int,
        default=500,
        help="Pair-score cache rows to buffer before each SQLite batch write.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    start = time.time()
    phase_timings: Dict[str, float] = {}
    out_dir = Path(args.out)
    cache_dir = Path(args.cache_dir) if args.cache_dir else out_dir / "_cache"
    cache = CacheDB(cache_dir / "song_repo_cache.sqlite")
    run_id = time.strftime("%Y%m%d_%H%M%S")

    try:
        phase_start = time.time()
        files = discover_files(args.openlyrics, args.onsong, args.txt)
        phase_timings["discovery_seconds"] = round(time.time() - phase_start, 3)
        if not files:
            print("No source files found. Check --openlyrics, --onsong, and --txt paths.", file=sys.stderr)
            return 2

        print(f"Discovered {len(files)} source files in {format_duration(phase_timings['discovery_seconds'])}.")
        phase_start = time.time()
        songs, parse_stats = parse_sources(
            files,
            cache,
            run_id,
            args.verify_hashes,
            args.force_reparse,
            args.progress_every,
            args.parse_workers,
            args.parallel_parse_threshold,
        )
        phase_timings["parse_seconds"] = round(time.time() - phase_start, 3)
        print(
            f"Parsed {len(songs)} songs in {format_duration(phase_timings['parse_seconds'])} "
            f"| parsed_now={parse_stats['parsed_now']} cached={parse_stats['parse_cache_hits']} "
            f"| parallel={parse_stats['parallel_parse_used']} workers={parse_stats['parse_workers']}"
        )

        print("Building candidate pairs...")
        phase_start = time.time()
        pairs = generate_candidate_pairs(songs, args.max_line_bucket, args.max_title_bucket)
        phase_timings["candidate_seconds"] = round(time.time() - phase_start, 3)
        print(f"Candidate pairs: {len(pairs)} built in {format_duration(phase_timings['candidate_seconds'])}")

        settings_hash = matcher_settings_hash(args)
        phase_start = time.time()
        scores, score_stats = score_candidates(songs, pairs, cache, settings_hash, args)
        phase_timings["score_seconds"] = round(time.time() - phase_start, 3)
        print(
            f"Scored {len(scores)} pairs in {format_duration(phase_timings['score_seconds'])} "
            f"| scored_now={score_stats['scored_now']} cached={score_stats['score_cache_hits']} "
            f"| exact_fast={score_stats['exact_lyric_fast_path']} "
            f"| parallel={score_stats['parallel_score_used']} workers={score_stats['score_workers']} "
            f"| batches={score_stats['score_batches_submitted']}"
        )

        print("Building final groups...")
        phase_start = time.time()
        groups = build_groups(songs, scores, args.auto_identity_threshold)
        phase_timings["group_seconds"] = round(time.time() - phase_start, 3)
        print(f"Built {len(groups)} groups in {format_duration(phase_timings['group_seconds'])}.")

        cache_stats = {
            "parse": parse_stats,
            "score": score_stats,
            "cache_db": str(cache.db_path),
            "matcher_settings_hash": settings_hash,
            "phase_timings": phase_timings,
        }
        phase_timings["pre_export_seconds"] = round(time.time() - start, 3)
        export_summary = export_results(songs, groups, scores, args, cache_stats)
        phase_timings["total_seconds"] = round(time.time() - start, 3)
        cache.set_state(
            "last_run_summary",
            {
                "run_id": run_id,
                "dry_run": bool(args.dry_run),
                "source_count": len(songs),
                "candidate_pairs": len(pairs),
                "group_count": export_summary["group_count"],
                "exported_count": export_summary["exported_count"],
                "classification_counts": export_summary["classification_counts"],
                "elapsed_seconds": phase_timings["total_seconds"],
                "phase_timings": phase_timings,
                "updated_at": now_iso(),
            },
        )

        print("")
        print("Done.")
        print(f"Sources: {len(songs)}")
        print(f"Candidate pairs: {len(pairs)}")
        print(f"Groups/exported canonical files: {export_summary['exported_count']}")
        print(f"Same-title different-lyrics conflicts: {export_summary['title_lyric_conflicts']}")
        print(f"Elapsed: {format_duration(time.time() - start)}")
        if args.dry_run:
            print("Dry run only: reports were written, but final .chopro files were not exported.")
        else:
            print(f"Output: {out_dir}")
            print(f"Cache: {cache.db_path}")
        return 0
    finally:
        cache.close()


if __name__ == "__main__":
    raise SystemExit(main())
