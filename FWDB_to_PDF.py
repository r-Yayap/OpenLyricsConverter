from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as html_escape
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    BaseDocTemplate, Paragraph, Spacer, Frame, PageTemplate,
    NextPageTemplate, PageBreak, Flowable
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib import colors
from reportlab.platypus.flowables import CondPageBreak, KeepTogether

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from dataclasses import dataclass, field
from typing import Callable, List, Optional
import unicodedata
import os
import re


# ========= Optional RTL visual reordering =========
def _identity_display(s: str) -> str:
    return s

try:
    # pip install python-bidi
    from bidi.algorithm import get_display as _bidi_get_display  # type: ignore[import-not-found]
    rtl_visual: Callable[[str], str] = _bidi_get_display
except Exception:
    rtl_visual = _identity_display


# ========= Robust Unicode font registration =========
def _register_font_family(display_name: str, regular: str, bold: str = None, italic: str = None, bolditalic: str = None):
    """Register whichever styles exist; missing styles are simply skipped."""
    if regular and os.path.exists(regular):
        pdfmetrics.registerFont(TTFont(f"{display_name}", regular))
    if bold and os.path.exists(bold):
        pdfmetrics.registerFont(TTFont(f"{display_name}-Bold", bold))
    if italic and os.path.exists(italic):
        pdfmetrics.registerFont(TTFont(f"{display_name}-Italic", italic))
    if bolditalic and os.path.exists(bolditalic):
        pdfmetrics.registerFont(TTFont(f"{display_name}-BoldItalic", bolditalic))

# Try a few families (edit paths as needed). We’ll pick the first that’s found.
CANDIDATES = [
    # Noto Sans Hebrew (recommended)
    ("NotoSansHebrew",
     r"C:\fonts\NotoSansHebrew-Regular.ttf",
     r"C:\fonts\NotoSansHebrew-Bold.ttf",
     r"C:\fonts\NotoSansHebrew-Italic.ttf",          # may not exist
     r"C:\fonts\NotoSansHebrew-BoldItalic.ttf"),     # may not exist

    # DejaVu Sans
    ("DejaVuSans",
     r"C:\fonts\DejaVuSans.ttf",
     r"C:\fonts\DejaVuSans-Bold.ttf",
     r"C:\fonts\DejaVuSans-Oblique.ttf",
     r"C:\fonts\DejaVuSans-BoldOblique.ttf"),

    # Windows system fonts (often available on Windows)
    ("SegoeUI",
     r"C:\Windows\Fonts\segoeui.ttf",
     r"C:\Windows\Fonts\segoeuib.ttf",
     r"C:\Windows\Fonts\segoeuii.ttf",
     r"C:\Windows\Fonts\segoeuiz.ttf"),

    ("Arial",
     r"C:\Windows\Fonts\arial.ttf",
     r"C:\Windows\Fonts\arialbd.ttf",
     r"C:\Windows\Fonts\ariali.ttf",
     r"C:\Windows\Fonts\arialbi.ttf"),
]

UNICODE_FAMILY = None
for name, reg, bold, ital, boldital in CANDIDATES:
    try:
        # At least a regular face must exist
        if not os.path.exists(reg):
            continue
        _register_font_family(name, reg, bold, ital, boldital)
        UNICODE_FAMILY = name
        print(f"Using Unicode font family: {UNICODE_FAMILY}")
        break
    except Exception:
        continue

if not UNICODE_FAMILY:
    print("WARNING: No Unicode TTF family registered. Hebrew will render as squares. "
          "Install Noto Sans Hebrew or DejaVu Sans and update paths.")


# ========= Regex helpers =========
RTL_RE = re.compile(r'[\u0590-\u05FF]')


def is_rtl_text(s: str) -> bool:
    return bool(RTL_RE.search(s or ""))


def contains_hebrew(s: str) -> bool:
    return is_rtl_text(s)


