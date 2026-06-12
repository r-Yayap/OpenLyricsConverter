# OpenLyrics Chord PDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenLyrics chord parsing and chord-above-lyrics PDF rendering to the FreeWorship XML songbook converter.

**Architecture:** Keep the existing single-script ReportLab converter, but add small structured lyric models, a namespace-safe OpenLyrics mixed-content parser, and a custom `ChordLyricsBlock` flowable. Preserve the existing 3-column TOC and 2-column song pages while improving print-friendly colors.

**Tech Stack:** Python stdlib `xml.etree.ElementTree`, `unittest`, ReportLab Platypus/PDF metrics, optional `python-bidi` when installed.

---

## File Structure

- Modify `FWDB_to_PDF.py`: fix syntax error, add chord data structures, parsing helpers, language detection helpers, chord flowable, and PDF integration.
- Create `tests/test_fwdb_to_pdf.py`: stdlib unit tests for compilation/import, chord parsing, chord formatting, lyric-only preservation, RTL detection, and flowable wrap dimensions.
- Use inline XML strings in tests for old-style `name`, root/structure/bass, wrapped chord, and lyric-only cases.
- Use `sample-songs/` only for final local PDF smoke verification, because those files are currently untracked in this workspace.

## Task 1: Make The Script Importable

**Files:**
- Modify: `FWDB_to_PDF.py:155-163`
- Create: `tests/test_fwdb_to_pdf.py`

- [ ] **Step 1: Write the failing import test**

Create `tests/test_fwdb_to_pdf.py` with:

```python
import importlib.util
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "FWDB_to_PDF.py"


def load_module():
    spec = importlib.util.spec_from_file_location("fwdb_to_pdf", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ImportTests(unittest.TestCase):
    def test_module_imports(self):
        module = load_module()
        self.assertTrue(hasattr(module, "PDFConverter"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ImportTests.test_module_imports -v
```

Expected: FAIL/ERROR with `SyntaxError: invalid syntax` at `ForceLeftColumn.wrap`.

- [ ] **Step 3: Fix the syntax error**

Change:

```python
    def wrap(self, availWidth, availHeight):`
```

to:

```python
    def wrap(self, availWidth, availHeight):
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ImportTests.test_module_imports -v
```

Expected: PASS.

## Task 2: Add Chord Data Models And Formatting

**Files:**
- Modify: `FWDB_to_PDF.py:17-20`, after `findall_by_local`
- Modify: `tests/test_fwdb_to_pdf.py`

- [ ] **Step 1: Write failing chord formatting tests**

Append to `tests/test_fwdb_to_pdf.py`:

```python
class ChordFormattingTests(unittest.TestCase):
    def test_old_style_name_attribute_wins(self):
        module = load_module()
        chord = module.format_chord_name({"name": "A7", "root": "C", "structure": "min"})
        self.assertEqual(chord, "A7")

    def test_root_structure_bass_formats_common_structure(self):
        module = load_module()
        chord = module.format_chord_name({"root": "C", "structure": "min", "bass": "Eb"})
        self.assertEqual(chord, "Cm/Eb")

    def test_unknown_structure_is_preserved(self):
        module = load_module()
        chord = module.format_chord_name({"root": "E", "structure": "3-5-m7-13"})
        self.assertEqual(chord, "E3-5-m7-13")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ChordFormattingTests -v
```

Expected: ERROR because `format_chord_name` does not exist.

- [ ] **Step 3: Add data models and chord formatter**

Add imports:

```python
from dataclasses import dataclass, field
from typing import Callable, List, Optional
```

Replace the current `from typing import Callable` import.

Add after `findall_by_local`:

```python
@dataclass
class ChordMarker:
    text: str
    offset: int


@dataclass
class LyricLine:
    text: str = ""
    chords: List[ChordMarker] = field(default_factory=list)

    @property
    def has_chords(self) -> bool:
        return bool(self.chords)


