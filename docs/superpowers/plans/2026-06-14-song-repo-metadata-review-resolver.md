# Song Repo Metadata Review Resolver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve author/source metadata, clean OnSong header parsing, improve review-folder filenames, and add a post-processing GUI resolver that records and applies manual review decisions.

**Architecture:** Keep parsing/matching/building inside `song_repo_builder.py`. Add `song_repo_review.py` for post-processing review data loading, decision persistence, SQLite audit updates, and file moves. Add `song_repo_review_gui.py` for the Tkinter resolver window, and wire it into the existing dashboard with a `Review issues` button.

**Tech Stack:** Python standard library, `unittest`, `sqlite3`, `csv`, `json`, `shutil`, `tkinter`/`ttk`.

---

## File Structure

- Modify `song_repo_builder.py`: add `author` and `source_meta`, parse bare OnSong headers, export richer metadata, add review-folder filename helper, and include new report columns.
- Modify `tests/test_song_repo_builder.py`: add failing tests for OpenLyrics author, OnSong bare metadata, export metadata, and review filename stems.
- Create `song_repo_review.py`: pure helper module for loading review candidates, saving manual decisions, updating the optional cache database, and moving exported `.chopro` files.
- Create `tests/test_song_repo_review.py`: unit tests for post-processing without visible GUI windows.
- Create `song_repo_review_gui.py`: Tkinter review resolver window that calls `song_repo_review.py`.
- Modify `song_repo_gui.py`: add a `Review issues` button that opens the resolver for the selected output folder.
- Modify `tests/test_song_repo_gui.py`: verify the dashboard helper opens the resolver with the selected output folder.

---

### Task 1: Builder Metadata Tests

**Files:**
- Modify: `tests/test_song_repo_builder.py`

- [ ] **Step 1: Write failing OpenLyrics author and artist tests**

Add these tests to `OpenLyricsBuilderParsingTests`:

```python
    def test_openlyrics_author_exports_as_author_not_artist(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties>
    <titles><title>Author Song</title></titles>
    <authors><author>Jane Writer</author></authors>
  </properties>
  <lyrics><verse name="v1"><lines>Useful lyric line</lines></verse></lyrics>
</song>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "author.xml"
            path.write_text(xml, encoding="utf-8")

            parsed = builder.parse_openlyrics(path)
            song = builder.song_from_parsed(
                0,
                path,
                builder.OPENLYRICS_REPO,
                "openlyrics",
                "hash-author",
                path.stat().st_size,
                path.stat().st_mtime_ns,
                parsed,
            )
            exported = builder.build_export_text(song, "G00001", "test", [song])

        self.assertEqual(parsed["author"], "Jane Writer")
        self.assertEqual(parsed["artist"], "")
        self.assertIn("{author: Jane Writer}", exported)
        self.assertNotIn("{artist: Jane Writer}", exported)

    def test_openlyrics_artist_remains_artist(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties>
    <titles><title>Artist Song</title></titles>
    <artists><artist>Band Name</artist></artists>
  </properties>
  <lyrics><verse name="v1"><lines>Useful lyric line</lines></verse></lyrics>
</song>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "artist.xml"
            path.write_text(xml, encoding="utf-8")

            parsed = builder.parse_openlyrics(path)

        self.assertEqual(parsed["artist"], "Band Name")
        self.assertEqual(parsed["author"], "")
```

- [ ] **Step 2: Write failing OnSong bare metadata test**

Add this test class after `OpenLyricsBuilderParsingTests`:

