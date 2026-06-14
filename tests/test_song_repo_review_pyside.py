import os
import csv
import pathlib
import tempfile
import time
import unittest

import song_repo_builder as builder


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6 import QtWidgets
    import song_repo_review_pyside as review_pyside

    HAS_QT = True
except Exception:
    QtWidgets = None
    review_pyside = None
    HAS_QT = False


def write_csv(path, rows, fieldnames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class PySideReviewResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def pump_until(self, predicate, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.01)
        self.app.processEvents()
        return predicate()

    def make_output(self, temp_dir, count=1):
        out_dir = pathlib.Path(temp_dir)
        reports = out_dir / "reports"
        review_dir = out_dir / builder.CLASS_FOLDERS["needs_review"]
        review_dir.mkdir(parents=True, exist_ok=True)
        canonical_rows = []
        group_rows = []
        pair_rows = []
        for index in range(count):
            group_id = f"G{index + 1:05d}"
            title = f"Review Song {index + 1}"
            export_path = review_dir / f"{title}.chopro"
            source_a = out_dir / f"source_a_{index + 1}.onsong"
            source_b = out_dir / f"source_b_{index + 1}.onsong"
            export_path.write_text(f"{{title: {title}}}\nShared lyric {index + 1}\n", encoding="utf-8")
            source_a.write_text(f"Title: {title}\nArtist: Writer A\n[G]Shared lyric {index + 1}\n", encoding="utf-8")
            source_b.write_text(f"Title: {title}\nArtist: Writer B\n[C]Shared lyric {index + 1}\n", encoding="utf-8")
            canonical_rows.append(
                {
                    "group_id": group_id,
                    "classification": "needs_review",
                    "export_path": str(export_path),
                    "canonical_reason": "Possible lyric identity match below clean threshold",
                    "title": title,
                    "artist": "",
                    "author": "",
                    "source_repo": builder.ONSONG_REPO,
                    "source_format": "onsong",
                    "source_path": str(source_a),
                    "file_hash": f"hash-{index}-a",
                    "source_meta": "{}",
                    "has_chords": "1",
                    "chord_count": "1",
                    "line_count": "3",
                }
            )
            group_rows.append(
                {
                    "group_id": group_id,
                    "classification": "needs_review",
                    "classification_folder": builder.CLASS_FOLDERS["needs_review"],
                    "classification_reason": "Possible lyric identity match below clean threshold",
                    "member_count": "2",
                    "canonical_title": title,
                    "canonical_source_repo": builder.ONSONG_REPO,
                    "canonical_source_file": str(source_a),
                    "best_lyric_identity_score": "0.72",
                    "best_lyric_score": "0.70",
                    "best_line_coverage_max": "0.75",
                    "members": f"02_OnSong: {title} [{source_a}] || 02_OnSong: {title} [{source_b}]",
                }
            )
            pair_rows.append(
                {
                    "a_index": str(index * 2),
                    "b_index": str(index * 2 + 1),
                    "a_title": title,
                    "b_title": title,
                    "a_source": builder.ONSONG_REPO,
                    "b_source": builder.ONSONG_REPO,
                    "a_path": str(source_a),
                    "b_path": str(source_b),
                    "title_score": "1.0",
                    "lyric_score": "0.72",
                    "line_coverage_a_in_b": "0.75",
                    "line_coverage_b_in_a": "0.70",
                    "line_coverage_max": "0.75",
                    "line_coverage_min": "0.70",
                    "lyric_identity_score": "0.72",
                    "final_score": "0.72",
                    "shared_line_count": "1",
                    "decision_hint": "review",
                    "cache_hit": "0",
                }
            )
        write_csv(
            reports / "03_canonical_selection.csv",
            canonical_rows,
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
            group_rows,
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
            pair_rows,
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
        write_csv(reports / "08_same_title_different_lyrics.csv", [], ["title", "a_title", "b_title"])
        return out_dir

    def test_window_loads_empty_output_folder(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            window = review_pyside.ReviewResolverWindow(pathlib.Path(temp_dir))
            try:
                self.app.processEvents()
                self.assertEqual(window.issue_table.rowCount(), 0)
                self.assertIn("No review items", window.status_label.text())
            finally:
                window.close()
                self.app.processEvents()

    def test_realistic_fixture_loads_first_item_after_index_build(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = self.make_output(temp_dir)
            window = review_pyside.ReviewResolverWindow(out_dir)
            try:
                self.assertTrue(self.pump_until(lambda: window.current_diff is not None, timeout=6.0))
                self.assertEqual(window.issue_table.rowCount(), 1)
                self.assertEqual(window.current_candidate.group_id, "G00001")
                self.assertIn("Shared lyric 1", window.source_a_text.toPlainText())
                self.assertIn(window.current_pair.source_a.snapshot_source, {"parsed_source", "builder_cache"})
            finally:
                window.close()
                self.app.processEvents()

    def test_rapid_row_changes_only_render_latest_request(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = self.make_output(temp_dir, count=2)
            window = review_pyside.ReviewResolverWindow(out_dir)
            try:
                self.assertEqual(window.issue_table.rowCount(), 2)
                window.issue_table.selectRow(0)
                window.on_issue_selected()
                window.issue_table.selectRow(1)
                window.on_issue_selected()
                self.assertTrue(
                    self.pump_until(
                        lambda: window.current_candidate is not None and window.current_candidate.group_id == "G00002",
                        timeout=6.0,
                    )
                )
                self.assertIn("Review Song 2", window.issue_badge.text())
            finally:
                window.close()
                self.app.processEvents()

    def test_failed_detail_load_shows_error_and_writes_debug_log(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = self.make_output(temp_dir)
            window = review_pyside.ReviewResolverWindow(out_dir)
            try:
                if window._detail_token:
                    window._detail_token.cancel()

                def fail_detail(_candidate):
                    raise RuntimeError("forced detail failure")

                window.context.load_candidate_details = fail_detail
                window.issue_table.selectRow(0)
                window.on_issue_selected()
                self.assertTrue(
                    self.pump_until(lambda: "Could not load details" in window.status_label.text(), timeout=6.0)
                )
                log_text = (out_dir / "reports" / "review_resolver_debug.log").read_text(encoding="utf-8")
                self.assertIn("detail_worker_error", log_text)
                self.assertIn("forced detail failure", log_text)
            finally:
                window.close()
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
