# OpenLyrics Chord PDF Design

## Goal

Improve `FWDB_to_PDF.py` so FreeWorship/OpenLyrics XML files produce a cleaner songbook PDF with chords rendered above the lyric syllable they apply to, while preserving the existing table of contents and two-column song layout.

## Current Behavior

The script is a homemade OpenLyrics reader and ReportLab PDF generator. It extracts song metadata, normalizes some text, maps verse labels, and builds a PDF with a 3-column table of contents followed by 2-column song pages.

The current parser intentionally removes chords in `to_raw_without_chords()`. It walks mixed XML content, maps `<br/>` and `<line>` to newlines, but skips `<chord>` elements. That explains why chord data from `sample-songs/In The Garden.xml` never reaches the PDF.

There is also a syntax error in `ForceLeftColumn.wrap()` caused by a stray backtick after the method signature. This blocks compilation and must be fixed before tests or PDF generation can run.

## Chord Requirements

Support chords above lyrics, not inline chords.

The parser must handle:

- FreeWorship/OpenLyrics 0.8 style: `<chord name="G"/>`
- OpenLyrics root/structure/bass style: `<chord root="C" structure="min" bass="Eb"/>`
- Chords wrapping lyrics or syllables: `<chord root="E">san</chord>`
- Nested chord tags conservatively, preserving lyric text and drawing each chord at the current lyric position
- `<br/>` line breaks inside `<lines>`
- Lyric-only songs without changing visible output except for improved typography defaults

Chord display rules:

- `name` wins for old-style chord elements.
- Otherwise display `root` plus structure notation plus optional `/bass`.
- Use common structure mappings such as `min -> m`, `dom7 -> 7`, `maj7 -> maj7`, `sus4 -> sus4`, and keep unknown structures as written.
- Chords render smaller than lyrics, about 70-75% of body size.
- Chords use a distinct muted color, not the same color as lyrics.
- Chords are positioned above the lyric x-coordinate where the chord tag appears.

## PDF Rendering Design

Add a custom ReportLab `Flowable`, tentatively named `ChordLyricsBlock`, for verse bodies that contain structured lines.

Each parsed lyric line becomes a list of positioned text runs:

- lyric segments contain visible lyric text
- chord markers contain chord text and the lyric character offset where the chord applies
- line breaks split blocks into separate visual rows

The flowable will:

- measure lyric text using `pdfmetrics.stringWidth`
- draw chord text above the corresponding lyric position
- draw the lyric line below the chord row
- use one line height that includes chord ascent plus lyric leading
- wrap long lyric lines conservatively at whitespace when possible
- keep chord positions tied to measured text, not monospace spacing tricks

The existing `Paragraph` body rendering can remain for lyric-only verses if that keeps the implementation smaller, but a single flowable path for both chorded and unchorded lines is acceptable if tests cover both.

## Typography And UX

Keep the current document structure:

- table of contents: 3 columns
- song content: 2 columns
- songs should not start in the right column
- title, author, verse labels, and body text should remain compact enough for a printable songbook

Improve color defaults:

- TOC entries should not be blue and should not be black. Use a restrained dark slate/teal color such as `#355C63`.
- TOC title should use the same neutral family as the rest of the document.
- Song title color should remain distinct but not overly bright, such as dark burgundy.
- Verse labels should be muted and readable, not hyperlink-blue.
- Lyrics should remain near-black for readability.
- Chords should use a muted accent color such as `#8A4B2A` or `#7A4E8A`, clearly different from lyrics.

Prefer print-friendly contrast over decorative styling. Avoid making the PDF look like a web page. The output should feel like a compact musician/congregation songbook: readable, scannable, and predictable.

## Language Support

Keep Hebrew support and make the code less Hebrew-specific where practical.

Scope for this change:

- Continue registering Unicode fonts where available.
- Detect RTL scripts for Hebrew and apply `python-bidi` visual ordering when installed.
- Support general Unicode LTR scripts such as Greek, Cyrillic, and CJK when the selected font has glyph coverage.
- Do not implement Arabic or Indic shaping now.
- Keep the parsing/rendering code structured so future shaping can be added in one text-layout function rather than spread through XML parsing and PDF drawing.

If a font is missing glyphs for a language, the script should warn or fall back as it does today rather than corrupting the parser.

## Tests

Add a small automated test suite before implementation changes.

Required test coverage:

- The existing script compiles after the syntax fix.
- `In The Garden.xml` parses old-style `<chord name="..."/>` chords and preserves lyric text.
- A synthetic root/structure/bass chord renders display names such as `Cm/Eb`.
- Lyric-only sample songs still parse into the same visible lyric text.
- Hebrew/RTL handling remains covered at the text transformation level without adding Arabic/Indic shaping.
- `ChordLyricsBlock.wrap()` returns stable dimensions for lines with and without chords.

PDF pixel-perfect tests are not required. The important behavior is parser correctness and layout object stability.

## Non-Goals

- Do not switch the project to ChordPro.
- Do not add Arabic or Indic shaping in this change.
- Do not redesign the whole PDF.
- Do not change the FreeWorship input folder behavior unless needed for tests.
- Do not add transposition or chord diagrams.

## References

- OpenLyrics documentation: https://docs.openlyrics.org/en/latest/dataformat.html#chords
- OpenLyrics changes: https://docs.openlyrics.org/en/latest/changes.html