```python
class PlainMetadataParsingTests(unittest.TestCase):
    def test_onsong_bare_headers_become_metadata_not_lyrics(self):
        text = """Title: Celebrate Jesus
Artist: Gary Oliver
Key: [F]
Original Key: F
Book: Camp 2022, Camp Fast, Fast
Notes: Use fast tempo
Scripture Reference(s): Psalm 100

Chorus:
Celebrate [F]Jesus celebrate
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "Celebrate Jesus.onsong"
            path.write_text(text, encoding="utf-8")

            parsed = builder.parse_plain_chordpro(path)

        self.assertEqual(parsed["title"], "Celebrate Jesus")
        self.assertEqual(parsed["artist"], "Gary Oliver")
        self.assertEqual(parsed["key"], "F")
        self.assertEqual(parsed["source_meta"]["original_key"], "F")
        self.assertEqual(parsed["source_meta"]["book"], "Camp 2022, Camp Fast, Fast")
        self.assertEqual(parsed["source_meta"]["notes"], "Use fast tempo")
        self.assertEqual(parsed["source_meta"]["scripture_references"], "Psalm 100")
        self.assertEqual(parsed["plain_lyrics"].splitlines(), ["Celebrate Jesus celebrate"])
        self.assertNotIn("Title:", parsed["chordpro_body"])
        self.assertNotIn("Artist:", parsed["chordpro_body"])
        self.assertIn("{comment: Chorus}", parsed["chordpro_body"])
```

- [ ] **Step 3: Write failing export metadata test**

Add this test to `PlainMetadataParsingTests`:

```python
    def test_export_includes_source_meta_without_bare_header_lines(self):
        song = builder.Song(
            index=0,
            source_repo=builder.ONSONG_REPO,
            source_format="onsong",
            source_path="Celebrate Jesus.onsong",
            file_hash="hash-celebrate",
            size=0,
            mtime_ns=0,
            title="Celebrate Jesus",
            artist="Gary Oliver",
            author="",
            key="F",
            plain_lyrics="Celebrate Jesus celebrate",
            normalized_lyrics=builder.normalize_lyric_text("Celebrate Jesus celebrate"),
            lyric_lines=["celebrate jesus celebrate"],
            chordpro_body="Title: Celebrate Jesus\nArtist: Gary Oliver\nCelebrate [F]Jesus celebrate",
            source_meta={"book": "Camp 2022", "original_key": "F"},
        )

        exported = builder.build_export_text(song, "G00001", "test", [song])

        self.assertIn("{title: Celebrate Jesus}", exported)
        self.assertIn("{artist: Gary Oliver}", exported)
        self.assertIn("{key: F}", exported)
        self.assertIn("{meta: book Camp 2022}", exported)
        self.assertIn("{meta: original_key F}", exported)
        self.assertNotIn("Title: Celebrate Jesus", exported)
        self.assertNotIn("Artist: Gary Oliver", exported)
```

- [ ] **Step 4: Run tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_builder -v
```

Expected: FAIL with errors mentioning missing `author` or `source_meta` keys/arguments.

---

### Task 2: Builder Metadata Implementation

**Files:**
- Modify: `song_repo_builder.py`

- [ ] **Step 1: Add parser version and metadata constants**

Change the parser version and add bare metadata constants near the existing regex constants:

```python
PARSER_VERSION = "parser.2026.06.14.1"
```

```python
BARE_METADATA_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 ()/_-]{0,40})\s*:\s*(.*?)\s*$")

DIRECT_METADATA_FIELDS = {
    "title": "title",
    "t": "title",
    "artist": "artist",
    "subtitle": "artist",
    "st": "artist",
    "author": "author",
    "key": "key",
    "tempo": "tempo",
    "time": "time_signature",
    "time signature": "time_signature",
    "time_signature": "time_signature",
    "copyright": "copyright",
}

SOURCE_METADATA_FIELDS = {
    "original key": "original_key",
    "book": "book",
    "notes": "notes",
    "scripture reference(s)": "scripture_references",
    "scripture references": "scripture_references",
    "scripture reference": "scripture_references",
}
```

- [ ] **Step 2: Add `author` and `source_meta` to `Song`**

Update the dataclass fields:

```python
    title: str = ""
    artist: str = ""
    author: str = ""
    key: str = ""
    tempo: str = ""
    time_signature: str = ""
    copyright: str = ""
    source_meta: Dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 3: Add cache migration helper**

