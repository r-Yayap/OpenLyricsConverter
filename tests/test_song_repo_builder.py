import pathlib
import json
import tempfile
import unittest
from argparse import Namespace

import song_repo_builder as builder


def make_song(index, title, lyric_lines, normalized_lyrics=None):
    plain_lyrics = "\n".join(lyric_lines)
    return builder.Song(
        index=index,
        source_repo=builder.OPENLYRICS_REPO,
        source_format="openlyrics",
        source_path=f"song-{index}.xml",
        file_hash=f"hash-{index}",
        size=0,
        mtime_ns=0,
        title=title,
        plain_lyrics=plain_lyrics,
        normalized_lyrics=normalized_lyrics or builder.normalize_lyric_text(plain_lyrics),
        lyric_lines=[builder.normalize_lyric_line(line) for line in lyric_lines],
    )


class OpenLyricsBuilderParsingTests(unittest.TestCase):
    def test_utf16_openlyrics_parses_as_xml(self):
        path = pathlib.Path("sample-songs") / "In This House ().xml"

        parsed = builder.parse_openlyrics(path)

        self.assertEqual(parsed["parse_status"], "ok")
        self.assertEqual(parsed["title"], "In This House")
        self.assertIn("IN THIS HOUSE THERE IS HOPE", parsed["plain_lyrics"])
        self.assertGreaterEqual(parsed["line_count"], 5)

    def test_openlyrics_br_elements_create_lyric_lines(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties><titles><title>Breaks</title></titles></properties>
  <lyrics><verse name="v1"><lines>Alpha line<br/>Beta line<br/>Gamma line</lines></verse></lyrics>
</song>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "breaks.xml"
            path.write_text(xml, encoding="utf-8")

            parsed = builder.parse_openlyrics(path)

        self.assertEqual(parsed["plain_lyrics"].splitlines(), ["Alpha line", "Beta line", "Gamma line"])
        self.assertEqual(parsed["lyric_lines"], ["alpha line", "beta line", "gamma line"])

    def test_openlyrics_chords_are_preserved_in_chordpro_body(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties><titles><title>Chorded</title></titles></properties>
  <lyrics><verse name="v1"><lines>I <chord name="G"/>come<br/>Ho<chord root="C" structure="min" bass="Eb">san</chord>na</lines></verse></lyrics>
</song>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "chorded.xml"
            path.write_text(xml, encoding="utf-8")

            parsed = builder.parse_openlyrics(path)

        self.assertEqual(parsed["chord_count"], 2)
        self.assertIn("I [G]come", parsed["chordpro_body"])
        self.assertIn("Ho[Cm/Eb]sanna", parsed["chordpro_body"])
        self.assertEqual(parsed["plain_lyrics"].splitlines(), ["I come", "Hosanna"])

    def test_openlyrics_author_exports_as_author_not_artist(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties>
    <titles><title>Author Song</title></titles>
    <authors><author>Jane Writer</author></authors>
  </properties>
  <lyrics><verse name="v1"><lines>Useful lyric line</lines></verse></lyrics>
</song>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "author.xml"
            path.write_text(xml, encoding="utf-8")

            parsed = builder.parse_openlyrics(path)
            song = builder.song_from_parsed(
                0,
                path,
                builder.OPENLYRICS_REPO,
                "openlyrics",
                "hash-author",
                path.stat().st_size,
                path.stat().st_mtime_ns,
                parsed,
            )
            exported = builder.build_export_text(song, "G00001", "test", [song])

        self.assertEqual(parsed["author"], "Jane Writer")
        self.assertEqual(parsed["artist"], "")
        self.assertIn("{author: Jane Writer}", exported)
        self.assertNotIn("{artist: Jane Writer}", exported)

    def test_openlyrics_artist_remains_artist(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties>
    <titles><title>Artist Song</title></titles>
    <artists><artist>Band Name</artist></artists>
  </properties>
  <lyrics><verse name="v1"><lines>Useful lyric line</lines></verse></lyrics>
</song>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "artist.xml"
            path.write_text(xml, encoding="utf-8")

            parsed = builder.parse_openlyrics(path)

        self.assertEqual(parsed["artist"], "Band Name")
        self.assertEqual(parsed["author"], "")


class PlainMetadataParsingTests(unittest.TestCase):
    def test_onsong_bare_headers_become_metadata_not_lyrics(self):
        text = """Title: Celebrate Jesus
Artist: Gary Oliver
Key: [F]
Original Key: F
Book: Camp 2022, Camp Fast, Fast
Notes: Use fast tempo
Scripture Reference(s): Psalm 100

Chorus:
Celebrate [F]Jesus celebrate
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = pathlib.Path(temp_dir) / "Celebrate Jesus.onsong"
            path.write_text(text, encoding="utf-8")

            parsed = builder.parse_plain_chordpro(path)

        self.assertEqual(parsed["title"], "Celebrate Jesus")
        self.assertEqual(parsed["artist"], "Gary Oliver")
        self.assertEqual(parsed["key"], "F")
        self.assertEqual(parsed["source_meta"]["original_key"], "F")
        self.assertEqual(parsed["source_meta"]["book"], "Camp 2022, Camp Fast, Fast")
        self.assertEqual(parsed["source_meta"]["notes"], "Use fast tempo")
        self.assertEqual(parsed["source_meta"]["scripture_references"], "Psalm 100")
        self.assertEqual(parsed["plain_lyrics"].splitlines(), ["Celebrate Jesus celebrate"])
        self.assertNotIn("Title:", parsed["chordpro_body"])
        self.assertNotIn("Artist:", parsed["chordpro_body"])
        self.assertIn("{comment: Chorus}", parsed["chordpro_body"])

    def test_export_includes_source_meta_without_bare_header_lines(self):
        song = builder.Song(
            index=0,
            source_repo=builder.ONSONG_REPO,
            source_format="onsong",
            source_path="Celebrate Jesus.onsong",
            file_hash="hash-celebrate",
            size=0,
            mtime_ns=0,
            title="Celebrate Jesus",
            artist="Gary Oliver",
            author="",
            key="F",
            plain_lyrics="Celebrate Jesus celebrate",
            normalized_lyrics=builder.normalize_lyric_text("Celebrate Jesus celebrate"),
            lyric_lines=["celebrate jesus celebrate"],
            chordpro_body="Title: Celebrate Jesus\nArtist: Gary Oliver\nCelebrate [F]Jesus celebrate",
            source_meta={"book": "Camp 2022", "original_key": "F"},
        )

        exported = builder.build_export_text(song, "G00001", "test", [song])

        self.assertIn("{title: Celebrate Jesus}", exported)
        self.assertIn("{artist: Gary Oliver}", exported)
        self.assertIn("{key: F}", exported)
        self.assertIn("{meta: book Camp 2022}", exported)
        self.assertIn("{meta: original_key F}", exported)
        self.assertNotIn("Title: Celebrate Jesus", exported)
        self.assertNotIn("Artist: Gary Oliver", exported)


class ExportFilenameTests(unittest.TestCase):
    def test_review_folders_use_title_and_artist_stem(self):
        song = builder.Song(
            index=0,
            source_repo=builder.ONSONG_REPO,
            source_format="onsong",
            source_path="source.onsong",
            file_hash="hash",
            size=0,
            mtime_ns=0,
            title="Celebrate Jesus",
            artist="Gary Oliver",
        )

        stem = builder.export_stem_for_classification(song, "title_match_lyrics_different")

        self.assertEqual(stem, "Celebrate Jesus - Gary Oliver")

    def test_review_folders_fallback_to_author(self):
        song = builder.Song(
            index=0,
            source_repo=builder.OPENLYRICS_REPO,
            source_format="openlyrics",
            source_path="source.xml",
            file_hash="hash",
            size=0,
            mtime_ns=0,
            title="In The Garden",
            author="C. Austin Miles",
        )

        stem = builder.export_stem_for_classification(song, "multiple_chorded_sources")

        self.assertEqual(stem, "In The Garden - C. Austin Miles")

    def test_non_review_folders_keep_title_only_stem(self):
        song = builder.Song(
            index=0,
            source_repo=builder.ONSONG_REPO,
            source_format="onsong",
            source_path="source.onsong",
            file_hash="hash",
            size=0,
            mtime_ns=0,
            title="Clean Title",
            artist="Artist Name",
        )

        stem = builder.export_stem_for_classification(song, "clean_match")

        self.assertEqual(stem, "Clean Title")


class MatchingPerformanceTests(unittest.TestCase):
    def test_line_coverage_does_not_fuzzy_score_exact_shared_lines(self):
        original = builder.line_match_score
        calls = 0

        def counting_score(line_a, line_b):
            nonlocal calls
            calls += 1
            return original(line_a, line_b)

        builder.line_match_score = counting_score
        try:
            coverage = builder.line_coverage(
                ["amazing grace how sweet"],
                ["amazing grace how sweet", "another useful line"],
                0.82,
            )
        finally:
            builder.line_match_score = original

        self.assertEqual(coverage, 1.0)
        self.assertEqual(calls, 0)

    def test_candidate_pairs_include_matching_lyric_shingles_across_line_wraps(self):
        songs = [
            make_song(
                0,
                "Alpha",
                ["amazing grace how sweet", "the sound that saved a wretch"],
            ),
            make_song(
                1,
                "Omega",
                ["amazing grace how", "sweet the sound that saved a wretch"],
            ),
        ]

        pairs = builder.generate_candidate_pairs(songs, max_line_bucket=250, max_title_bucket=500)

        self.assertIn((0, 1), pairs)

    def test_same_title_openlyrics_and_chorded_arrangement_auto_match_when_lines_are_split(self):
        openlyrics_path = pathlib.Path("sample-songs") / "10000 REASONS.xml"
        onsong_path = pathlib.Path("sample-songs") / "10000 Reasons.onsong"
        openlyrics = builder.song_from_parsed(
            0,
            openlyrics_path,
            builder.OPENLYRICS_REPO,
            "openlyrics",
            "openlyrics-hash",
            openlyrics_path.stat().st_size,
            openlyrics_path.stat().st_mtime_ns,
            builder.parse_openlyrics(openlyrics_path),
        )
        onsong = builder.song_from_parsed(
            1,
            onsong_path,
            builder.ONSONG_REPO,
            "onsong",
            "onsong-hash",
            onsong_path.stat().st_size,
            onsong_path.stat().st_mtime_ns,
            builder.parse_plain_chordpro(onsong_path),
        )

        score = builder.score_pair_raw(openlyrics, onsong, line_match_threshold=0.82)

        self.assertGreaterEqual(score["lyric_identity_score"], 0.86)

    def test_exact_normalized_lyrics_skip_fuzzy_scoring(self):
        original_sequence_ratio = builder.sequence_ratio
        original_line_match_score = builder.line_match_score
        calls = {"sequence": 0, "line": 0}

        def counting_sequence_ratio(a, b):
            calls["sequence"] += 1
            return original_sequence_ratio(a, b)

        def counting_line_match_score(a, b):
            calls["line"] += 1
            return original_line_match_score(a, b)

        a = make_song(0, "Title A", ["Amazing grace how sweet the sound"])
        b = make_song(1, "Title B", ["Amazing grace how sweet the sound"])
        builder.sequence_ratio = counting_sequence_ratio
        builder.line_match_score = counting_line_match_score
        try:
            score = builder.score_pair_raw(a, b, line_match_threshold=0.82)
        finally:
            builder.sequence_ratio = original_sequence_ratio
            builder.line_match_score = original_line_match_score

        self.assertEqual(score["lyric_identity_score"], 1.0)
        self.assertEqual(score["final_score"], 1.0)
        self.assertEqual(calls, {"sequence": 0, "line": 0})

    def test_song_precomputes_match_keys(self):
        song = make_song(0, "The Amazing Grace", ["Amazing grace how sweet the sound"])

        self.assertEqual(song.normalized_title, "the amazing grace")
        self.assertEqual(song.compact_title, "theamazinggrace")
        self.assertEqual(song.title_tokens, ["amazing", "grace"])
        self.assertIn("amazing grace how sweet the", song.lyric_shingles)


class FakeFuture:
    def __init__(self, value):
        self.value = value

    def result(self):
        return self.value


class RecordingExecutor:
    created = []

    def __init__(self, max_workers=None, initializer=None, initargs=()):
        self.max_workers = max_workers
        self.initializer = initializer
        self.initargs = initargs
        self.submissions = []
        RecordingExecutor.created.append(self)
        if initializer:
            initializer(*initargs)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, fn, *args):
        self.submissions.append((fn, args))
        return FakeFuture(fn(*args))


class MultiprocessingTests(unittest.TestCase):
    def setUp(self):
        RecordingExecutor.created = []
        self.original_executor = getattr(builder, "ProcessPoolExecutor", None)
        self.original_as_completed = getattr(builder, "as_completed", None)
        builder.ProcessPoolExecutor = RecordingExecutor
        builder.as_completed = lambda futures: list(futures)

    def tearDown(self):
        if self.original_executor is None:
            delattr(builder, "ProcessPoolExecutor")
        else:
            builder.ProcessPoolExecutor = self.original_executor
        if self.original_as_completed is None:
            delattr(builder, "as_completed")
        else:
            builder.as_completed = self.original_as_completed

    def test_parse_sources_uses_process_pool_for_uncached_files(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<song xmlns="http://openlyrics.info/namespace/2009/song" version="0.8">
  <properties><titles><title>Parallel Parse</title></titles></properties>
  <lyrics><verse name="v1"><lines>Alpha line</lines></verse></lyrics>
</song>"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            song_path = root / "parallel.xml"
            song_path.write_text(xml, encoding="utf-8")
            cache = builder.CacheDB(root / "cache.sqlite")
            try:
                songs, stats = builder.parse_sources(
                    [(song_path, builder.OPENLYRICS_REPO, "openlyrics")],
                    cache,
                    run_id="test-run",
                    verify_hashes=False,
                    force_reparse=False,
                    progress_every=0,
                    parse_workers=2,
                    parallel_parse_threshold=1,
                )
            finally:
                cache.close()

        self.assertEqual(songs[0].title, "Parallel Parse")
        self.assertEqual(stats["parsed_now"], 1)
        self.assertEqual(RecordingExecutor.created[0].max_workers, 2)
        self.assertEqual(len(RecordingExecutor.created[0].submissions), 1)

    def test_score_candidates_uses_process_pool_for_uncached_pairs(self):
        songs = [
            make_song(0, "Same", ["amazing grace how sweet the sound"]),
            make_song(1, "Same", ["amazing grace how sweet the sound"]),
        ]
        args = Namespace(
            line_match_threshold=0.82,
            force_rescore=False,
            progress_every=0,
            score_workers=2,
            parallel_score_threshold=1,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = builder.CacheDB(pathlib.Path(temp_dir) / "cache.sqlite")
            try:
                scores, stats = builder.score_candidates(
                    songs,
                    {(0, 1)},
                    cache,
                    settings_hash="settings",
                    args=args,
                )
            finally:
                cache.close()

        self.assertEqual(len(scores), 1)
        self.assertGreaterEqual(scores[0].lyric_identity_score, 0.86)
        self.assertEqual(stats["scored_now"], 1)
        self.assertEqual(RecordingExecutor.created[0].max_workers, 2)
        self.assertEqual(len(RecordingExecutor.created[0].submissions), 1)

    def test_score_candidates_batches_parallel_pair_work(self):
        songs = [
            make_song(0, "Same", ["amazing grace how sweet the sound"]),
            make_song(1, "Same", ["amazing grace how sweet the sound"]),
            make_song(2, "Same", ["amazing grace how sweet the sound"]),
            make_song(3, "Same", ["amazing grace how sweet the sound"]),
        ]
        args = Namespace(
            line_match_threshold=0.82,
            force_rescore=False,
            progress_every=0,
            score_workers=2,
            parallel_score_threshold=1,
            score_batch_size=2,
        )
        pairs = {(0, 1), (0, 2), (0, 3)}
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = builder.CacheDB(pathlib.Path(temp_dir) / "cache.sqlite")
            try:
                scores, stats = builder.score_candidates(
                    songs,
                    pairs,
                    cache,
                    settings_hash="settings",
                    args=args,
                )
            finally:
                cache.close()

        self.assertEqual(len(scores), 3)
        self.assertEqual(stats["scored_now"], 3)
        self.assertEqual(stats["score_batch_size"], 2)
        self.assertEqual(stats["score_batches_submitted"], 2)
        self.assertEqual(len(RecordingExecutor.created[0].submissions), 2)


class ReportingTests(unittest.TestCase):
    def test_export_summary_includes_phase_timings(self):
        songs = [make_song(0, "Solo", ["Only one useful lyric line"])]
        args = Namespace(
            out="",
            dry_run=True,
            clean_output=False,
            review_lyric_threshold=0.65,
            clean_lyric_threshold=0.96,
            auto_identity_threshold=0.86,
            line_match_threshold=0.82,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            args.out = temp_dir
            builder.export_results(
                songs,
                [[0]],
                [],
                args,
                {
                    "parse": {},
                    "score": {},
                    "phase_timings": {
                        "discovery_seconds": 0.01,
                        "parse_seconds": 0.02,
                        "candidate_seconds": 0.03,
                        "score_seconds": 0.04,
                        "group_seconds": 0.05,
                    },
                },
            )
            summary = json.loads((pathlib.Path(temp_dir) / "reports" / "run_summary.json").read_text())

        self.assertIn("phase_timings", summary)
        self.assertEqual(summary["phase_timings"]["score_seconds"], 0.04)


if __name__ == "__main__":
    unittest.main()
