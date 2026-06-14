# Song Repository Metadata And Review Resolver Design

## Goal

Improve `song_repo_builder.py` and `song_repo_gui.py` so exported ChordPro repositories preserve source metadata more accurately, avoid noisy OnSong header lines in lyrics, use clearer filenames for ambiguous review folders, and provide a separate post-processing resolver for manual review items.

The manual resolver is explicitly post-processing. It does not change parser output, matcher scores, canonical grouping, or automated classification logic during the build. It records human decisions after a build and applies those decisions to exported files and review state.

## Current Behavior

OpenLyrics parsing reads the first matching creator-like XML tag from `author`, `artist`, `composer`, or `writer` and stores it in the single `artist` field. Export then writes `{artist: ...}` only. This means OpenLyrics `<author>` data can appear under the wrong ChordPro directive and no distinct author metadata survives.

Plain OnSong/ChordPro parsing recognizes brace directives such as `{title: ...}` and `{artist: ...}`. It does not recognize bare OnSong-style headers such as:

- `Title: Celebrate Jesus`
- `Artist: Gary Oliver`
- `Key: [F]`
- `Original Key: F`
- `Book: Camp 2022, Camp Fast, Fast`

Those lines currently fall through into `plain_lyrics` and `chordpro_body`, creating extra lyric mismatch and duplicate-looking exported metadata.

Export filenames currently use only the canonical title plus an auto-number suffix on collision. In folders such as `05_title_match_lyrics_different_chordpro` and `06_multiple_chorded_sources_chordpro`, this makes same-title review files harder to distinguish.

The dashboard GUI can run the builder and open output/report folders, but it cannot review or resolve `08_needs_review_chordpro` or related review folders.

## Metadata Requirements

Extend the parsed song data model with a separate `author` field while keeping `artist` for sources that provide artist metadata.

OpenLyrics parsing should:

- read `<author>` into `author`
- read `<artist>` into `artist`
- use `composer` or `writer` as fallback author-like data only when `author` is empty
- preserve existing behavior for title, key, copyright, chord counts, and lyrics

Plain OnSong/ChordPro parsing should recognize bare header lines near the top of the file. The parser should treat known `Name: value` headers as metadata until the song body begins. Known headers include:

- `Title`
- `Artist`
- `Author`
- `Key`
- `Original Key`
- `Book`
- `Tempo`
- `Time`
- `Time Signature`
- `Copyright`
- `Notes`
- `Scripture Reference(s)`

Recognized bare headers should not be added to `plain_lyrics` or `chordpro_body`. Metadata fields with direct ChordPro equivalents should be exported as ChordPro directives. Extra OnSong-specific fields such as `Book`, `Original Key`, `Notes`, and `Scripture Reference(s)` should be preserved as `{meta: ...}` lines rather than lyric text.

For `Key: [F]`, the parser should store `F`, not `[F]`.

## Export Requirements

`build_export_text()` should write top-level metadata in this order when present:

- `{title: ...}`
- `{artist: ...}`
- `{author: ...}`
- `{key: ...}`
- `{tempo: ...}`
- `{time: ...}`
- `{copyright: ...}`
- source-specific `{meta: ...}` values
- canonical group/source metadata

When filtering the canonical body before export, remove both brace-style metadata and bare OnSong-style metadata that the parser preserved only in legacy cached bodies. This prevents stale cache data from reintroducing `Title:` or `Artist:` lines into exported song bodies after the parser is fixed.

The parser version must be bumped so cached parsed rows are refreshed.

## Filename Requirements

Only `05_title_match_lyrics_different_chordpro` and `06_multiple_chorded_sources_chordpro` need the richer filename pattern:

```text
Song Title - ArtistOrAuthor.chopro
```

Use `artist` first, then `author`, then source repository or source filename if both creator fields are empty. Sanitize both title and creator using the existing filename rules.

If two files still resolve to the same filename, keep the existing numeric suffix behavior:

```text
Song Title - Artist.chopro
Song Title - Artist_2.chopro
```

Other classification folders can keep the current title-only naming.

## Post-Processing Review Resolver

Add a `Review issues` button near the existing output/report actions in the dashboard GUI. The resolver opens a separate Tkinter window and reads an already-built output folder.

The resolver should load review candidates from:

- `08_needs_review_chordpro`
- `05_title_match_lyrics_different_chordpro`
- `06_multiple_chorded_sources_chordpro`
- `reports/02_match_groups.csv`
- `reports/03_canonical_selection.csv`
- `reports/07_group_pair_scores.csv`
- `reports/08_same_title_different_lyrics.csv`

The left side should list review items with group id, classification, title, artist/author, best score where available, and current status. The right side should show selected item details:

- exported ChordPro text
- group members and source paths from the reports
- pair score details when available
- side-by-side source/member text where report and file paths allow loading it

The first implementation can use text panes and simple line-level diff markers. It does not need a perfect semantic diff.

## Resolver Actions

Supported manual actions:

- accept current export in its current folder
- move to clean match
- move to lyrics only
- move to title match lyrics different
- move to multiple chorded sources
- keep needs review
- skip without changing anything

Applying an action should:

- move the `.chopro` file to the selected classification folder when the folder changes
- use the same unique filename behavior as export
- append or update a decision record
- refresh the resolver list

Manual moves are post-processing and should not mutate automated score rows. A later builder run may regenerate the output from source data; manual decisions remain as audit records and are not reapplied automatically in this change.

## Decision Storage

Store decisions in:

```text
OUT/reports/manual_review_decisions.json
```

Each decision should include:

- decision id or timestamp
- group id
- original classification
- chosen classification
- original export path
- final export path
- selected action
- note field, initially optional/empty
- source file hash when available
- canonical source path
- created/updated timestamp

If the builder cache database exists, also create or update a separate `manual_review_decisions` table. This table is independent of parser and matcher caches. It should store the same essential fields so manual review history can be queried later without reading JSON.

The resolver should be resilient when the database is missing, locked, or unavailable. JSON decision storage is the source of truth for post-processing.

## GUI Design

Stay consistent with the current dashboard: native Tkinter/ttk, restrained colors, and dense operational layout.

The resolver window should have:

- top toolbar: output folder, refresh, open file, open final folder
- left issue list: sortable tree/table
- right details area: metadata summary, decision controls, text comparison
- bottom status line: last action or error

No new web server or frontend framework is needed.

## Tests

Add tests before implementation.

Required builder coverage:

- OpenLyrics `<author>` becomes `author` and exports as `{author: ...}`
- OpenLyrics `<artist>` remains `artist`
- bare OnSong headers are parsed as metadata and excluded from lyrics/body
- `Key: [F]` becomes `F`
- extra OnSong headers export as `{meta: ...}`
- review-folder filename stems include `Title - ArtistOrAuthor`

Required resolver coverage:

- loading review candidates from reports and output files
- saving a JSON manual decision
- optional SQLite decision table update when a cache database is supplied
- moving a file to the selected classification folder with collision-safe naming
- resolver helpers tolerate missing reports/database without crashing

GUI widget smoke tests can stay light. The important behavior should live in testable helper functions that do not require a visible Tk window.

## Non-Goals

- Do not rebuild the parser/matcher architecture.
- Do not change automated score calculations for manual decisions.
- Do not delete source files.
- Do not make manual decisions rewrite original OnSong, TXT, or OpenLyrics files.
- Do not require a database for post-processing to work.
- Do not build a web app for the resolver.