Add this method inside `CacheDB`:

```python
    def ensure_column(self, table: str, column: str, definition: str) -> None:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row["name"]) for row in rows}
        if column not in existing:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
```

After the `CREATE TABLE IF NOT EXISTS parsed_songs` statement in `init_schema()`, call:

```python
        self.ensure_column("parsed_songs", "author", "TEXT")
        self.ensure_column("parsed_songs", "source_meta_json", "TEXT NOT NULL DEFAULT '{}'")
```

- [ ] **Step 4: Persist author and source metadata in parsed cache**

Update the `INSERT INTO parsed_songs` field list, values list, conflict update, and argument tuple to include `author` and `source_meta_json`.

The selected SQL field section should become:

```sql
                file_hash, parser_version, title, artist, author, key, tempo, time_signature, copyright,
                has_chords, chord_count, line_count, plain_lyrics, normalized_lyrics,
                lyric_lines_json, chordpro_body, source_meta_json, parse_status, parse_error, created_at
```

The matching Python arguments should include:

```python
                data.get("artist", ""),
                data.get("author", ""),
                data.get("key", ""),
```

and:

```python
                data.get("chordpro_body", ""),
                json.dumps(data.get("source_meta", {}), ensure_ascii=False),
                data.get("parse_status", "ok"),
```

In `get_parsed_song()`, add:

```python
        data["source_meta"] = json.loads(data.pop("source_meta_json") or "{}")
```

- [ ] **Step 5: Add metadata helper functions**

Add these helpers near `title_from_filename()`:

```python
def source_meta_key(name: str) -> str:
    key = normalize_title(name).replace(" ", "_")
    if key == "scripture_reference_s":
        return "scripture_references"
    return key


def clean_metadata_value(field: str, value: str) -> str:
    value = norm_space(value)
    if field == "key":
        chord_match = re.fullmatch(r"\[([^\[\]]+)\]", value)
        if chord_match:
            return norm_space(chord_match.group(1))
    return value


def parse_bare_metadata_line(line: str) -> Optional[Tuple[str, str, str]]:
    match = BARE_METADATA_RE.match(line)
    if not match:
        return None
    raw_name = norm_space(match.group(1)).lower()
    value = norm_space(match.group(2))
    if raw_name in DIRECT_METADATA_FIELDS:
        field = DIRECT_METADATA_FIELDS[raw_name]
        return ("direct", field, clean_metadata_value(field, value))
    if raw_name in SOURCE_METADATA_FIELDS:
        return ("source", SOURCE_METADATA_FIELDS[raw_name], value)
    return None


def colon_section_label(line: str) -> Optional[str]:
    stripped = line.strip()
    match = re.fullmatch(r"([A-Za-z][A-Za-z ]*(?:\s+\d+)?)\s*:", stripped)
    if not match:
        return None
    label = norm_space(match.group(1))
    label_base = re.sub(r"\d+", "", label.lower())
    label_base = norm_space(label_base.replace("-", " "))
    if label.lower() in SECTION_WORDS or label_base in SECTION_WORDS:
        return label
    return None
```

- [ ] **Step 6: Update `parse_openlyrics()`**

Replace the current artist assignment with:

```python
    author = first_xml_text(root, ("author",)) or first_xml_text(root, ("composer", "writer"))
    artist = first_xml_text(root, ("artist",))
```

Return both fields and an empty source metadata dict:

```python
        "artist": artist,
        "author": author,
        "key": key,
```

and:

```python
        "source_meta": {},
```

- [ ] **Step 7: Update `parse_plain_chordpro()` metadata state**

Add local variables:

```python
    author = ""
    source_meta: Dict[str, str] = {}
    header_mode = True
```

In the brace metadata branch, set fields through `DIRECT_METADATA_FIELDS`:

