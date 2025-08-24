from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape as html_escape
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    BaseDocTemplate, Paragraph, Spacer, Frame, PageTemplate,
    NextPageTemplate, PageBreak
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import unicodedata
import os
import re


# ===== Helpers required across the module =====

# Map common C1 control codes (Windows‑1252 artifacts) to Unicode punctuation
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
    Namespace‑agnostic 'find'. Try several short paths using local names only.
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
    """Namespace‑agnostic 'findall' by local name anywhere under root."""
    return [el for el in root.iter() if isinstance(el.tag, str) and local_name(el.tag) == tag_name]


# ===== Main converter =====

class PDFConverter:
    def __init__(self, xml_folder_path, output_pdf_path):
        self.xml_folder_path = xml_folder_path
        self.output_pdf_path = output_pdf_path

        # Font and spacing configuration
        self.title_font_size = 14
        self.title_font_name = 'Helvetica'
        self.title_bold = True
        self.title_italic = False
        self.title_color = colors.darkred
        self.title_space_before = 16
        self.title_space_after = 1

        self.author_font_size = 7
        self.author_font_name = 'Times-Roman'
        self.author_bold = False
        self.author_italic = True
        self.author_color = colors.darkgray
        self.author_space_after = 5

        self.verse_name_font_size = 10
        self.verse_name_font_name = 'Courier'
        self.verse_name_bold = True
        self.verse_name_italic = False
        self.verse_name_color = colors.darkblue
        self.verse_name_space_after = 1

        self.body_text_font_size = 9
        self.body_text_font_name = 'Helvetica'
        self.body_text_bold = False
        self.body_text_italic = False
        self.body_text_color = colors.black
        self.body_text_space_after = 3
        self.line_spacing = 5  # extra space between verses

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

    # ---------- XML parsing ----------
    def parse_xml(self, xml_file_path):
        import unicodedata
        from xml.sax.saxutils import escape as html_escape

        print(f"Parsing XML file: {xml_file_path}")

        # --- robust file reading (UTF‑8 BOM -> UTF‑16 -> Windows‑1252) ---
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

        # --- parse XML ---
        try:
            tree = ET.ElementTree(ET.fromstring(content))
            root = tree.getroot()
        except ET.ParseError as e:
            print(f"Error parsing XML: {e}")
            return None

        # --- helpers ---
        def normalize_text(s: str) -> str:
            # Clean C1 controls, normalize, fix Cyrillic lookalike E/e
            s = clean_text(s)
            s = unicodedata.normalize("NFKC", s)
            s = s.replace("Е", "E").replace("е", "e")  # Cyrillic IE -> Latin
            return s

        def to_raw_without_chords(node) -> str:
            """
            Walk element in document order and return text where:
              - element.text and child.tail are included
              - <br .../> -> '\n'
              - <line> content included; add '\n' after </line>
              - <chord .../> is ignored (produces no text)
            """
            out = []

            if getattr(node, "text", None):
                out.append(node.text)

            for child in list(node):
                tag = local_name(child.tag)

                if tag == "br":
                    out.append("\n")
                elif tag == "chord":
                    # ignore chords; do not insert spaces
                    pass
                elif tag == "line":
                    out.append(to_raw_without_chords(child))
                    out.append("\n")  # end of <line> → newline
                else:
                    out.append(to_raw_without_chords(child))

                if getattr(child, "tail", None):
                    out.append(child.tail)

            return "".join(out)

        # --- Title (namespace‑agnostic; try common OpenLyrics paths) ---
        title_el = find_first_by_local(root, "properties/titles/title", "title")
        title_text = normalize_text(title_el.text.strip()) if (
                    title_el is not None and title_el.text) else "Unknown Title"

        # --- Authors (prefer properties/authors; fallback to any <author>) ---
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

        # --- Lyrics & verses (preserve original XML order) ---
        lyrics_el = find_first_by_local(root, "lyrics")
        verses = []
        if lyrics_el is not None:
            for verse_el in lyrics_el:
                if local_name(verse_el.tag) != "verse":
                    continue

                verse_name_attr = verse_el.attrib.get('name', 'Unnamed')

                # Prefer <lines> containers if present
                lines_blocks = [child for child in verse_el if local_name(child.tag) == "lines"]
                if lines_blocks:
                    parts = [to_raw_without_chords(lb) for lb in lines_blocks]
                    combined = "\n".join(p for p in parts if p)
                else:
                    combined = to_raw_without_chords(verse_el)

                # Normalize newlines
                combined = re.sub(r'\r\n?', '\n', combined)
                combined = re.sub(r'\n{2,}', '\n', combined)

                # Build HTML for Paragraph: escape & join with <br/>
                lines = [ln for ln in combined.split("\n") if ln.strip()]
                if not lines:
                    continue
                html_text = "<br/>".join(html_escape(normalize_text(ln.strip())) for ln in lines)

                # Map verse name to display label (rendering shows header only at run start)
                name_l = verse_name_attr.lower()
                if name_l.startswith('c'):
                    display_name = "Chorus"
                elif name_l.startswith('b'):
                    display_name = "Bridge"
                elif name_l.startswith('e') and name_l != "ei":
                    display_name = "Ending"
                elif name_l == "ei":
                    display_name = None
                elif name_l.startswith('p'):  # p, pa, pb
                    display_name = "Pre-Chorus"
                elif name_l.startswith('v'):
                    m = re.match(r'v(\d+)', name_l, re.IGNORECASE)
                    display_name = f"Verse {m.group(1)}" if m else "Verse"
                else:
                    display_name = verse_name_attr

                verses.append({'name': display_name, 'lines': html_text})

        return {
            'title': title_text,
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

        # Styles (use fontName for bold/italic)
        def pick_font(base, bold=False, italic=False):
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
            return base

        title_style = ParagraphStyle(
            name='Title',
            fontSize=self.title_font_size,
            leading=self.title_font_size + 2,
            textColor=self.title_color,
            spaceAfter=self.title_space_after,
            fontName=pick_font(self.title_font_name, self.title_bold, self.title_italic),
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

        # TOC style: small, clickable (links are wired by afterFlowable)
        toc = TableOfContents()
        toc.levelStyles = [
            ParagraphStyle(
                fontSize=6,
                name='TOCLevel1',
                leftIndent=16,
                firstLineIndent=-16,
                leading=7,
                textColor=colors.blue,
                spaceBefore=0,
                spaceAfter=0,
            )
        ]

        story = []
        story.append(Paragraph("Table of Contents", ParagraphStyle(name="TOCTitle", fontSize=12, spaceAfter=6)))
        story.append(toc)
        story.append(NextPageTemplate('TwoCol'))
        story.append(PageBreak())

        used_anchors = set()

        # Preserve song order; show verse label only at start of a consecutive run
        for song in songs_data:
            if not song:
                continue

            story.append(Spacer(1, self.title_space_before))

            visible_title = song["title"]
            anchor = self._safe_anchor(visible_title, used_anchors)

            # Title paragraph with named anchor — becomes TOC + outline target
            p = Paragraph(f'<a name="{anchor}"/>{html_escape(visible_title)}', title_style)
            p.bookmarkName = anchor
            p.outlineLevel = 0
            p.toc_text = visible_title
            story.append(p)

            story.append(Spacer(1, self.title_space_after))

            if song['authors']:
                authors_str = "Authors: " + ", ".join(song['authors'])
                story.append(Paragraph(html_escape(authors_str), author_style))
                story.append(Spacer(1, self.author_space_after))

            last_label = None
            for verse in song['verses']:
                label = verse['name']  # e.g., "Verse 1", "Chorus", None

                # Show header only if label exists AND starts a new run
                if label and label != last_label:
                    story.append(Paragraph(html_escape(label), verse_name_style))
                    last_label = label
                elif label != last_label:
                    last_label = label  # reset if label becomes None or changes

                # Verse body (already HTML with <br/> + chord markup)
                story.append(Paragraph(verse['lines'], body_text_style))
                story.append(Spacer(1, self.line_spacing))

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

            # create bookmark + outline
            self.canv.bookmarkPage(name)
            self.canv.addOutlineEntry(text, name, level=level, closed=False)

            # correct page number from canvas
            page_num = self.canv.getPageNumber()

            # TOC notification with key for linking to anchor
            self.notify('TOCEntry', (level, text, page_num, name))


# ----- Run as a script -----

if __name__ == "__main__":
    xml_folder = r"C:\Users\Y\AppData\Roaming\FreeWorship\Data\Songs"
    output_pdf = r"C:\Users\Y\Documents\songs_collection_report.pdf"

    converter = PDFConverter(xml_folder, output_pdf)
    converter.convert_all_xml_to_pdf()