# ===== Helpers required across the module =====
C1_TO_UNICODE = {
    0x80: '€', 0x82: '‚', 0x83: 'ƒ', 0x84: '„', 0x85: '…', 0x86: '†', 0x87: '‡',
    0x88: 'ˆ', 0x89: '‰', 0x8A: 'Š', 0x8B: '‹', 0x8C: 'Œ',
    0x91: '‘', 0x92: '’', 0x93: '“', 0x94: '”', 0x95: '•', 0x96: '–', 0x97: '—',
    0x98: '˜', 0x99: '™', 0x9A: 'š', 0x9B: '›', 0x9C: 'œ', 0x9F: 'Ÿ',
}

def clean_text(s: str) -> str:
    """Replace C1 controls with proper Unicode and strip other control chars."""
    out = []
    for ch in s:
        code = ord(ch)
        if 0x80 <= code <= 0x9F:
            out.append(C1_TO_UNICODE.get(code, ''))  # replace known, drop unknown
        else:
            out.append(ch)
    s = ''.join(out)
    # keep normal whitespace; drop other control chars
    s = ''.join(c for c in s if (c >= ' ' or c in '\n\r\t'))
    return s

def local_name(tag: str) -> str:
    """Return tag without namespace prefix like '{ns}tag' -> 'tag'."""
    return tag.split('}', 1)[-1] if '}' in tag else tag

def find_first_by_local(root, *path_candidates):
    """
    Namespace-agnostic 'find'. Try several short paths using local names only.
    Example: 'properties/titles/title' or 'title'
    """
    for cand in path_candidates:
        node = root
        ok = True
        for seg in cand.strip('/').split('/'):
            matched = None
            for child in node:
                if isinstance(child.tag, str) and local_name(child.tag) == seg:
                    matched = child
                    break
            if matched is None:
                ok = False
                break
            node = matched
        if ok:
            return node
    return None

def findall_by_local(root, tag_name: str):
    """Namespace-agnostic 'findall' by local name anywhere under root."""
    return [el for el in root.iter() if isinstance(el.tag, str) and local_name(el.tag) == tag_name]


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


# ===== Layout helper to never start songs in the right column =====
class ForceLeftColumn(Flowable):
    """
    If the current frame is the right column, force a page break so that
    the next content starts at the left column of the next page.
    Otherwise, do nothing.
    """
    def wrap(self, availWidth, availHeight):
        self._availHeight = availHeight
        return (0, 0)

    def draw(self):
        frame = getattr(self.canv, "_frame", None)
        frame_id = getattr(frame, "id", "")
        if frame_id == "rightColumn":
            self.canv.showPage()


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
        alignment: int = TA_LEFT,
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
        self.alignment = alignment
        self._wrapped_width = 0
        self._line_height = self.lyric_size + self.chord_size + 3
        self._height = 0
        self._visual_lines: List[LyricLine] = []

    def wrap(self, availWidth, availHeight):
        self._wrapped_width = availWidth
        self._line_height = self.lyric_size + self.chord_size + 3
        self._visual_lines = []
        for line in self.lines:
            self._visual_lines.extend(self._wrap_line(line, availWidth))
        self._height = len(self._visual_lines) * self._line_height + self.space_after
        return availWidth, self._height

    def _text_width(self, text: str, font: str, size: float) -> float:
        return pdfmetrics.stringWidth(text or "", font, size)

    def _x_for_offset(self, line: LyricLine, offset: int) -> float:
        safe_offset = max(0, min(offset, len(line.text)))
        return self._text_width(line.text[:safe_offset], self.lyric_font, self.lyric_size)

    def _max_fitting_end(self, text: str, start: int, avail_width: float) -> int:
        low = start + 1
        high = len(text)
        best = low
        while low <= high:
            mid = (low + high) // 2
            if self._text_width(text[start:mid], self.lyric_font, self.lyric_size) <= avail_width:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        return best

    def _wrap_line(self, line: LyricLine, avail_width: float) -> List[LyricLine]:
        if not line.text:
            return [line]
        if self._text_width(line.text, self.lyric_font, self.lyric_size) <= avail_width:
            return [line]

        wrapped = []
        text = line.text
        start = 0
        while start < len(text):
            while start < len(text) and text[start] == " ":
                start += 1
            if start >= len(text):
                break

            max_end = self._max_fitting_end(text, start, avail_width)
            if max_end >= len(text):
                end = len(text)
                next_start = end
            else:
                break_at = text.rfind(" ", start + 1, max_end + 1)
                if break_at > start:
                    end = break_at
                    next_start = break_at + 1
                else:
                    end = max_end
                    next_start = max_end

            part_text = text[start:end].rstrip()
            if not part_text:
                part_text = text[start:next_start] or text[start]
                end = start + len(part_text)
                next_start = end

            part_chords = [
                ChordMarker(chord.text, max(0, min(len(part_text), chord.offset - start)))
                for chord in line.chords
                if start <= chord.offset < next_start
            ]
            wrapped.append(LyricLine(part_text, part_chords))
            start = next_start

        return wrapped or [line]

    def draw(self):
        if not self._visual_lines:
            self._visual_lines = self.lines

        y = self._height - self._line_height
        for line in self._visual_lines:
            chord_y = y + self.lyric_size + 1
            lyric_y = y
            lyric_width = self._text_width(line.text, self.lyric_font, self.lyric_size)
            base_x = max(self._wrapped_width - lyric_width, 0) if self.alignment == TA_RIGHT else 0

            self.canv.setFont(self.chord_font, self.chord_size)
            self.canv.setFillColor(self.chord_color)
            for chord in line.chords:
                chord_width = self._text_width(chord.text, self.chord_font, self.chord_size)
                max_x = max(self._wrapped_width - chord_width, 0)
                x = min(base_x + self._x_for_offset(line, chord.offset), max_x)
                self.canv.drawString(x, chord_y, chord.text)

            self.canv.setFont(self.lyric_font, self.lyric_size)
            self.canv.setFillColor(self.lyric_color)
            self.canv.drawString(base_x, lyric_y, line.text)
            y -= self._line_height