```python
            mapped = DIRECT_METADATA_FIELDS.get(name)
            if mapped == "title":
                title = value
            elif mapped == "artist":
                artist = value
            elif mapped == "author":
                author = value
            elif mapped == "key":
                key = clean_metadata_value("key", value)
            elif mapped == "tempo":
                tempo = value
            elif mapped == "time_signature":
                time_signature = value
            elif mapped == "copyright":
                copyright_text = value
```

Before comment/section/body processing, add bare metadata handling:

```python
        if header_mode:
            bare_meta = parse_bare_metadata_line(stripped)
            if bare_meta:
                kind, name, value = bare_meta
                if kind == "direct":
                    if name == "title":
                        title = value
                    elif name == "artist":
                        artist = value
                    elif name == "author":
                        author = value
                    elif name == "key":
                        key = value
                    elif name == "tempo":
                        tempo = value
                    elif name == "time_signature":
                        time_signature = value
                    elif name == "copyright":
                        copyright_text = value
                else:
                    source_meta[name] = value
                continue
            if stripped:
                header_mode = False
```

After `bracket_section_label`, handle colon sections:

```python
        colon_section = colon_section_label(stripped)
        if colon_section:
            body_lines.append(f"{{comment: {colon_section}}}")
            continue
```

Return:

```python
        "artist": artist,
        "author": author,
        "key": key,
```

and:

```python
        "source_meta": source_meta,
```

- [ ] **Step 8: Update `song_from_parsed()`**

Add:

```python
        author=parsed.get("author", "") or "",
        source_meta=dict(parsed.get("source_meta") or {}),
```

- [ ] **Step 9: Update export metadata filtering**

In `build_export_text()`, add:

```python
    add_meta("author", canonical.author)
```

after artist, and before canonical group metadata add:

```python
    for key, value in sorted(canonical.source_meta.items()):
        if value:
            lines.append(f"{{meta: {source_meta_key(key)} {chordpro_escape(value)}}}")
```

In the body filtering metadata name set, add `"author"`. Also skip stale bare metadata lines:

```python
            if parse_bare_metadata_line(raw.strip()):
                continue
```

- [ ] **Step 10: Update report rows**

Add `author` and `source_meta` to inventory and canonical rows:

```python
                "author": song.author,
                "source_meta": json.dumps(song.source_meta, ensure_ascii=False),
```

and:

```python
                "author": canonical.author,
                "source_meta": json.dumps(canonical.source_meta, ensure_ascii=False),
```

Add the new fieldnames after `artist` in both report writers:

```python
            "artist",
            "author",
```

and add `"source_meta"` before `has_chords`.

- [ ] **Step 11: Run tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_builder -v
```

Expected: PASS.

- [ ] **Step 12: Commit builder metadata changes**

Run:

```powershell
git add song_repo_builder.py tests/test_song_repo_builder.py
git commit -m "fix: preserve song source metadata"
```

Expected: commit succeeds.

---

### Task 3: Review Folder Filename Tests And Implementation

**Files:**
- Modify: `tests/test_song_repo_builder.py`
- Modify: `song_repo_builder.py`

- [ ] **Step 1: Write failing filename helper tests**

Add this class to `tests/test_song_repo_builder.py`:

```python
class ExportFilenameTests(unittest.TestCase):
    def test_review_folders_use_title_and_artist_stem(self):
        song = builder.Song(
            index=0,
            source_repo=builder.ONSONG_REPO,
            source_format="onsong",
            source_path="source.onsong",
            file_hash="hash",
            size=0,
            mtime_ns=0,
            title="Celebrate Jesus",
            artist="Gary Oliver",
        )

        stem = builder.export_stem_for_classification(song, "title_match_lyrics_different")

        self.assertEqual(stem, "Celebrate Jesus - Gary Oliver")

    def test_review_folders_fallback_to_author(self):
        song = builder.Song(
            index=0,
            source_repo=builder.OPENLYRICS_REPO,
            source_format="openlyrics",
            source_path="source.xml",
            file_hash="hash",
            size=0,
            mtime_ns=0,
            title="In The Garden",
            author="C. Austin Miles",
        )

        stem = builder.export_stem_for_classification(song, "multiple_chorded_sources")

        self.assertEqual(stem, "In The Garden - C. Austin Miles")

    def test_non_review_folders_keep_title_only_stem(self):
        song = builder.Song(
            index=0,
            source_repo=builder.ONSONG_REPO,
            source_format="onsong",
            source_path="source.onsong",
            file_hash="hash",
            size=0,
            mtime_ns=0,
            title="Clean Title",
            artist="Artist Name",
        )

        stem = builder.export_stem_for_classification(song, "clean_match")

        self.assertEqual(stem, "Clean Title")
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_builder.ExportFilenameTests -v
```

Expected: FAIL because `export_stem_for_classification` does not exist.

- [ ] **Step 3: Implement filename helper**

Add near `unique_path()`:

```python
CREATOR_FILENAME_CLASS_KEYS = {"title_match_lyrics_different", "multiple_chorded_sources"}


