import contextlib
import importlib.util
import io
import pathlib
import sys
import unittest
import warnings


ROOT = pathlib.Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "FWDB_to_PDF.py"
_MODULE = None


def load_module():
    global _MODULE
    if _MODULE is not None:
        return _MODULE

    spec = importlib.util.spec_from_file_location("fwdb_to_pdf", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"reportlab\..*")
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
    _MODULE = module
    return module


class ImportTests(unittest.TestCase):
    def test_module_imports(self):
        module = load_module()
        self.assertTrue(hasattr(module, "PDFConverter"))


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


class ScriptHandlingTests(unittest.TestCase):
    def test_hebrew_is_detected_as_rtl(self):
        module = load_module()
        self.assertTrue(module.is_rtl_text("\u05e9\u05dc\u05d5\u05dd"))

    def test_greek_and_cyrillic_are_not_rtl(self):
        module = load_module()
        self.assertFalse(module.is_rtl_text("\u03a7\u03c1\u03b9\u03c3\u03c4\u03cc\u03c2"))
        self.assertFalse(module.is_rtl_text("\u0413\u043e\u0441\u043f\u043e\u0434\u044c"))


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


if __name__ == "__main__":
    unittest.main()