STRUCTURE_NOTATION = {
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


def format_chord_name(attributes) -> str:
    old_name = (attributes.get("name") or "").strip()
    if old_name:
        return old_name

    root = (attributes.get("root") or "").strip()
    if not root:
        return ""

    structure = (attributes.get("structure") or "").strip()
    suffix = STRUCTURE_NOTATION.get(structure, structure)
    bass = (attributes.get("bass") or "").strip()
    chord = f"{root}{suffix}"
    return f"{chord}/{bass}" if bass else chord
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ChordFormattingTests -v
```

Expected: PASS.

## Task 3: Parse OpenLyrics Mixed Content Into Structured Lines

**Files:**
- Modify: `FWDB_to_PDF.py`, after `format_chord_name`
- Modify: `FWDB_to_PDF.py:316-396`
- Modify: `tests/test_fwdb_to_pdf.py`

- [ ] **Step 1: Write failing parser tests**

Append to `tests/test_fwdb_to_pdf.py`:

```python
class ChordParsingTests(unittest.TestCase):
    def test_old_style_empty_chords_preserve_offsets(self):
        module = load_module()
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties><titles><title>In The Garden</title></titles></properties>
  <lyrics><verse name="v1"><lines>I <chord name="G"/>come to the garden alone, While the <chord name="C"/>dew is still on the <chord name="G"/>roses;<br/></lines></verse></lyrics>
</song>"""
        root = module.ET.fromstring(xml)
        lines_el = next(el for el in root.iter() if module.local_name(el.tag) == "lines")
        first_line = module.parse_lyric_lines(lines_el)[0]
        self.assertEqual(first_line.text, "I come to the garden alone, While the dew is still on the roses;")
        self.assertEqual([(c.text, c.offset) for c in first_line.chords[:3]], [("G", 2), ("C", 38), ("G", 58)])

    def test_root_structure_bass_chord_preserves_wrapped_lyric(self):
        module = load_module()
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.9">
  <properties><titles><title>Wrapped</title></titles></properties>
  <lyrics><verse name="v1"><lines>Ho<chord root="C" structure="min" bass="Eb">san</chord>na<br/></lines></verse></lyrics>
</song>"""
        root = module.ET.fromstring(xml)
        lines_el = next(el for el in root.iter() if module.local_name(el.tag) == "lines")
        lines = module.parse_lyric_lines(lines_el)
        self.assertEqual(lines[0].text, "Hosanna")
        self.assertEqual([(c.text, c.offset) for c in lines[0].chords], [("Cm/Eb", 2)])

    def test_lyric_only_lines_are_preserved(self):
        module = load_module()
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties><titles><title>In This House</title></titles></properties>
  <lyrics><verse name="c"><lines>IN THIS HOUSE THERE IS HOPE<br/>IN THIS HOUSE</lines></verse></lyrics>
</song>"""
        root = module.ET.fromstring(xml)
        lines_el = next(el for el in root.iter() if module.local_name(el.tag) == "lines")
        first_line = module.parse_lyric_lines(lines_el)[0]
        self.assertEqual(first_line.text, "IN THIS HOUSE THERE IS HOPE")
        self.assertEqual(first_line.chords, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ChordParsingTests -v
```

Expected: ERROR because `parse_lyric_lines` and `lyric_lines` do not exist.

- [ ] **Step 3: Add mixed-content parser**

Add after `format_chord_name`:

```python
def append_text_to_line(line: LyricLine, text: Optional[str]) -> None:
    if text:
        line.text += text


def parse_lyric_lines(node) -> List[LyricLine]:
    lines = [LyricLine()]

    def current_line() -> LyricLine:
        return lines[-1]

    def new_line() -> None:
        if current_line().text or current_line().chords:
            lines.append(LyricLine())

    def walk(element) -> None:
        append_text_to_line(current_line(), getattr(element, "text", None))
        for child in list(element):
            tag = local_name(child.tag)
            if tag == "br":
                new_line()
            elif tag == "line":
                walk(child)
                new_line()
            elif tag == "chord":
                chord_text = format_chord_name(child.attrib)
                if chord_text:
                    current_line().chords.append(ChordMarker(chord_text, len(current_line().text)))
                walk(child)
            else:
                walk(child)
            append_text_to_line(current_line(), getattr(child, "tail", None))

    walk(node)
    return [line for line in lines if line.text.strip() or line.chords]
```

In `parse_xml`, replace `to_raw_without_chords()` usage with `parse_lyric_lines()` and keep an escaped plain HTML string for compatibility:

```python
parsed_lines = []
for lb in lines_blocks:
    parsed_lines.extend(parse_lyric_lines(lb))
if not lines_blocks:
    parsed_lines = parse_lyric_lines(verse_el)

for line in parsed_lines:
    line.text = normalize_text(line.text.strip())
parsed_lines = [line for line in parsed_lines if line.text or line.chords]
```

Build `html_text` from `parsed_lines`:

```python
html_text = "<br/>".join(html_escape(line.text) for line in parsed_lines)
```

Append verses as:

```python
verses.append({
    "name": display_name,
    "lines": html_text,
    "lyric_lines": parsed_lines,
    "lang": verse_lang,
})
```

- [ ] **Step 4: Run parser tests to verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ChordParsingTests -v
```

Expected: PASS.

## Task 4: Make Language Handling Less Hebrew-Specific

**Files:**
- Modify: `FWDB_to_PDF.py:95-99`, `FWDB_to_PDF.py:341-342`, `FWDB_to_PDF.py:393-394`, `FWDB_to_PDF.py:626-630`
- Modify: `tests/test_fwdb_to_pdf.py`

- [ ] **Step 1: Write failing script detection tests**

Append to `tests/test_fwdb_to_pdf.py`:

```python
class ScriptHandlingTests(unittest.TestCase):
    def test_hebrew_is_detected_as_rtl(self):
        module = load_module()
        self.assertTrue(module.is_rtl_text("שלום"))

    def test_greek_and_cyrillic_are_not_rtl(self):
        module = load_module()
        self.assertFalse(module.is_rtl_text("Χριστός"))
        self.assertFalse(module.is_rtl_text("Господь"))
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ScriptHandlingTests -v
```

Expected: ERROR because `is_rtl_text` does not exist.

- [ ] **Step 3: Add reusable text direction helpers**

Replace:

```python
HEBREW_RE = re.compile(r'[\u0590-\u05FF]')
def contains_hebrew(s: str) -> bool:
    return bool(HEBREW_RE.search(s or ""))
```

with:

```python
RTL_RE = re.compile(r'[\u0590-\u05FF]')


def is_rtl_text(s: str) -> bool:
    return bool(RTL_RE.search(s or ""))


def contains_hebrew(s: str) -> bool:
    return is_rtl_text(s)
```

Update title, verse, and PDF style checks to call `is_rtl_text(...)`. Keep `contains_hebrew` as a compatibility wrapper for existing code and tests.

- [ ] **Step 4: Run script tests to verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ScriptHandlingTests -v
```

Expected: PASS.

## Task 5: Add ChordLyricsBlock Flowable

**Files:**
- Modify: `FWDB_to_PDF.py`, after `ForceLeftColumn`
- Modify: `tests/test_fwdb_to_pdf.py`

- [ ] **Step 1: Write failing flowable wrap tests**

Append to `tests/test_fwdb_to_pdf.py`:

```python
class ChordLyricsBlockTests(unittest.TestCase):
    def test_wrap_returns_stable_dimensions_for_chorded_line(self):
        module = load_module()
        line = module.LyricLine("Amazing grace", [module.ChordMarker("G", 0), module.ChordMarker("C", 8)])
        block = module.ChordLyricsBlock(
            [line],
            lyric_font="Helvetica",
            lyric_size=8,
            lyric_color=module.colors.HexColor("#202020"),
            chord_font="Helvetica-Bold",
            chord_size=6,
            chord_color=module.colors.HexColor("#8A4B2A"),
            space_after=3,
        )
        width, height = block.wrap(200, 500)
        self.assertEqual(width, 200)
        self.assertGreater(height, 10)

    def test_wrap_returns_stable_dimensions_for_lyric_only_line(self):
        module = load_module()
        line = module.LyricLine("Amazing grace", [])
        block = module.ChordLyricsBlock(
            [line],
            lyric_font="Helvetica",
            lyric_size=8,
            lyric_color=module.colors.HexColor("#202020"),
            chord_font="Helvetica-Bold",
            chord_size=6,
            chord_color=module.colors.HexColor("#8A4B2A"),
            space_after=3,
        )
        width, height = block.wrap(200, 500)
        self.assertEqual(width, 200)
        self.assertGreater(height, 8)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ChordLyricsBlockTests -v
```

Expected: ERROR because `ChordLyricsBlock` does not exist.

- [ ] **Step 3: Add flowable implementation**

Add after `ForceLeftColumn`:

```python
class ChordLyricsBlock(Flowable):
    def __init__(
        self,
        lines: List[LyricLine],
        lyric_font: str,
        lyric_size: float,
        lyric_color,
        chord_font: str,
        chord_size: float,
        chord_color,
        space_after: float = 0,
    ):
        super().__init__()
        self.lines = lines
        self.lyric_font = lyric_font
        self.lyric_size = lyric_size
        self.lyric_color = lyric_color
        self.chord_font = chord_font
        self.chord_size = chord_size
        self.chord_color = chord_color
        self.space_after = space_after
        self._wrapped_width = 0
        self._line_height = self.lyric_size + self.chord_size + 3
        self._height = 0

    def wrap(self, availWidth, availHeight):
        self._wrapped_width = availWidth
        self._line_height = self.lyric_size + self.chord_size + 3
        self._height = len(self.lines) * self._line_height + self.space_after
        return availWidth, self._height

    def _text_width(self, text: str, font: str, size: float) -> float:
        return pdfmetrics.stringWidth(text or "", font, size)

    def _x_for_offset(self, line: LyricLine, offset: int) -> float:
        safe_offset = max(0, min(offset, len(line.text)))
        return self._text_width(line.text[:safe_offset], self.lyric_font, self.lyric_size)

    def draw(self):
        y = self._height - self._line_height
        for line in self.lines:
            chord_y = y + self.lyric_size + 1
            lyric_y = y
            self.canv.setFont(self.chord_font, self.chord_size)
            self.canv.setFillColor(self.chord_color)
            for chord in line.chords:
                x = min(self._x_for_offset(line, chord.offset), max(self._wrapped_width - self._text_width(chord.text, self.chord_font, self.chord_size), 0))
                self.canv.drawString(x, chord_y, chord.text)

            self.canv.setFont(self.lyric_font, self.lyric_size)
            self.canv.setFillColor(self.lyric_color)
            self.canv.drawString(0, lyric_y, line.text)
            y -= self._line_height
```

- [ ] **Step 4: Run flowable tests to verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ChordLyricsBlockTests -v
```

Expected: PASS.

## Task 6: Integrate ChordLyricsBlock And Update Print-Friendly Colors

**Files:**
- Modify: `FWDB_to_PDF.py:178-207`, `FWDB_to_PDF.py:216-222`, `FWDB_to_PDF.py:494-561`, `FWDB_to_PDF.py:626-635`
- Modify: `tests/test_fwdb_to_pdf.py`

- [ ] **Step 1: Write failing theme integration tests**

Append to `tests/test_fwdb_to_pdf.py`:

```python
class ThemeIntegrationTests(unittest.TestCase):
    def test_converter_exposes_chord_style_defaults(self):
        module = load_module()
        converter = module.PDFConverter(".", "out.pdf")
        self.assertEqual(converter.chord_text_font_size, 5.25)
        self.assertEqual(converter.chord_text_color.hexval().lower(), "0x8a4b2a")
        self.assertEqual(converter.toc_text_color.hexval().lower(), "0x355c63")

    def test_set_theme_accepts_chord_and_toc_settings(self):
        module = load_module()
        converter = module.PDFConverter(".", "out.pdf")
        converter.set_theme(chord_text_font_size=6, toc_text_color=module.colors.HexColor("#445566"))
        self.assertEqual(converter.chord_text_font_size, 6)
        self.assertEqual(converter.toc_text_color.hexval().lower(), "0x445566")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ThemeIntegrationTests -v
```

Expected: ERROR because the new theme attributes do not exist.

- [ ] **Step 3: Add theme defaults and use flowable in PDF**

In `__init__`, update colors and add chord/TOC theme attributes:

```python
self.title_color = colors.HexColor("#7A1F2B")
self.verse_name_color = colors.HexColor("#355C63")
self.body_text_color = colors.HexColor("#202020")
self.chord_text_font_size = 5.25
self.chord_text_font_name = "Helvetica"
self.chord_text_bold = True
self.chord_text_italic = False
self.chord_text_color = colors.HexColor("#8A4B2A")
self.toc_text_color = colors.HexColor("#355C63")
self.toc_title_color = colors.HexColor("#2F3A3D")
```

Add these keys to `set_theme()`:

```python
"chord_text_font_size", "chord_text_font_name", "chord_text_bold", "chord_text_italic", "chord_text_color",
"toc_text_color", "toc_title_color",
```

Scale `chord_text_font_size` in `scale_fonts()`.

In `create_pdf()`, use `self.toc_text_color` for TOC level style and `toc.linkColor`. Use `self.toc_title_color` for the TOC title.

When adding verse bodies, replace `Paragraph(verse["lines"], bp_style)` with:

```python
lines_for_block = verse.get("lyric_lines") or [LyricLine(text=re.sub(r"<br\\s*/?>", "\n", verse["lines"]).replace("<br/>", "\n"))]
bp = ChordLyricsBlock(
    lines_for_block,
    lyric_font=bp_style.fontName,
    lyric_size=bp_style.fontSize,
    lyric_color=bp_style.textColor,
    chord_font=pick_font(self.chord_text_font_name, self.chord_text_bold, self.chord_text_italic),
    chord_size=self.chord_text_font_size,
    chord_color=self.chord_text_color,
    space_after=self.body_text_space_after,
)
```

Keep `bp.keepWithNext` behavior unchanged.

- [ ] **Step 4: Run theme integration tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_fwdb_to_pdf.ThemeIntegrationTests -v
```

Expected: PASS.

## Task 7: Full Verification And Sample PDF

**Files:**
- Modify only if a verification issue is found: `FWDB_to_PDF.py`, `tests/test_fwdb_to_pdf.py`

- [ ] **Step 1: Run all unit tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 2: Run compile verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m py_compile FWDB_to_PDF.py
```

Expected: exit code 0.

- [ ] **Step 3: Generate a sample PDF from fixture songs**

Run:

```powershell
@'
from pathlib import Path
from FWDB_to_PDF import PDFConverter, UNICODE_FAMILY

root = Path.cwd()
out = root / "sample-songs-output.pdf"
converter = PDFConverter(str(root / "sample-songs"), str(out))
if UNICODE_FAMILY:
    converter.set_theme(
        title_font_name=UNICODE_FAMILY,
        author_font_name=UNICODE_FAMILY,
        verse_name_font_name=UNICODE_FAMILY,
        body_text_font_name=UNICODE_FAMILY,
        chord_text_font_name=UNICODE_FAMILY,
    )
converter.convert_all_xml_to_pdf()
print(out)
'@ | .\.venv\Scripts\python.exe -
```

Expected: command exits 0 and writes `sample-songs-output.pdf`.

- [ ] **Step 4: Inspect git diff**

Run:

```powershell
git diff -- FWDB_to_PDF.py tests/test_fwdb_to_pdf.py docs/superpowers/plans/2026-06-13-openlyrics-chord-pdf.md
```

Expected: diff is limited to the converter, tests, and plan.

- [ ] **Step 5: Commit implementation files only**

Run:

```powershell
git add -- FWDB_to_PDF.py tests/test_fwdb_to_pdf.py docs/superpowers/plans/2026-06-13-openlyrics-chord-pdf.md
git commit --only -m "feat: render OpenLyrics chords in songbook PDF" -- FWDB_to_PDF.py tests/test_fwdb_to_pdf.py docs/superpowers/plans/2026-06-13-openlyrics-chord-pdf.md
```

Expected: commit excludes the pre-existing unrelated `YML_CSV.py`, `.idea/`, and untracked sample song files.

## Self-Review

- Spec coverage: chord parsing, chord-above-lyrics rendering, TOC/content colors, Unicode/Hebrew scope, tests, and non-goals are covered.
- Placeholder scan: no `TBD`, `TODO`, or deferred implementation steps are present.
- Type consistency: the plan consistently uses `ChordMarker`, `LyricLine`, `format_chord_name`, `parse_lyric_lines`, `ChordLyricsBlock`, and `lyric_lines`.