def export_stem_for_classification(canonical: Song, class_key: str) -> str:
    title = sanitize_filename(canonical.title)
    if class_key not in CREATOR_FILENAME_CLASS_KEYS:
        return title
    creator = (
        canonical.artist
        or canonical.author
        or Path(canonical.source_path).stem
        or canonical.source_repo
    )
    creator_stem = sanitize_filename(creator)
    if normalize_title(title) == normalize_title(creator_stem):
        return title
    return sanitize_filename(f"{title} - {creator_stem}")
```

In `export_results()`, replace:

```python
        stem = sanitize_filename(canonical.title)
```

with:

```python
        stem = export_stem_for_classification(canonical, class_key)
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_builder.ExportFilenameTests -v
```

Expected: PASS.

- [ ] **Step 5: Run full builder test module**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_builder -v
```

Expected: PASS.

- [ ] **Step 6: Commit filename changes**

Run:

```powershell
git add song_repo_builder.py tests/test_song_repo_builder.py
git commit -m "fix: clarify review export filenames"
```

Expected: commit succeeds.

---

### Task 4: Post-Processing Resolver Core

**Files:**
- Create: `song_repo_review.py`
- Create: `tests/test_song_repo_review.py`

- [ ] **Step 1: Write failing review core tests**

Create `tests/test_song_repo_review.py` with:

