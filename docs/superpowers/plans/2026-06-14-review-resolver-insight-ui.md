# Review Resolver Insight UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the review resolver explain each issue, show the matched source/member options, and provide fast source-aware action buttons.

**Architecture:** Keep this as post-processing. Extend `song_repo_review.py` with issue metadata, source/member parsing, filtering helpers, and decision action labels. Update `song_repo_review_gui.py` to show a filterable issue list, source/member table, explanation panel, highlighted comparison panes, and direct action buttons. Preserve existing JSON/SQLite decision recording.

**Tech Stack:** Python standard library, `unittest`, `tkinter`/`ttk`, `difflib`.

---

### Task 1: Resolver Core Tests

**Files:**
- Modify: `tests/test_song_repo_review.py`

- [x] Add tests proving `04_lyric_match_title_different` is included and `07_unmatched_non_openlyrics` is excluded.
- [x] Add tests for issue explanations for `04`, `05`, `06`, and `08`.
- [x] Add tests that member rows are parsed from the `members` report string into source/title/path records.
- [x] Add tests for filtering by issue type, status, and search text.
- [x] Run `.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_review -v` and verify the new tests fail for missing helpers/behavior.

### Task 2: Resolver Core Implementation

**Files:**
- Modify: `song_repo_review.py`

- [x] Include `lyric_match_title_different` in review candidates and keep `unmatched_non_openlyrics` excluded.
- [x] Add `IssueExplanation`, `ReviewMember`, and helper functions:
  - `issue_explanation(candidate)`
  - `parse_members(text)`
  - `filter_candidates(candidates, issue_type, status, search)`
  - `available_actions(candidate)`
- [x] Add source/member detail records to `ReviewCandidate`.
- [x] Run `.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_review -v` and verify pass.

### Task 3: GUI Upgrade

**Files:**
- Modify: `song_repo_review_gui.py`

- [x] Add top filters: issue type dropdown, status dropdown, search box, clear button.
- [x] Add sortable issue list columns for type, title, creator, score, source count, status, reason.
- [x] Add an explanation panel with “Why this is here”, “What to check”, “Evidence”, and “Suggested action”.
- [x] Add a source/member table for all matched members.
- [x] Replace action dropdown with direct buttons:
  - `Use Selected Source`
  - `Use This Title`
  - `Keep Both/All`
  - `Mark Same Song`
  - `Keep Unresolved`
  - `Skip`
- [x] Highlight comparison text using tags for metadata lines, chord lines, matching lines, and source-only/export-only lines.
- [x] Run syntax check with temp pyc output and `tests.test_song_repo_gui`.

### Task 4: Verification

**Files:**
- No new files unless verification exposes defects.

- [x] Run `.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_review -v`.
- [x] Run `.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_gui -v`.
- [x] Run `.\.venv\Scripts\python.exe -m unittest tests.test_song_repo_builder -v`.
- [x] Probe real `Output3` to confirm candidate count and issue-type counts.
- [x] Commit with `feat: improve review resolver insight UI`.
