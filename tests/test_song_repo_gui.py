import json
import pathlib
import tempfile
import unittest

import song_repo_gui
from song_repo_gui import DashboardSettings, build_builder_command, load_run_summary, performance_preset


class DashboardSettingsTests(unittest.TestCase):
    def test_build_command_includes_selected_sources_and_safety_flags(self):
        settings = DashboardSettings(
            openlyrics=pathlib.Path("C:/Songs/OpenLyrics"),
            onsong=None,
            txt=pathlib.Path("C:/Songs/Mixed Text"),
            output=pathlib.Path("C:/Songs/Output"),
            cache_dir=pathlib.Path("C:/Songs/Cache"),
            dry_run=True,
            clean_output=True,
            verify_hashes=True,
            force_reparse=True,
            force_rescore=True,
            parse_workers=6,
            score_workers=7,
            parallel_parse_threshold=12,
            parallel_score_threshold=34,
            score_batch_size=456,
            cache_write_batch_size=789,
            progress_every=25,
        )

        command = build_builder_command(
            settings,
            python_executable="python-test.exe",
            script_path=pathlib.Path("C:/App/song_repo_builder.py"),
        )

        self.assertEqual(command[:2], ["python-test.exe", str(pathlib.Path("C:/App/song_repo_builder.py"))])
        self.assertIn("--openlyrics", command)
        self.assertIn(str(pathlib.Path("C:/Songs/OpenLyrics")), command)
        self.assertNotIn("--onsong", command)
        self.assertIn("--txt", command)
        self.assertIn(str(pathlib.Path("C:/Songs/Mixed Text")), command)
        self.assertIn("--out", command)
        self.assertIn(str(pathlib.Path("C:/Songs/Output")), command)
        self.assertIn("--cache-dir", command)
        self.assertIn(str(pathlib.Path("C:/Songs/Cache")), command)
        for flag in ("--dry-run", "--clean-output", "--verify-hashes", "--force-reparse", "--force-rescore"):
            self.assertIn(flag, command)
        self.assertIn("--parse-workers", command)
        self.assertIn("6", command)
        self.assertIn("--score-workers", command)
        self.assertIn("7", command)
        self.assertIn("--score-batch-size", command)
        self.assertIn("456", command)

    def test_performance_presets_do_not_change_matching_thresholds(self):
        threshold_keys = {
            "auto_identity_threshold",
            "clean_lyric_threshold",
            "review_lyric_threshold",
            "line_match_threshold",
        }
        baseline = {key: performance_preset("Balanced")[key] for key in threshold_keys}

        for name in ("Balanced", "Max speed", "Careful audit"):
            preset = performance_preset(name)
            self.assertEqual({key: preset[key] for key in threshold_keys}, baseline)

        self.assertLess(
            performance_preset("Max speed")["parallel_score_threshold"],
            performance_preset("Balanced")["parallel_score_threshold"],
        )
        self.assertGreater(
            performance_preset("Max speed")["score_batch_size"],
            performance_preset("Balanced")["score_batch_size"],
        )

    def test_load_run_summary_reads_report_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            out_dir = pathlib.Path(temp_dir)
            reports = out_dir / "reports"
            reports.mkdir()
            expected = {
                "source_count": 3000,
                "group_count": 2910,
                "exported_count": 2910,
                "classification_counts": {"clean_match": 1800},
                "phase_timings": {"total_seconds": 123.4},
            }
            (reports / "run_summary.json").write_text(json.dumps(expected), encoding="utf-8")

            self.assertEqual(load_run_summary(out_dir), expected)

    def test_load_run_summary_returns_empty_dict_when_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(load_run_summary(pathlib.Path(temp_dir)), {})


class ReviewResolverLaunchTests(unittest.TestCase):
    def test_review_resolver_uses_selected_output_and_cache_folder(self):
        calls = []
        original = song_repo_gui.open_review_resolver
        try:
            song_repo_gui.open_review_resolver = lambda parent, output, cache_db_path=None: calls.append(
                (parent, output, cache_db_path)
            )
            with tempfile.TemporaryDirectory() as temp_dir:
                root = pathlib.Path(temp_dir)
                settings = DashboardSettings(
                    output=root / "out",
                    cache_dir=root / "cache",
                )

                song_repo_gui.open_review_resolver_for_settings(object(), settings)
        finally:
            song_repo_gui.open_review_resolver = original

        self.assertEqual(calls[0][1], root / "out")
        self.assertEqual(calls[0][2], root / "cache" / "song_repo_cache.sqlite")


if __name__ == "__main__":
    unittest.main()