# ===== Main converter =====
class PDFConverter:
    def __init__(self, xml_folder_path, output_pdf_path):
        self.xml_folder_path = xml_folder_path
        self.output_pdf_path = output_pdf_path

        # Font and spacing configuration (default theme)
        self.title_font_size = 11
        self.title_font_name = 'Helvetica'
        self.title_bold = True
        self.title_italic = False
        self.title_color = colors.HexColor("#7A1F2B")
        self.title_space_before = 11
        self.title_space_after = 1

        self.author_font_size = 6
        self.author_font_name = 'Times-Roman'
        self.author_bold = False
        self.author_italic = True
        self.author_color = colors.darkgray
        self.author_space_after = 5

        self.verse_name_font_size = 7.5
        self.verse_name_font_name = 'Courier'
        self.verse_name_bold = True
        self.verse_name_italic = False
        self.verse_name_color = colors.HexColor("#355C63")
        self.verse_name_space_after = 1

        self.body_text_font_size = 7
        self.body_text_font_name = 'Helvetica'
        self.body_text_bold = False
        self.body_text_italic = False
        self.body_text_color = colors.HexColor("#202020")
        self.body_text_space_after = 3
        self.line_spacing = 4  # extra space between verses

        self.chord_text_font_size = 5.25
        self.chord_text_font_name = 'Helvetica'
        self.chord_text_bold = True
        self.chord_text_italic = False
        self.chord_text_color = colors.HexColor("#8A4B2A")

        self.toc_text_color = colors.HexColor("#355C63")
        self.toc_title_color = colors.HexColor("#2F3A3D")

    # --- Theme helpers -------------------------------------------------
    def set_theme(self, **kwargs):
        """
        Convenience setter for fonts/spacings/colors.
        Example:
          converter.set_theme(body_text_font_size=10, title_bold=False, verse_name_font_name='Helvetica')
        """
        allowed = {
            'title_font_size','title_font_name','title_bold','title_italic','title_color',
            'title_space_before','title_space_after',
            'author_font_size','author_font_name','author_bold','author_italic','author_color','author_space_after',
            'verse_name_font_size','verse_name_font_name','verse_name_bold','verse_name_italic','verse_name_color','verse_name_space_after',
            'body_text_font_size','body_text_font_name','body_text_bold','body_text_italic','body_text_color','body_text_space_after',
            'chord_text_font_size','chord_text_font_name','chord_text_bold','chord_text_italic','chord_text_color',
            'toc_text_color','toc_title_color',
            'line_spacing'
        }
        for k, v in kwargs.items():
            if k in allowed:
                setattr(self, k, v)

    def scale_fonts(self, factor: float):
        """
        Scale all font sizes and vertical spacings at once.
        E.g., converter.scale_fonts(1.15) for +15%.
        """
        self.title_font_size       = round(self.title_font_size       * factor, 2)
        self.author_font_size      = round(self.author_font_size      * factor, 2)
        self.verse_name_font_size  = round(self.verse_name_font_size  * factor, 2)
        self.body_text_font_size   = round(self.body_text_font_size   * factor, 2)
        self.chord_text_font_size  = round(self.chord_text_font_size  * factor, 2)

        self.title_space_before    = round(self.title_space_before    * factor, 2)
        self.title_space_after     = round(self.title_space_after     * factor, 2)
        self.author_space_after    = round(self.author_space_after    * factor, 2)
        self.verse_name_space_after= round(self.verse_name_space_after* factor, 2)
        self.body_text_space_after = round(self.body_text_space_after * factor, 2)
        self.line_spacing          = round(self.line_spacing          * factor, 2)

    # --- Internal helpers ----------------------------------------------
    @staticmethod
    def _safe_anchor(text, used):
        base = re.sub(r'[^A-Za-z0-9_.-]+', '_', (text or '').strip()) or "Song"
        name = base
        i = 2
        while name in used:
            name = f"{base}_{i}"
            i += 1
        used.add(name)
        return name

    @staticmethod
    def measure_block_height(flowables, avail_width):
        """Sum the wrapped heights of flowables at a known width."""
        total = 0
        for f in flowables:
            _, h = f.wrap(avail_width, 10 ** 6)
            total += h
        return total

    # ---------- XML parsing ----------
    def parse_xml(self, xml_file_path):
        print(f"Parsing XML file: {xml_file_path}")

        # --- robust file reading (UTF-8 BOM -> UTF-16 -> Windows-1252) ---
        def read_text_safely(path):
            try:
                with open(path, 'r', encoding='utf-8-sig') as f:
                    return f.read()
            except UnicodeDecodeError:
                pass
            try:
                with open(path, 'rb') as f:
                    return f.read().decode('utf-16')
            except UnicodeDecodeError:
                pass
            with open(path, 'r', encoding='cp1252', errors='replace') as f:
                return f.read()

        content = read_text_safely(xml_file_path)

        try:
            tree = ET.ElementTree(ET.fromstring(content))
            root = tree.getroot()
        except ET.ParseError as e:
            print(f"Error parsing XML: {e}")
            return None

        def normalize_text(s: str) -> str:
            # Clean C1 controls, normalize
            s = clean_text(s)
            s = unicodedata.normalize("NFKC", s)

            # Unify various apostrophe-like glyphs to ASCII apostrophe (fixes ■ for U+02BC etc.)
            APOSTROPHE_MAP = {
                0x02BC: 0x27,  # MODIFIER LETTER APOSTROPHE ʼ -> '
                0x2019: 0x27,  # RIGHT SINGLE QUOTATION MARK ’ -> '
                0x2018: 0x27,  # LEFT SINGLE QUOTATION MARK ‘ -> '
                0x2032: 0x27,  # PRIME ′ -> '
                0xFF07: 0x27,  # FULLWIDTH APOSTROPHE ＇ -> '
            }
            s = s.translate(APOSTROPHE_MAP)

            # (optional) normalize dashes to ASCII hyphen if needed:
            # s = s.translate({0x2013: 0x2D, 0x2014: 0x2D})  # – — -> -

            # Fix Cyrillic lookalike E/e (rare but present in some sources)
            s = s.replace("Е", "E").replace("е", "e")
            return s

        def normalize_lyric_line(line: LyricLine) -> LyricLine:
            normalized_raw = normalize_text(line.text or "")
            leading_trim = len(normalized_raw) - len(normalized_raw.lstrip())
            text = normalized_raw.strip()
            chords = [
                ChordMarker(chord.text, max(0, min(len(text), chord.offset - leading_trim)))
                for chord in line.chords
            ]
            return LyricLine(text=text, chords=chords)

        # --- Title ---
        title_el = find_first_by_local(root, "properties/titles/title", "title")
        title_text = (title_el.text or "").strip() if (title_el is not None and title_el.text) else "Unknown Title"
        title_lang = (title_el.attrib.get('lang') or "").lower() if title_el is not None else ""
        title_text = normalize_text(title_text)
        if title_lang == 'he' or is_rtl_text(title_text):
            title_text = rtl_visual(title_text)

        # --- Authors ---
        authors_parent = find_first_by_local(root, "properties/authors")
        if authors_parent is not None:
            author_els = [c for c in authors_parent if local_name(c.tag) == "author"]
        else:
            author_els = findall_by_local(root, "author")
        authors = [normalize_text((a.text or "Unknown Author").strip()) for a in author_els] or []

        # --- Optional metadata ---
        copyright_el = find_first_by_local(root, "properties/copyright")
        released_el = find_first_by_local(root, "properties/released")
        copyright_text = (
            normalize_text((copyright_el.text or "Unknown Copyright").strip())
            if copyright_el is not None else "Unknown Copyright"
        )
        released_text = (
            normalize_text((released_el.text or "Unknown Release Date").strip())
            if released_el is not None else "Unknown Release Date"
        )

        # --- Lyrics & verses ---
        lyrics_el = find_first_by_local(root, "lyrics")
        verses = []
        if lyrics_el is not None:
            for verse_el in lyrics_el:
                if local_name(verse_el.tag) != "verse":
                    continue

                verse_name_attr = verse_el.attrib.get('name', 'Unnamed')
                verse_lang = (verse_el.attrib.get('lang') or "").lower()

                # Prefer <lines> containers
                lines_blocks = [child for child in verse_el if local_name(child.tag) == "lines"]
                if lines_blocks:
                    parsed_lines = []
                    for lb in lines_blocks:
                        parsed_lines.extend(parse_lyric_lines(lb))
                else:
                    parsed_lines = parse_lyric_lines(verse_el)

                norm_lines = [normalize_lyric_line(line) for line in parsed_lines]
                norm_lines = [line for line in norm_lines if line.text or line.chords]
                if not norm_lines:
                    continue

                if verse_lang == 'he' or any(is_rtl_text(line.text) for line in norm_lines):
                    norm_lines = [LyricLine(rtl_visual(line.text), line.chords) for line in norm_lines]

                html_text = "<br/>".join(html_escape(line.text) for line in norm_lines)

                # Display label mapping
                name_l = verse_name_attr.lower()
                if name_l.startswith('c'):
                    display_name = "Chorus"
                elif name_l.startswith('b'):
                    display_name = "Bridge"
                elif name_l.startswith('e') and name_l != "ei":
                    display_name = "Ending"
                elif name_l == "ei":
                    display_name = None
                elif name_l.startswith('p'):
                    display_name = "Pre-Chorus"
                elif name_l.startswith('v'):
                    m = re.match(r'v(\d+)', name_l, re.IGNORECASE)
                    display_name = f"Verse {m.group(1)}" if m else "Verse"
                else:
                    display_name = verse_name_attr

                verses.append({'name': display_name, 'lines': html_text, 'lyric_lines': norm_lines, 'lang': verse_lang})

        return {
            'title': title_text,
            'title_lang': title_lang,
            'authors': authors,
            'copyright': copyright_text,
            'released': released_text,
            'verses': verses
        }

    # ---------- PDF building ----------
    def create_pdf(self, songs_data):
        doc = MyDocTemplate(self.output_pdf_path, pagesize=A4, title="Songs Collection")

        # Page geometry
        page_width, page_height = A4
        left_margin = doc.leftMargin
        right_margin = doc.rightMargin
        top_margin = doc.topMargin
        bottom_margin = doc.bottomMargin
        usable_width = page_width - left_margin - right_margin
        usable_height = page_height - top_margin - bottom_margin

        # TOC: 3 columns
        column_gap = 6
        toc_frame_width = (usable_width - 2 * column_gap) / 3.0
        frame_toc_left = Frame(left_margin, bottom_margin, toc_frame_width, usable_height, id='tocLeft')
        frame_toc_center = Frame(left_margin + toc_frame_width + column_gap, bottom_margin, toc_frame_width,
                                 usable_height, id='tocCenter')
        frame_toc_right = Frame(left_margin + 2 * (toc_frame_width + column_gap), bottom_margin, toc_frame_width,
                                usable_height, id='tocRight')

        # Content: 2 columns
        content_frame_width = (usable_width - column_gap) / 2.0
        frame_left = Frame(left_margin, bottom_margin, content_frame_width, usable_height, id='leftColumn')
        frame_right = Frame(left_margin + content_frame_width + column_gap, bottom_margin, content_frame_width,
                            usable_height, id='rightColumn')

        toc_template = PageTemplate(id='TOC', frames=[frame_toc_left, frame_toc_center, frame_toc_right])
        two_column_template = PageTemplate(id='TwoCol', frames=[frame_left, frame_right])
        doc.addPageTemplates([toc_template, two_column_template])

        # ---- Font picking helper (robust to missing styles) ----
        def pick_font(base, bold=False, italic=False):
            registered = set(pdfmetrics.getRegisteredFontNames())
            candidates = []
            if bold and italic:
                candidates += [f"{base}-BoldItalic", f"{base}-BoldOblique"]
            if bold:
                candidates += [f"{base}-Bold"]
            if italic:
                candidates += [f"{base}-Italic", f"{base}-Oblique"]
            candidates.append(base)
            for name in candidates:
                if name in registered:
                    return name
            # Fallback to core families mapping if nothing above hits
            if base.startswith('Times'):
                if bold and italic: return 'Times-BoldItalic'
                if bold: return 'Times-Bold'
                if italic: return 'Times-Italic'
                return 'Times-Roman'
            if base.startswith('Helvetica'):
                if bold and italic: return 'Helvetica-BoldOblique'
                if bold: return 'Helvetica-Bold'
                if italic: return 'Helvetica-Oblique'
                return 'Helvetica'
            if base.startswith('Courier'):
                if bold and italic: return 'Courier-BoldOblique'
                if bold: return 'Courier-Bold'
                if italic: return 'Courier-Oblique'
                return 'Courier'
            return base  # last resort

        # Prefer the successfully registered Unicode family for Hebrew (and optionally for all)
        he_font_base = UNICODE_FAMILY if (UNICODE_FAMILY and UNICODE_FAMILY in pdfmetrics.getRegisteredFontNames()) else self.body_text_font_name

        # Styles
        title_style = ParagraphStyle(
            name='Title',
            fontSize=self.title_font_size,
            leading=self.title_font_size + 2,
            textColor=self.title_color,
            spaceAfter=self.title_space_after,
            fontName=pick_font(self.title_font_name, self.title_bold, self.title_italic),
        )
        title_style_he = ParagraphStyle(
            name='TitleHE',
            fontSize=self.title_font_size,
            leading=self.title_font_size + 2,
            textColor=self.title_color,
            spaceAfter=self.title_space_after,
            fontName=pick_font(he_font_base, self.title_bold, self.title_italic),
            alignment=TA_RIGHT,
        )
        author_style = ParagraphStyle(
            name='Author',
            fontSize=self.author_font_size,
            leading=self.author_font_size + 2,
            textColor=self.author_color,
            spaceAfter=self.author_space_after,
            fontName=pick_font(self.author_font_name, self.author_bold, self.author_italic),
        )
        verse_name_style = ParagraphStyle(
            name='VerseName',
            fontSize=self.verse_name_font_size,
            leading=self.verse_name_font_size + 2,
            textColor=self.verse_name_color,
            spaceAfter=self.verse_name_space_after,
            fontName=pick_font(self.verse_name_font_name, self.verse_name_bold, self.verse_name_italic),
        )
        body_text_style = ParagraphStyle(
            name='BodyText',
            fontSize=self.body_text_font_size,
            leading=self.body_text_font_size + 2,
            textColor=self.body_text_color,
            spaceAfter=self.body_text_space_after,
            fontName=pick_font(self.body_text_font_name, self.body_text_bold, self.body_text_italic),
        )
        body_text_style_he = ParagraphStyle(
            name='BodyTextHE',
            fontSize=self.body_text_font_size,
            leading=self.body_text_font_size + 2,
            textColor=self.body_text_color,
            spaceAfter=self.body_text_space_after,
            fontName=pick_font(he_font_base, self.body_text_bold, self.body_text_italic),
            alignment=TA_RIGHT,
        )

        # TOC style (ensure Unicode font is used if available)
        toc = TableOfContents()
        toc.levelStyles = [
            ParagraphStyle(
                fontSize=6,
                name='TOCLevel1',
                leftIndent=16,
                firstLineIndent=-16,
                leading=7,
                textColor=self.toc_text_color,
                spaceBefore=0,
                spaceAfter=0,
                fontName=pick_font(he_font_base, False, False),
            )
        ]
        toc.linkColor = self.toc_text_color
        try:
            toc.dotsMinLevel = 0  # show dot leaders if supported by your ReportLab version
        except Exception:
            pass

        story = []
        story.append(Paragraph("Table of Contents", ParagraphStyle(name="TOCTitle", fontSize=12, spaceAfter=6,
                                                                  fontName=pick_font(he_font_base),
                                                                  textColor=self.toc_title_color)))
        story.append(toc)
        story.append(NextPageTemplate('TwoCol'))
        story.append(PageBreak())

        used_anchors = set()

        for song in songs_data:
            if not song:
                continue

            # Build the exact flowables that constitute *this* song
            song_block = []

            # spacing before title (counted in measurement)
            song_block.append(Spacer(1, self.title_space_before))

            visible_title = song["title"]
            title_lang = song.get("title_lang", "")
            anchor = self._safe_anchor(visible_title, used_anchors)

            # Choose LTR/RTL title style
            title_style_to_use = title_style_he if (title_lang == 'he' or contains_hebrew(visible_title)) else title_style

            # Title paragraph (anchor + outline + toc metadata)
            p = Paragraph(f'<a name="{anchor}"/>{html_escape(visible_title)}', title_style_to_use)
            p.bookmarkName = anchor
            p.outlineLevel = 0
            p.toc_text = visible_title
            p.keepWithNext = True
            song_block.append(p)

            # gap after title
            song_block.append(Spacer(1, self.title_space_after))

            # Authors (optional)
            if song['authors']:
                authors_str = "Authors: " + ", ".join(song['authors'])
                ap = Paragraph(html_escape(authors_str), author_style)
                ap.keepWithNext = True
                song_block.append(ap)
                song_block.append(Spacer(1, self.author_space_after))

            # Verses (labels shown only at the start of a label run)
            last_label = None
            first_body_added = False
            for verse in song['verses']:
                label = verse['name']  # e.g., "Verse 1", "Chorus", None

                if label and label != last_label:
                    vp = Paragraph(html_escape(label), verse_name_style)
                    vp.keepWithNext = True
                    song_block.append(vp)
                    last_label = label
                elif label != last_label:
                    last_label = label

                # Pick body style based on language
                vlang = (verse.get('lang') or "").lower()
                lyric_lines = verse.get('lyric_lines') or []
                is_rtl_verse = (
                    vlang == 'he'
                    or any(is_rtl_text(line.text) for line in lyric_lines)
                    or is_rtl_text(verse['lines'])
                )
                bp_style = body_text_style_he if is_rtl_verse else body_text_style

                fallback_text = verse['lines'].replace("<br/>", "\n")
                lines_for_block = lyric_lines or [
                    LyricLine(text=line) for line in fallback_text.split("\n") if line
                ]
                bp = ChordLyricsBlock(
                    lines_for_block,
                    lyric_font=bp_style.fontName,
                    lyric_size=bp_style.fontSize,
                    lyric_color=bp_style.textColor,
                    chord_font=pick_font(self.chord_text_font_name, self.chord_text_bold, self.chord_text_italic),
                    chord_size=self.chord_text_font_size,
                    chord_color=self.chord_text_color,
                    space_after=self.body_text_space_after,
                    alignment=getattr(bp_style, "alignment", TA_LEFT),
                )
                if not first_body_added:
                    bp.keepWithNext = False
                    first_body_added = True

                song_block.append(bp)
                song_block.append(Spacer(1, self.line_spacing))

            # === Pagination control ===
            required_h = self.measure_block_height(song_block, content_frame_width)
            story.append(CondPageBreak(required_h))  # if remaining space < block height → new page
            story.append(ForceLeftColumn())          # and never start a song in the right column
            story.append(KeepTogether(song_block))   # keep block together if it fits a fresh page

        doc.multiBuild(story)
        print(f"PDF created with table of contents and two columns on content pages: {self.output_pdf_path}")

    def convert_all_xml_to_pdf(self):
        songs_data = []
        for file_name in sorted(os.listdir(self.xml_folder_path), key=str.lower):
            if file_name.lower().endswith('.xml'):
                file_path = os.path.join(self.xml_folder_path, file_name)
                print(f"Processing file: {file_name}")
                song_data = self.parse_xml(file_path)
                if song_data:
                    songs_data.append(song_data)
        self.create_pdf(songs_data)