```python
import csv
import json
import pathlib
import sqlite3
import tempfile
import unittest

import song_repo_builder as builder
import song_repo_review as review


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class ReviewCoreTests(unittest.TestCase):
    def make_output(self, temp_dir):
        out_dir = pathlib.Path(temp_dir)
        review_dir = out_dir / builder.CLASS_FOLDERS["needs_review"]
        review_dir.mkdir(parents=True)
        export_path = review_dir / "Review Song.chopro"
        export_path.write_text("{title: Review Song}\nReview lyric\n", encoding="utf-8")
        source_path = out_dir / "source.onsong"
        source_path.write_text("Title: Review Song\nArtist: Artist Name\nReview lyric\n", encoding="utf-8")
        reports = out_dir / "reports"
        write_csv(
            reports / "03_canonical_selection.csv",
            [
                {
                    "group_id": "G00001",
                    "classification": "needs_review",
                    "export_path": str(export_path),
                    "canonical_reason": "Possible lyric identity match below clean threshold",
                    "title": "Review Song",
                    "artist": "Artist Name",
                    "author": "",
                    "source_repo": builder.ONSONG_REPO,
                    "source_format": "onsong",
                    "source_path": str(source_path),
                    "file_hash": "hash-review",
                    "source_meta": "{}",
                    "has_chords": "1",
                    "chord_count": "1",
                    "line_count": "1",
                }
            ],
            [
                "group_id",
                "classification",
                "export_path",
                "canonical_reason",
                "title",
                "artist",
                "author",
                "source_repo",
                "source_format",
                "source_path",
                "file_hash",
                "source_meta",
                "has_chords",
                "chord_count",
                "line_count",
            ],
        )
        write_csv(
            reports / "02_match_groups.csv",
            [
                {
                    "group_id": "G00001",
                    "classification": "needs_review",
                    "classification_folder": builder.CLASS_FOLDERS["needs_review"],
                    "classification_reason": "Possible lyric identity match below clean threshold",
                    "member_count": "2",
                    "canonical_title": "Review Song",
                    "canonical_source_repo": builder.ONSONG_REPO,
                    "canonical_source_file": str(out_dir / "source.onsong"),
                    "best_lyric_identity_score": "0.72",
                    "best_lyric_score": "0.70",
                    "best_line_coverage_max": "0.75",
                    "members": "02_OnSong: Review Song [source.onsong]",
                }
            ],
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
            reports / "07_group_pair_scores.csv",
            [
                {
                    "a_index": "0",
                    "b_index": "1",
                    "a_title": "Review Song",
                    "b_title": "Review Song",
                    "a_source": builder.ONSONG_REPO,
                    "b_source": builder.OPENLYRICS_REPO,
                    "a_path": str(source_path),
                    "b_path": str(out_dir / "source.xml"),
                    "title_score": "1.0",
                    "lyric_score": "0.72",
                    "line_coverage_a_in_b": "0.75",
                    "line_coverage_b_in_a": "0.70",
                    "line_coverage_max": "0.75",
                    "line_coverage_min": "0.70",
                    "lyric_identity_score": "0.72",
                    "final_score": "0.72",
                    "shared_line_count": "4",
                    "decision_hint": "review",
                    "cache_hit": "0",
                }
            ],
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
        return out_dir, export_path

    def test_load_review_candidates_reads_reports_and_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir, export_path = self.make_output(temp_dir)

            candidates = review.load_review_candidates(out_dir)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].group_id, "G00001")
        self.assertEqual(candidates[0].classification, "needs_review")
        self.assertEqual(candidates[0].title, "Review Song")
        self.assertEqual(candidates[0].artist, "Artist Name")
        self.assertEqual(candidates[0].export_path, export_path)
        self.assertIn("Review lyric", candidates[0].export_text)
        self.assertIn("Title: Review Song", candidates[0].source_text)
        self.assertEqual(len(candidates[0].pair_details), 1)

    def test_apply_review_decision_moves_file_and_saves_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir, export_path = self.make_output(temp_dir)
            candidate = review.load_review_candidates(out_dir)[0]

            decision = review.apply_review_decision(
                out_dir,
                candidate,
                chosen_classification="clean_match",
                action="move_to_clean_match",
                note="verified manually",
            )

            self.assertFalse(export_path.exists())
            self.assertTrue(decision.final_export_path.exists())
            self.assertEqual(decision.chosen_classification, "clean_match")
            self.assertIn(builder.CLASS_FOLDERS["clean_match"], str(decision.final_export_path))
            data = json.loads((out_dir / "reports" / "manual_review_decisions.json").read_text(encoding="utf-8"))
            self.assertEqual(data["decisions"][0]["group_id"], "G00001")
            self.assertEqual(data["decisions"][0]["note"], "verified manually")

    def test_apply_review_decision_updates_optional_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir, _export_path = self.make_output(temp_dir)
            db_path = out_dir / "_cache" / "song_repo_cache.sqlite"
            db_path.parent.mkdir()
            sqlite3.connect(db_path).close()
            candidate = review.load_review_candidates(out_dir)[0]

            decision = review.apply_review_decision(
                out_dir,
                candidate,
                chosen_classification="lyrics_only",
                action="move_to_lyrics_only",
                cache_db_path=db_path,
            )

            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT group_id, chosen_classification, final_export_path FROM manual_review_decisions"
                ).fetchone()
            self.assertEqual(row[0], "G00001")
            self.assertEqual(row[1], "lyrics_only")
            self.assertEqual(row[2], str(decision.final_export_path))

    def test_missing_reports_return_empty_candidate_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(review.load_review_candidates(pathlib.Path(temp_dir)), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_review -v
```

