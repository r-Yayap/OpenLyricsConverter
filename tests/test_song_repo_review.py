import csv
import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

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

    def test_select_default_comparison_pair_prefers_same_title_conflict_row(self):
        candidate = self.make_candidate(
            "title_match_lyrics_different",
            title="At the Cross",
            members_list=[
                review.ReviewMember("01_FreeWorship_OpenLyrics", "At the Cross", r"C:\songs\a.xml"),
                review.ReviewMember("02_OnSong", "At The Cross", r"C:\songs\b.onsong"),
            ],
            conflict_details=[
                {
                    "a_title": "At the Cross",
                    "b_title": "At The Cross",
                    "a_source": "01_FreeWorship_OpenLyrics",
                    "b_source": "02_OnSong",
                    "a_path": r"C:\songs\a.xml",
                    "b_path": r"C:\songs\b.onsong",
                    "lyric_identity_score": "0.28103",
                    "title_score": "1.0",
                }
            ],
            pair_details=[
                {
                    "a_title": "At the Cross",
                    "b_title": "Other",
                    "a_source": "01_FreeWorship_OpenLyrics",
                    "b_source": "03_TXT_ChordPro",
                    "a_path": r"C:\songs\a.xml",
                    "b_path": r"C:\songs\c.txt",
                    "lyric_identity_score": "0.9",
                }
            ],
        )

        pair = review.select_default_comparison_pair(candidate)

        self.assertEqual(pair.source_a.source_path, r"C:\songs\a.xml")
        self.assertEqual(pair.source_b.source_path, r"C:\songs\b.onsong")
        self.assertIn("same title", pair.selection_reason.lower())
        self.assertFalse(pair.fallback_to_export)

    def test_load_member_text_resolves_relative_output_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = pathlib.Path(temp_dir)
            source_path = out_dir / "sources" / "song.onsong"
            source_path.parent.mkdir()
            source_path.write_text("Title: Review Song\nLyric line\n", encoding="utf-8")
            member = review.ReviewMember("02_OnSong", "Review Song", "sources/song.onsong")

            text = review.load_member_text(out_dir, member)

        self.assertIn("Lyric line", text)

    def test_compute_song_diff_marks_metadata_chords_and_missing_lines(self):
        diff = review.compute_song_diff(
            "{title: Alpha}\n[G]Shared lyric\nOnly A\n",
            "{title: Beta}\n[C]Shared lyric\nOnly B\n",
            "multiple_chorded_sources",
        )

        statuses = [line.status for line in diff.lines]
        self.assertIn("metadata", statuses)
        self.assertIn("chord_change", statuses)
        self.assertIn("a_only", statuses)
        self.assertIn("b_only", statuses)
        self.assertGreater(diff.chord_difference_count, 0)

    def test_available_issue_actions_are_specific_to_issue_type(self):
        actions_by_class = {
            classification: [action.action_id for action in review.available_issue_actions(self.make_candidate(classification))]
            for classification in (
                "lyric_match_title_different",
                "title_match_lyrics_different",
                "multiple_chorded_sources",
                "needs_review",
            )
        }

        self.assertIn("use_source_a_title", actions_by_class["lyric_match_title_different"])
        self.assertIn("use_source_b_lyrics", actions_by_class["title_match_lyrics_different"])
        self.assertIn("use_source_a_chords", actions_by_class["multiple_chorded_sources"])
        self.assertIn("split_not_same_song", actions_by_class["needs_review"])

    def test_apply_review_decision_saves_matched_source_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir, _export_path = self.make_output(temp_dir)
            candidate = review.load_review_candidates(out_dir)[0]
            source_a = review.ComparisonSource(
                marker="A",
                source_repo="01_FreeWorship_OpenLyrics",
                title="Review Song",
                source_path="a.xml",
            )
            source_b = review.ComparisonSource(
                marker="B",
                source_repo="02_OnSong",
                title="Review Song",
                source_path="b.onsong",
            )

            review.apply_review_decision(
                out_dir,
                candidate,
                chosen_classification="clean_match",
                action="use_source_a",
                note="manual note",
                action_label="Use Source A",
                source_a=source_a,
                source_b=source_b,
                chosen_source=source_a,
                decision_category="lyrics",
                auto_note="Used Source A.",
            )

            data = json.loads((out_dir / "reports" / "manual_review_decisions.json").read_text(encoding="utf-8"))
            saved = data["decisions"][0]
            self.assertEqual(saved["action_label"], "Use Source A")
            self.assertEqual(saved["source_a_title"], "Review Song")
            self.assertEqual(saved["source_b_path"], "b.onsong")
            self.assertEqual(saved["chosen_source_marker"], "A")
            self.assertEqual(saved["decision_category"], "lyrics")
            self.assertIn("manual note", saved["note"])

    def test_apply_review_decision_regenerates_output_from_different_chosen_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir, export_path = self.make_output(temp_dir)
            candidate = review.load_review_candidates(out_dir)[0]
            chosen_source = review.ComparisonSource(
                marker="B",
                source_repo=builder.ONSONG_REPO,
                title="Better Source Song",
                source_path=str(out_dir / "different.onsong"),
                text="{title: Better Source Song}\n{artist: Better Writer}\n\n[C]Better lyric\n",
            )

            decision = review.apply_review_decision(
                out_dir,
                candidate,
                chosen_classification="clean_match",
                action="use_source_b_lyrics",
                chosen_source=chosen_source,
                source_b=chosen_source,
                action_label="Use Source B Lyrics",
            )

            self.assertFalse(export_path.exists())
            self.assertIn(builder.CLASS_FOLDERS["clean_match"], str(decision.final_export_path))
            final_text = decision.final_export_path.read_text(encoding="utf-8")
            self.assertIn("{title: Better Source Song}", final_text)
            self.assertIn("[C]Better lyric", final_text)
            self.assertNotIn("Review lyric", final_text)

    def test_apply_review_decision_mark_same_song_moves_without_regenerating(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir, _export_path = self.make_output(temp_dir)
            candidate = review.load_review_candidates(out_dir)[0]
            source_a = review.ComparisonSource(
                marker="A",
                source_repo=builder.ONSONG_REPO,
                title="Different Source",
                source_path=str(out_dir / "different.onsong"),
                text="{title: Different Source}\nShould not replace export\n",
            )

            decision = review.apply_review_decision(
                out_dir,
                candidate,
                chosen_classification="clean_match",
                action="mark_same_song",
                source_a=source_a,
                action_label="Mark Same Song",
            )

            final_text = decision.final_export_path.read_text(encoding="utf-8")
            self.assertIn("{title: Review Song}", final_text)
            self.assertIn("Review lyric", final_text)
            self.assertNotIn("Should not replace export", final_text)

    def test_apply_review_decision_use_source_title_rewrites_title_directive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = pathlib.Path(temp_dir)
            export_dir = out_dir / builder.CLASS_FOLDERS["lyric_match_title_different"]
            export_dir.mkdir(parents=True)
            export_path = export_dir / "Current Title.chopro"
            export_path.write_text("{title: Current Title}\nSame lyric\n", encoding="utf-8")
            candidate = self.make_candidate(
                "lyric_match_title_different",
                export_path=export_path,
                title="Current Title",
                source_path=str(out_dir / "current.onsong"),
            )
            chosen_source = review.ComparisonSource(
                marker="B",
                source_repo=builder.ONSONG_REPO,
                title="Alternate Title",
                source_path=str(out_dir / "alternate.onsong"),
                text="{title: Wrong Internal Title}\nSame lyric\n",
            )

            decision = review.apply_review_decision(
                out_dir,
                candidate,
                chosen_classification="clean_match",
                action="use_source_b_title",
                chosen_source=chosen_source,
                source_b=chosen_source,
                action_label="Use Title from Source B",
            )

            final_text = decision.final_export_path.read_text(encoding="utf-8")
            self.assertIn("{title: Alternate Title}", final_text)
            self.assertNotIn("{title: Wrong Internal Title}", final_text)

    def test_resolver_context_indexes_pair_reports_without_rescanning_on_detail_load(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir, _export_path = self.make_output(temp_dir)
            context = review.ReviewResolverContext.open(out_dir)
            try:
                context.ensure_indexes()
                candidate = review.load_review_candidates(out_dir, include_text=False, include_details=False)[0]

                with mock.patch("song_repo_review.read_detail_rows_for_title", side_effect=AssertionError("rescanned csv")):
                    detailed = context.load_candidate_details(candidate)
            finally:
                context.close()

        self.assertEqual(len(detailed.pair_details), 1)
        self.assertEqual(detailed.pair_details[0]["a_title"], "Review Song")

    def test_resolver_context_source_snapshot_uses_builder_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = pathlib.Path(temp_dir)
            source_path = out_dir / "cached.onsong"
            source_path.write_text("raw should not be used", encoding="utf-8")
            db_path = out_dir / "song_repo_cache.sqlite"
            cache = builder.CacheDB(db_path)
            try:
                file_hash = "cache-hash"
                cache.conn.execute(
                    """
                    INSERT INTO source_files
                        (path, source_repo, source_format, size, mtime_ns, file_hash, last_seen_run, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (str(source_path), builder.ONSONG_REPO, "onsong", 1, 2, file_hash, "run", "now"),
                )
                cache.save_parsed_song(
                    file_hash,
                    {
                        "title": "Cached Song",
                        "artist": "Cached Artist",
                        "author": "",
                        "key": "G",
                        "tempo": "",
                        "time_signature": "",
                        "copyright": "",
                        "has_chords": True,
                        "chord_count": 1,
                        "line_count": 1,
                        "plain_lyrics": "Cached lyric",
                        "normalized_lyrics": "cached lyric",
                        "lyric_lines": ["cached lyric"],
                        "chordpro_body": "[G]Cached lyric",
                        "source_meta": {},
                        "parse_status": "ok",
                        "parse_error": "",
                    },
                )
                cache.conn.commit()
            finally:
                cache.close()
            context = review.ReviewResolverContext.open(out_dir, builder_cache_db_path=db_path)
            try:
                snapshot = context.load_source_snapshot(
                    review.ComparisonSource("A", builder.ONSONG_REPO, "Cached Song", str(source_path))
                )
            finally:
                context.close()

        self.assertTrue(snapshot.cache_hit)
        self.assertEqual(snapshot.snapshot_source, "builder_cache")
        self.assertIn("{title: Cached Song}", snapshot.text)
        self.assertIn("[G]Cached lyric", snapshot.text)
        self.assertNotIn("raw should not be used", snapshot.text)

    def test_resolver_context_source_snapshot_parses_when_builder_cache_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = pathlib.Path(temp_dir)
            source_path = out_dir / "uncached.onsong"
            source_path.write_text("Title: Parsed Song\nArtist: Writer\n[G]Parsed lyric\n", encoding="utf-8")
            context = review.ReviewResolverContext.open(out_dir)
            try:
                snapshot = context.load_source_snapshot(
                    review.ComparisonSource("A", builder.ONSONG_REPO, "Parsed Song", str(source_path))
                )
            finally:
                context.close()

        self.assertFalse(snapshot.cache_hit)
        self.assertEqual(snapshot.snapshot_source, "parsed_source")
        self.assertIn("{title: Parsed Song}", snapshot.text)
        self.assertIn("[G]Parsed lyric", snapshot.text)

    def test_resolver_context_diff_cache_reuses_saved_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = pathlib.Path(temp_dir)
            context = review.ReviewResolverContext.open(out_dir)
            pair = review.SelectedComparisonPair(
                source_a=review.ComparisonSource("A", "repo", "A", "a.txt", text="same\nA only\n", file_hash="ha"),
                source_b=review.ComparisonSource("B", "repo", "B", "b.txt", text="same\nB only\n", file_hash="hb"),
                selection_reason="test",
            )

            try:
                first = context.compute_or_load_diff(pair, "needs_review")
                with mock.patch("song_repo_review.compute_song_diff", side_effect=AssertionError("recomputed diff")):
                    second = context.compute_or_load_diff(pair, "needs_review")
            finally:
                context.close()

        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(first.result.summary, second.result.summary)

    def test_compute_song_diff_uses_simplified_mode_for_large_inputs(self):
        text_a = "\n".join(f"A line {index}" for index in range(40))
        text_b = "\n".join(f"B line {index}" for index in range(40))

        diff = review.compute_song_diff(text_a, text_b, "needs_review", max_full_lines=10, max_render_lines=12)

        self.assertTrue(diff.simplified)
        self.assertTrue(diff.truncated)
        self.assertLessEqual(len(diff.lines), 13)
        self.assertIn("simplified", diff.summary.lower())

    def test_review_debug_logger_writes_stage_and_exception_details(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = pathlib.Path(temp_dir)
            logger = review.ReviewDebugLogger(out_dir, echo=False)
            logger.log("selection_start", group_id="G1", title="Debug Song")
            try:
                raise RuntimeError("debug failure")
            except RuntimeError as exc:
                logger.exception("detail_load", exc, group_id="G1")

            text = (out_dir / "reports" / "review_resolver_debug.log").read_text(encoding="utf-8")

        self.assertIn("selection_start", text)
        self.assertIn("Debug Song", text)
        self.assertIn("detail_load", text)
        self.assertIn("RuntimeError", text)


if __name__ == "__main__":
    unittest.main()