# ----- Document template with clickable TOC support -----
class MyDocTemplate(BaseDocTemplate):
    def __init__(self, filename, **kw):
        super().__init__(filename, **kw)
        self.allowSplitting = 1
        self.addPageTemplates(kw.get('pageTemplates', []))
        self.maxPasses = 20  # enough passes for stable TOC

    def afterFlowable(self, flowable):
        """
        When a Paragraph representing a song title (with bookmarkName) is drawn,
        create a PDF outline entry and notify the TOC with a clickable link.
        """
        if hasattr(flowable, 'bookmarkName'):
            name  = flowable.bookmarkName
            level = getattr(flowable, 'outlineLevel', 0)
            text  = getattr(flowable, 'toc_text', name)

            self.canv.bookmarkPage(name)
            self.canv.addOutlineEntry(text, name, level=level, closed=False)

            page_num = self.canv.getPageNumber()
            self.notify('TOCEntry', (level, text, page_num, name))


# ----- Run as a script -----
if __name__ == "__main__":
    xml_folder = r"C:\Users\Y\AppData\Roaming\FreeWorship\Data\Songs"
    output_pdf = r"C:\Users\Y\Documents\songs_collection_report.pdf"

    converter = PDFConverter(xml_folder, output_pdf)

    # If a Unicode family was registered, force all styles to use it by default
    if UNICODE_FAMILY:
        converter.set_theme(
            title_font_name=UNICODE_FAMILY,
            author_font_name=UNICODE_FAMILY,
            verse_name_font_name=UNICODE_FAMILY,
            body_text_font_name=UNICODE_FAMILY,
            chord_text_font_name=UNICODE_FAMILY
        )

    # Example tweaks (uncomment to try):
    # converter.scale_fonts(1.10)  # +10% sizes & spacings
    # converter.set_theme(title_font_size=12, body_text_font_size=8.5)

    converter.convert_all_xml_to_pdf()
