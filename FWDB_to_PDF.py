from xml.etree import ElementTree as ET
from reportlab.lib.pagesizes import A4
from reportlab.platypus import BaseDocTemplate, Paragraph, Spacer, Frame, PageTemplate
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import os
import re
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.platypus import NextPageTemplate, PageBreak

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
        self.title_space_before = 16  # Space before title of each new song
        self.title_space_after = 1    # Space after title

        self.author_font_size = 5
        self.author_font_name = 'Times-Roman'
        self.author_bold = False
        self.author_italic = True
        self.author_color = colors.darkgray
        self.author_space_after = 5  # Space after authors

        self.verse_name_font_size = 10
        self.verse_name_font_name = 'Courier'
        self.verse_name_bold = True
        self.verse_name_italic = False
        self.verse_name_color = colors.darkblue
        self.verse_name_space_after = 1  # Space after verse name

        self.body_text_font_size = 9
        self.body_text_font_name = 'Helvetica'
        self.body_text_bold = False
        self.body_text_italic = False
        self.body_text_color = colors.black
        self.body_text_space_after = 3  # Space after each line of body text
        self.line_spacing = 5  # Extra space between verses

    def parse_xml(self, xml_file_path):
        print(f"Parsing XML file: {xml_file_path}")
        try:
            # Attempt to read the file as UTF-16
            with open(xml_file_path, 'rb') as file:
                content = file.read().decode('utf-16')
        except UnicodeDecodeError:
            # If UTF-16 fails, fallback to UTF-8
            with open(xml_file_path, 'r', encoding='utf-8') as file:
                content = file.read()

        # Parse the XML content
        try:
            tree = ET.ElementTree(ET.fromstring(content))
            root = tree.getroot()
        except ET.ParseError as e:
            print(f"Error parsing XML: {e}")
            return None

        ns = {'': 'http://openlyrics.info/namespace/2009/song'}

        title = root.find('.//title', ns)
        title_text = title.text if title is not None else "Unknown Title"

        # Parse authors if available
        authors = [author.text if author.text is not None else "Unknown Author" for author in
                   root.findall('.//author', ns)]

        # Parse properties
        copyright_text = root.find('.//copyright', ns)
        copyright_text = copyright_text.text if copyright_text is not None else "Unknown Copyright"
        released = root.find('.//released', ns)
        released_text = released.text if released is not None else "Unknown Release Date"

        lyrics = root.find('.//lyrics', ns)
        verses_dict = {}

        if lyrics is not None:
            for verse in lyrics:
                verse_name = verse.attrib.get('name', 'Unnamed')
                lines = "\n".join([line.strip() for line in verse.itertext() if line.strip()]).replace('<br/>',
                                                                                                       '\n').strip()
                if not lines:  # Skip if lines are empty
                    continue

                if verse_name.startswith('c'):
                    standardized_name = "Chorus"
                elif verse_name.startswith('b'):
                    standardized_name = "Bridge"
                elif verse_name.startswith('e') and verse_name != "ei":
                    standardized_name = "Ending"
                elif verse_name == "ei":
                    standardized_name = None  # Include without label
                elif verse_name.startswith('v'):
                    verse_number_match = re.match(r'v(\d+)', verse_name, re.IGNORECASE)
                    standardized_name = f"Verse {verse_number_match.group(1)}" if verse_number_match else "Verse"
                elif verse_name.startswith('p') or verse_name.startswith('pa') or verse_name.startswith('pb'):
                    standardized_name = "Pre-Chorus"
                else:
                    standardized_name = verse_name

                if lines.strip():
                    if standardized_name not in verses_dict:
                        verses_dict[standardized_name] = lines
                    else:
                        verses_dict[standardized_name] += f"<br/><br/>{lines}"

        # Convert verses_dict to list format for consistent output
        verses = [{'name': verse_name, 'lines': verse_lines} for verse_name, verse_lines in verses_dict.items()]

        return {
            'title': title_text,
            'authors': authors,
            'copyright': copyright_text,
            'released': released_text,
            'verses': verses
        }

    def create_pdf(self, songs_data):
        # Create the document
        doc = MyDocTemplate(self.output_pdf_path, pagesize=A4, title="Songs Collection")

        # Page dimensions and margins
        page_width, page_height = A4
        left_margin = doc.leftMargin
        right_margin = doc.rightMargin
        top_margin = doc.topMargin
        bottom_margin = doc.bottomMargin
        usable_width = page_width - left_margin - right_margin
        usable_height = page_height - top_margin - bottom_margin

        # Define frames for TOC and content
        column_gap = 6  # Gap between columns
        toc_frame_width = (usable_width - 2 * column_gap) / 3
        frame_toc_left = Frame(left_margin, bottom_margin, toc_frame_width, usable_height, id='tocLeft')
        frame_toc_center = Frame(left_margin + toc_frame_width + column_gap, bottom_margin, toc_frame_width,
                                 usable_height, id='tocCenter')
        frame_toc_right = Frame(left_margin + 2 * (toc_frame_width + column_gap), bottom_margin, toc_frame_width,
                                usable_height, id='tocRight')

        content_frame_width = (usable_width - column_gap) / 2
        frame_left = Frame(left_margin, bottom_margin, content_frame_width, usable_height, id='leftColumn')
        frame_right = Frame(left_margin + content_frame_width + column_gap, bottom_margin, content_frame_width,
                            usable_height,
                            id='rightColumn')

        toc_template = PageTemplate(id='TOC', frames=[frame_toc_left, frame_toc_center, frame_toc_right])
        two_column_template = PageTemplate(id='TwoCol', frames=[frame_left, frame_right])

        doc.addPageTemplates([toc_template, two_column_template])

        styles = getSampleStyleSheet()

        # Define styles for title, author, verse name, and body text
        title_style = ParagraphStyle(
            name='Title',
            fontSize=self.title_font_size,
            leading=self.title_font_size + 2,
            textColor=self.title_color,
            spaceAfter=self.title_space_after,
            fontName=self.title_font_name if not self.title_bold else self.title_font_name + '-Bold',
            italic=self.title_italic
        )

        author_style = ParagraphStyle(
            name='Author',
            fontSize=self.author_font_size,
            leading=self.author_font_size + 2,
            textColor=self.author_color,
            spaceAfter=self.author_space_after,
            fontName=self.author_font_name if not self.author_bold else self.author_font_name + '-Bold',
            italic=self.author_italic
        )

        verse_name_style = ParagraphStyle(
            name='VerseName',
            fontSize=self.verse_name_font_size,
            leading=self.verse_name_font_size + 2,
            textColor=self.verse_name_color,
            spaceAfter=self.verse_name_space_after,
            fontName=self.verse_name_font_name if not self.verse_name_bold else self.verse_name_font_name + '-Bold',
            italic=self.verse_name_italic
        )

        body_text_style = ParagraphStyle(
            name='BodyText',
            fontSize=self.body_text_font_size,
            leading=self.body_text_font_size + 2,
            textColor=self.body_text_color,
            spaceAfter=self.body_text_space_after,
            fontName=self.body_text_font_name if not self.body_text_bold else self.body_text_font_name + '-Bold',
            italic=self.body_text_italic
        )

        toc = TableOfContents()
        toc.levelStyles = [
            ParagraphStyle(
                fontSize=7,
                name='TOCHeading1',
                leftIndent=2,
                firstLineIndent=-5,
                spaceBefore=2,
                leading=5,
                alignment=0
            ),
            ParagraphStyle(
                fontSize=7,
                name='TOCHeading2',
                leftIndent=2,
                firstLineIndent=-5,
                spaceBefore=2,
                leading=5,
                alignment=0
            ),
            ParagraphStyle(
                fontSize=7,
                name='TOCPageNumber',
                leftIndent=0,
                firstLineIndent=-5,
                spaceBefore=2,
                leading=5,
                alignment=2
            ),
        ]

        story = []

        story.append(Paragraph("Table of Contents", styles['Title']))
        story.append(Spacer(1, 12))
        story.append(toc)
        story.append(PageBreak())
        story.append(NextPageTemplate('TwoCol'))

        for song in songs_data:
            if not song:
                continue

            story.append(Spacer(1, self.title_space_before))

            p = Paragraph(song['title'], title_style)
            p.outlineLevel = 1  # Include in TOC
            story.append(p)
            story.append(Spacer(1, self.title_space_after))

            # Add TOC entry for the song title (this links it to the title in the document)
            toc.addEntry(1, song['title'], doc.current_page)  # Ensure this adds the entry to the TOC

            if song['authors']:
                authors_str = "Authors: " + ", ".join(song['authors'])
                story.append(Paragraph(authors_str, author_style))
                story.append(Spacer(1, self.author_space_after))

            for verse in song['verses']:
                if verse['name']:
                    story.append(Paragraph(verse['name'], verse_name_style))
                story.append(Paragraph(verse['lines'], body_text_style))
                story.append(Spacer(1, self.line_spacing))

        doc.multiBuild(story)
        print(f"PDF created with table of contents and two columns on content pages: {self.output_pdf_path}")

    def convert_all_xml_to_pdf(self):
        songs_data = []
        for file_name in os.listdir(self.xml_folder_path):
            if file_name.endswith('.xml'):
                file_path = os.path.join(self.xml_folder_path, file_name)
                print(f"Processing file: {file_name}")
                song_data = self.parse_xml(file_path)
                songs_data.append(song_data)

        self.create_pdf(songs_data)

# Place this at the module level, outside of any class or function
class MyDocTemplate(BaseDocTemplate):
    def __init__(self, filename, **kw):
        super().__init__(filename, **kw)
        self.allowSplitting = 1  # Enable flowable splitting
        self.addPageTemplates(kw.get('pageTemplates', []))

        # Adjust margins here
        self.leftMargin = 36  # 1-inch margin
        self.rightMargin = 36  # 1-inch margin
        self.topMargin = 72  # 1-inch margin
        self.bottomMargin = 72  # 1-inch margin

        self.current_page = 1  # Start with the first page

    def afterFlowable(self, flowable):
        if hasattr(flowable, 'outlineLevel'):
            text = flowable.getPlainText()
            self.notify('TOCEntry', (flowable.outlineLevel, text, self.current_page))

        # Increment the page number after each page's flowable is processed
        self.current_page = self.page

if __name__ == "__main__":
    xml_folder = r"C:\Users\Y\AppData\Roaming\FreeWorship\Data\Songs"
    output_pdf = r"C:\Users\Y\Documents\songs_collection_report.pdf"

    converter = PDFConverter(xml_folder, output_pdf)
    converter.convert_all_xml_to_pdf()