Expected: FAIL because `song_repo_review.py` does not exist.

- [ ] **Step 3: Implement `song_repo_review.py`**

Create `song_repo_review.py` with these public objects:

```python
#!/usr/bin/env python3
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
```

Implement helpers:

```python
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
```

Implement candidate loading:

```python
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
        group_title = row.get("title", "")
        pair_details = [
            item
            for item in pair_rows
            if item.get("a_title") == group_title or item.get("b_title") == group_title
        ]
        conflict_details = [
            item
            for item in conflict_rows
            if item.get("a_title") == group_title or item.get("b_title") == group_title
        ]
        decision = decisions.get(row.get("group_id", ""))
        status = "resolved" if decision else "unresolved"
        candidates.append(
            ReviewCandidate(
                group_id=row.get("group_id", ""),
                classification=class_key,
                export_path=export_path,
                title=row.get("title", ""),
                artist=row.get("artist", ""),
                author=row.get("author", ""),
                source_repo=row.get("source_repo", ""),
                source_format=row.get("source_format", ""),
                source_path=row.get("source_path", ""),
                file_hash=row.get("file_hash", ""),
                canonical_reason=row.get("canonical_reason", ""),
                members=group.get("members", ""),
                best_lyric_identity_score=group.get("best_lyric_identity_score", ""),
                pair_details=pair_details,
                conflict_details=conflict_details,
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
                    pair_details=[],
                    conflict_details=[],
                    export_text=read_text(export_path),
                    source_text="",
                )
            )
    return candidates
```

Implement decision serialization and movement:

```python
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
    try:
        with sqlite3.connect(cache_db_path) as conn:
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
        return True
    except sqlite3.Error:
        return False
```

Implement apply:

```python
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
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_review -v
```

Expected: PASS.

- [ ] **Step 5: Commit resolver core**

Run:

```powershell
git add song_repo_review.py tests/test_song_repo_review.py
git commit -m "feat: add manual review post-processing core"
```

Expected: commit succeeds.

---

### Task 5: Resolver GUI Window

**Files:**
- Create: `song_repo_review_gui.py`

- [ ] **Step 1: Create resolver window**

Create `song_repo_review_gui.py` with:

```python
#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

import tkinter as tk
from tkinter import messagebox, ttk

import song_repo_builder as builder
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
```

Add layout methods:

```python
    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.grid(row=0, column=0, sticky="nsew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(1, weight=1)

        toolbar = ttk.Frame(root)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
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
        panes.add(source_frame, weight=1)
        panes.add(export_frame, weight=1)

        ttk.Label(root, textvariable=self.status_var).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
```

Add behavior methods:

```python
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
```

Add action helpers:

```python
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


def open_review_resolver(parent: tk.Misc, output_dir: Path, cache_db_path: Optional[Path] = None) -> ReviewResolverWindow:
    return ReviewResolverWindow(parent, output_dir, cache_db_path=cache_db_path)
```

- [ ] **Step 2: Verify syntax**

Run:

```powershell
.\.venv\Scripts\python.exe -m py_compile song_repo_review_gui.py
```

Expected: PASS.

- [ ] **Step 3: Commit resolver GUI window**

Run:

```powershell
git add song_repo_review_gui.py
git commit -m "feat: add manual review resolver window"
```

Expected: commit succeeds.

---

### Task 6: Dashboard Integration

**Files:**
- Modify: `song_repo_gui.py`
- Modify: `tests/test_song_repo_gui.py`

- [ ] **Step 1: Write failing dashboard integration test**

Update the import in `tests/test_song_repo_gui.py`:

```python
import song_repo_gui
from song_repo_gui import DashboardSettings, build_builder_command, load_run_summary, performance_preset
```

Add:

