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
                    "canonical_source_file": str(source_path),
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
            candidate = review.load_review_candidates(out_dir)[0]

            decision = review.apply_review_decision(
                out_dir,
                candidate,
                chosen_classification="lyrics_only",
                action="move_to_lyrics_only",
                cache_db_path=db_path,
            )

            conn = sqlite3.connect(db_path)
            try:
                row = conn.execute(
                    "SELECT group_id, chosen_classification, final_export_path FROM manual_review_decisions"
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row[0], "G00001")
            self.assertEqual(row[1], "lyrics_only")
            self.assertEqual(row[2], str(decision.final_export_path))

    def test_missing_reports_return_empty_candidate_list(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(review.load_review_candidates(pathlib.Path(temp_dir)), [])

    def test_report_paths_that_include_output_folder_do_not_duplicate_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir, export_path = self.make_output(temp_dir)
            report_path = out_dir / "reports" / "03_canonical_selection.csv"
            with report_path.open("r", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["export_path"] = str(pathlib.Path(out_dir.name) / builder.CLASS_FOLDERS["needs_review"] / export_path.name)
            write_csv(report_path, rows, rows[0].keys())

            candidates = review.load_review_candidates(out_dir)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].export_path, export_path)
        self.assertIn("Review lyric", candidates[0].export_text)


if __name__ == "__main__":
    unittest.main()
