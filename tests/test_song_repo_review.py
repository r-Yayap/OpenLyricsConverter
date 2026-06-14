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

    def make_candidate(self, classification, **overrides):
        values = {
            "group_id": f"G-{classification}",
            "classification": classification,
            "export_path": pathlib.Path(f"{classification}.chopro"),
            "title": "Review Song",
            "artist": "Artist Name",
            "author": "",
            "source_repo": builder.ONSONG_REPO,
            "source_format": "onsong",
            "source_path": "source.onsong",
            "file_hash": "hash-review",
            "canonical_reason": "Possible lyric identity match below clean threshold",
            "members": "02_OnSong: Review Song [source.onsong]",
            "best_lyric_identity_score": "0.72",
        }
        values.update(overrides)
        return review.ReviewCandidate(**values)

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

    def test_load_review_candidates_decodes_utf16_source_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir, _export_path = self.make_output(temp_dir)
            source_path = out_dir / "source.onsong"
            source_path.write_text("Title: Review Song\nReview lyric\n", encoding="utf-16")

            candidates = review.load_review_candidates(out_dir)

        self.assertEqual(len(candidates), 1)
        self.assertIn("Title: Review Song", candidates[0].source_text)

    def test_load_review_candidates_can_defer_text_and_details(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir, _export_path = self.make_output(temp_dir)

            candidates = review.load_review_candidates(out_dir, include_text=False, include_details=False)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].export_text, "")
            self.assertEqual(candidates[0].source_text, "")
            self.assertEqual(candidates[0].pair_details, [])

            detailed = review.load_candidate_details(out_dir, candidates[0])

        self.assertIn("Review lyric", detailed.export_text)
        self.assertIn("Title: Review Song", detailed.source_text)
        self.assertEqual(len(detailed.pair_details), 1)

    def test_review_candidates_include_lyric_title_diff_but_exclude_unmatched_non_openlyrics(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir, _export_path = self.make_output(temp_dir)
            reports = out_dir / "reports"
            canonical_path = reports / "03_canonical_selection.csv"
            group_path = reports / "02_match_groups.csv"
            with canonical_path.open("r", encoding="utf-8") as handle:
                canonical_rows = list(csv.DictReader(handle))
            with group_path.open("r", encoding="utf-8") as handle:
                group_rows = list(csv.DictReader(handle))

            lyric_title_export = (
                out_dir
                / builder.CLASS_FOLDERS["lyric_match_title_different"]
                / "Same Lyrics Different Title.chopro"
            )
            unmatched_export = (
                out_dir
                / builder.CLASS_FOLDERS["unmatched_non_openlyrics"]
                / "Standalone Chorded.chopro"
            )
            lyric_title_export.parent.mkdir(parents=True)
            unmatched_export.parent.mkdir(parents=True)
            lyric_title_export.write_text("{title: Same Lyrics Different Title}\nLyric\n", encoding="utf-8")
            unmatched_export.write_text("{title: Standalone Chorded}\nLyric\n", encoding="utf-8")

            source_path = out_dir / "source2.onsong"
            source_path.write_text("Title: Alternate Title\nLyric\n", encoding="utf-8")
            canonical_rows.append(
                {
                    **canonical_rows[0],
                    "group_id": "G00002",
                    "classification": "lyric_match_title_different",
                    "export_path": str(lyric_title_export),
                    "title": "Same Lyrics Different Title",
                    "source_path": str(source_path),
                    "file_hash": "hash-title-diff",
                }
            )
            canonical_rows.append(
                {
                    **canonical_rows[0],
                    "group_id": "G00003",
                    "classification": "unmatched_non_openlyrics",
                    "export_path": str(unmatched_export),
                    "title": "Standalone Chorded",
                    "source_path": str(source_path),
                    "file_hash": "hash-unmatched",
                }
            )
            group_rows.append(
                {
                    **group_rows[0],
                    "group_id": "G00002",
                    "classification": "lyric_match_title_different",
                    "classification_folder": builder.CLASS_FOLDERS["lyric_match_title_different"],
                    "classification_reason": "Lyrics match strongly but titles differ",
                    "canonical_title": "Same Lyrics Different Title",
                    "members": "01_OpenLyrics: Same Lyrics Different Title [source.xml] || "
                    "02_OnSong: Alternate Title [source2.onsong]",
                }
            )
            group_rows.append(
                {
                    **group_rows[0],
                    "group_id": "G00003",
                    "classification": "unmatched_non_openlyrics",
                    "classification_folder": builder.CLASS_FOLDERS["unmatched_non_openlyrics"],
                    "classification_reason": "Chorded source did not match an OpenLyrics source",
                    "canonical_title": "Standalone Chorded",
                    "members": "02_OnSong: Standalone Chorded [source2.onsong]",
                }
            )
            write_csv(canonical_path, canonical_rows, canonical_rows[0].keys())
            write_csv(group_path, group_rows, group_rows[0].keys())

            candidates = review.load_review_candidates(out_dir, include_text=False, include_details=False)

        classifications_by_group = {candidate.group_id: candidate.classification for candidate in candidates}
        self.assertEqual(classifications_by_group["G00002"], "lyric_match_title_different")
        self.assertNotIn("G00003", classifications_by_group)

    def test_issue_explanation_describes_review_reason_and_actions(self):
        expectations = {
            "lyric_match_title_different": ("titles differ", "Use This Title"),
            "title_match_lyrics_different": ("different lyrics", "Keep Both/All"),
            "multiple_chorded_sources": ("multiple chorded", "Use Selected Source"),
            "needs_review": ("below clean threshold", "Keep Unresolved"),
        }

        for classification, (why_text, action_text) in expectations.items():
            with self.subTest(classification=classification):
                candidate = self.make_candidate(classification)
                explanation = review.issue_explanation(candidate)

                self.assertIn(why_text, explanation.why.lower())
                self.assertTrue(explanation.what_to_check)
                self.assertTrue(explanation.evidence)
                self.assertIn(action_text, explanation.suggested_action)

    def test_parse_members_extracts_titles_sources_and_paths(self):
        members = review.parse_members(
            r"01_FreeWorship_OpenLyrics: Abide With Me [C:\Songs\a.xml] || "
            r"03_TXT_ChordPro: Above All [C:\Songs\b.txt]"
        )

        self.assertEqual(len(members), 2)
        self.assertEqual(members[0].source_repo, "01_FreeWorship_OpenLyrics")
        self.assertEqual(members[0].title, "Abide With Me")
        self.assertEqual(members[0].source_path, r"C:\Songs\a.xml")
        self.assertEqual(members[1].source_repo, "03_TXT_ChordPro")
        self.assertEqual(members[1].title, "Above All")

    def test_filter_candidates_filters_type_status_and_search(self):
        candidates = [
            self.make_candidate(
                "needs_review",
                group_id="G1",
                title="Celebrate Jesus",
                artist="Gary Oliver",
                status="unresolved",
            ),
            self.make_candidate(
                "multiple_chorded_sources",
                group_id="G2",
                title="Above All",
                author="Lenny LeBlanc",
                status="resolved",
            ),
        ]

        filtered = review.filter_candidates(candidates, "needs_review", "unresolved", "gary")
        self.assertEqual([candidate.group_id for candidate in filtered], ["G1"])

        filtered = review.filter_candidates(candidates, "multiple_chorded_sources", "resolved", "leblanc")
        self.assertEqual([candidate.group_id for candidate in filtered], ["G2"])

        filtered = review.filter_candidates(candidates, "all", "all", "missing")
        self.assertEqual(filtered, [])

    def test_available_actions_are_issue_specific_buttons(self):
        actions_by_class = {
            classification: {action["action"]: action["label"] for action in review.available_actions(self.make_candidate(classification))}
            for classification in (
                "lyric_match_title_different",
                "title_match_lyrics_different",
                "multiple_chorded_sources",
                "needs_review",
            )
        }

        self.assertIn("use_this_title", actions_by_class["lyric_match_title_different"])
        self.assertIn("keep_both", actions_by_class["lyric_match_title_different"])
        self.assertIn("retain_all", actions_by_class["title_match_lyrics_different"])
        self.assertIn("use_selected_source", actions_by_class["multiple_chorded_sources"])
        self.assertIn("keep_unresolved", actions_by_class["needs_review"])


if __name__ == "__main__":
    unittest.main()