```python
class ReviewResolverLaunchTests(unittest.TestCase):
    def test_review_resolver_uses_selected_output_and_cache_folder(self):
        calls = []
        original = song_repo_gui.open_review_resolver
        try:
            song_repo_gui.open_review_resolver = lambda parent, output, cache_db_path=None: calls.append(
                (parent, output, cache_db_path)
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                root = pathlib.Path(temp_dir)
                settings = DashboardSettings(
                    output=root / "out",
                    cache_dir=root / "cache",
                )

                song_repo_gui.open_review_resolver_for_settings(object(), settings)
        finally:
            song_repo_gui.open_review_resolver = original

        self.assertEqual(calls[0][1], root / "out")
        self.assertEqual(calls[0][2], root / "cache" / "song_repo_cache.sqlite")
```

- [ ] **Step 2: Run test to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_gui.ReviewResolverLaunchTests -v
```

Expected: FAIL because `open_review_resolver_for_settings` is missing.

- [ ] **Step 3: Add dashboard integration helper and button**

In `song_repo_gui.py`, add import:

```python
from song_repo_review_gui import open_review_resolver
```

Add helper near `load_run_summary()`:

```python
def review_cache_db_path(settings: DashboardSettings) -> Path:
    cache_dir = settings.cache_dir if settings.cache_dir else settings.output / "_cache"
    return cache_dir / "song_repo_cache.sqlite"


def open_review_resolver_for_settings(parent: tk.Misc, settings: DashboardSettings) -> None:
    open_review_resolver(parent, settings.output, cache_db_path=review_cache_db_path(settings))
```

In `_build_run_panel()`, change `controls.grid_columnconfigure(5, weight=1)` to:

```python
        controls.grid_columnconfigure(6, weight=1)
```

Add the button after `Open summary`:

```python
        ttk.Button(controls, text="Review issues", command=self._open_review_issues).grid(row=0, column=5, sticky="w", padx=(8, 0))
```

Add method:

```python
    def _open_review_issues(self) -> None:
        try:
            settings = self._collect_settings()
        except ValueError as exc:
            messagebox.showerror("Check settings", str(exc), parent=self)
            return
        settings.output.mkdir(parents=True, exist_ok=True)
        open_review_resolver_for_settings(self, settings)
```

- [ ] **Step 4: Run GUI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_gui -v
```

Expected: PASS.

- [ ] **Step 5: Commit dashboard integration**

Run:

```powershell
git add song_repo_gui.py tests/test_song_repo_gui.py
git commit -m "feat: launch review resolver from dashboard"
```

Expected: commit succeeds.

---

### Task 7: Regression Verification

**Files:**
- No new files unless verification exposes a defect in touched code.

- [ ] **Step 1: Run builder tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_builder -v
```

Expected: PASS.

- [ ] **Step 2: Run review tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_review -v
```

Expected: PASS.

- [ ] **Step 3: Run GUI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_gui -v
```

Expected: PASS.

- [ ] **Step 4: Run full test discovery**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -v
```

Expected: `tests.test_song_repo_builder`, `tests.test_song_repo_review`, and `tests.test_song_repo_gui` pass. If an unrelated legacy `tests.test_fwdb_to_pdf` expectation fails, record the exact failure and do not hide it.

- [ ] **Step 5: Run a sample builder smoke test**

Run:

```powershell
Remove-Item -Recurse -Force .\tmp_song_repo_smoke -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe song_repo_builder.py --openlyrics sample-songs --onsong sample-songs --out tmp_song_repo_smoke --clean-output --parse-workers 1 --score-workers 1 --force-reparse
```

Expected: command exits with code 0 and writes `tmp_song_repo_smoke\reports\run_summary.json`.

- [ ] **Step 6: Inspect sample OnSong export**

Run:

```powershell
Get-ChildItem -Recurse tmp_song_repo_smoke -Filter '*.chopro' | Select-String -Pattern 'Title:|Artist:|Book:|Original Key:' -SimpleMatch
```

Expected: no matches for bare metadata lines in exported `.chopro` body text.

- [ ] **Step 7: Final status**

Run:

```powershell
git status --short
```

Expected: only intentionally untracked pre-existing files remain, plus no unstaged modifications from this implementation.
