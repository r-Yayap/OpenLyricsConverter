import pathlib
import tempfile
import unittest
import xml.etree.ElementTree as ET

import song_repo_builder as builder
import song_repo_opensong as opensong


class ChordProToOpenSongTests(unittest.TestCase):
    def test_chordpro_metadata_and_chords_convert_to_opensong_xml(self):
        chordpro = """{title: Amazing Grace}
{author: John Newton}
{artist: Worship Team}
{key: G}
{comment: Verse 1}
[G]Amazing [C]grace
How [D]sweet the [G]sound
"""

        root = opensong.chordpro_text_to_opensong_root(chordpro, source_name="Amazing Grace.chopro")

        self.assertEqual(root.findtext("title"), "Amazing Grace")
        self.assertEqual(root.findtext("author"), "John Newton")
        self.assertEqual(root.findtext("key"), "G")
        self.assertEqual(root.findtext("user1"), "Converted from ChordPro")
        lyrics = root.findtext("lyrics") or ""
        self.assertIn("[V1]", lyrics)
        self.assertIn(".G       C", lyrics)
        self.assertIn(" Amazing grace", lyrics)
        self.assertIn(".    D         G", lyrics)
        self.assertIn(" How sweet the sound", lyrics)

    def test_convert_chordpro_output_tree_preserves_classification_folders(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = pathlib.Path(temp_dir) / "Output"
            clean_dir = out_dir / builder.CLASS_FOLDERS["clean_match"]
            review_dir = out_dir / builder.CLASS_FOLDERS["needs_review"]
            clean_dir.mkdir(parents=True)
            review_dir.mkdir(parents=True)
            (clean_dir / "Clean Song.chopro").write_text(
                "{title: Clean Song}\n[G]Clean lyric\n",
                encoding="utf-8",
            )
            (review_dir / "Review Song.chopro").write_text(
                "{title: Review Song}\nReview lyric\n",
                encoding="utf-8",
            )

            summary = opensong.convert_chordpro_output_to_opensong(out_dir)

            self.assertEqual(summary.converted_count, 2)
            self.assertEqual(summary.error_count, 0)
            export_dir = out_dir / "opensong_export"
            clean_export = export_dir / "01_clean_match_opensong" / "Clean Song.ost"
            review_export = export_dir / "08_needs_review_opensong" / "Review Song.ost"
            self.assertTrue(clean_export.exists())
            self.assertTrue(review_export.exists())
            self.assertTrue((out_dir / builder.CLASS_FOLDERS["clean_match"] / "Clean Song.chopro").exists())
            self.assertEqual(ET.parse(clean_export).getroot().findtext("title"), "Clean Song")

    def test_repeated_output_tree_conversion_overwrites_instead_of_duplicating(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = pathlib.Path(temp_dir) / "Output"
            clean_dir = out_dir / builder.CLASS_FOLDERS["clean_match"]
            clean_dir.mkdir(parents=True)
            (clean_dir / "Clean Song.chopro").write_text(
                "{title: Clean Song}\nFirst lyric\n",
                encoding="utf-8",
            )

            opensong.convert_chordpro_output_to_opensong(out_dir)
            (clean_dir / "Clean Song.chopro").write_text(
                "{title: Clean Song}\nSecond lyric\n",
                encoding="utf-8",
            )
            opensong.convert_chordpro_output_to_opensong(out_dir)

            export_dir = out_dir / "opensong_export" / "01_clean_match_opensong"
            files = sorted(path.name for path in export_dir.glob("*.ost"))
            self.assertEqual(files, ["Clean Song.ost"])
            lyrics = ET.parse(export_dir / "Clean Song.ost").getroot().findtext("lyrics") or ""
            self.assertIn("Second lyric", lyrics)
            self.assertNotIn("First lyric", lyrics)


if __name__ == "__main__":
    unittest.main()
