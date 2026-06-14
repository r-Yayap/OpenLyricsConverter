"""Post-processing converter from exported ChordPro files to OpenSong XML."""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import song_repo_builder as builder


DEFAULT_OPENSONG_EXPORT_FOLDER = "opensong_export"
DEFAULT_OPENSONG_EXTENSION = ".ost"
CHORDPRO_DIRECTIVE_RE = re.compile(r"^\s*\{([^:}]+)\s*:\s*(.*?)\s*\}\s*$")
INLINE_CHORD_RE = re.compile(r"\[([^\[\]]+)\]")


@dataclass
class ChordMarker:
    text: str
    offset: int


@dataclass
class ChordProLine:
    text: str
    chords: List[ChordMarker] = field(default_factory=list)


@dataclass
class ChordProSong:
    title: str = ""
    author: str = ""
    artist: str = ""
    key: str = ""
    copyright: str = ""
    tempo: str = ""
    time_signature: str = ""
    sections: List[tuple[Optional[str], List[ChordProLine]]] = field(default_factory=list)


@dataclass
class OpenSongConversionSummary:
    source_dir: Path
    output_dir: Path
    converted_count: int = 0
    error_count: int = 0
    errors: List[str] = field(default_factory=list)


def safe_filename(name: str) -> str:
    name = name or "Untitled"
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", name)
    name = name.replace(".", " ")
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(" .")
    return name[:120] or "Untitled"


def opensong_section_label(display_name: Optional[str]) -> Optional[str]:
    if not display_name:
        return None
    name = display_name.strip().lower()
    if name.startswith("verse"):
        match = re.search(r"\d+", name)
        return f"V{match.group(0)}" if match else "V"
    if name in {"v", "v1", "v2", "v3", "v4"}:
        return name.upper()
    if name.startswith("chorus") or name == "c":
        return "C"
    if name.startswith("bridge") or name == "b":
        return "B"
    if name.startswith("pre-chorus") or name.startswith("pre chorus") or name.startswith("prechorus"):
        return "P"
    if name.startswith("ending") or name.startswith("tag"):
        return "T"
    cleaned = re.sub(r"[^A-Za-z0-9]+", "", display_name)
    return cleaned or None


def parse_chordpro_line(line: str) -> ChordProLine:
    chords: List[ChordMarker] = []
    lyric_parts: List[str] = []
    cursor = 0
    for match in INLINE_CHORD_RE.finditer(line):
        lyric_parts.append(line[cursor : match.start()])
        chord = match.group(1).strip()
        if chord:
            chords.append(ChordMarker(chord, len("".join(lyric_parts))))
        cursor = match.end()
    lyric_parts.append(line[cursor:])
    return ChordProLine(text="".join(lyric_parts).rstrip(), chords=chords)


def parse_chordpro_text(text: str, source_name: str = "") -> ChordProSong:
    song = ChordProSong(title=builder.title_from_filename(Path(source_name)) if source_name else "")
    current_label: Optional[str] = None
    current_lines: List[ChordProLine] = []

    def flush_section() -> None:
        nonlocal current_lines
        if current_lines:
            song.sections.append((current_label, current_lines))
            current_lines = []

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        directive = CHORDPRO_DIRECTIVE_RE.match(stripped)
        if directive:
            name = directive.group(1).strip().lower()
            value = directive.group(2).strip()
            if name in {"title", "t"}:
                song.title = value
            elif name in {"author", "composer", "writer"}:
                song.author = value
            elif name == "artist":
                song.artist = value
            elif name in {"key", "key_original", "original_key"}:
                if not song.key:
                    song.key = builder.clean_metadata_value("key", value)
            elif name == "copyright":
                song.copyright = value
            elif name == "tempo":
                song.tempo = value
            elif name in {"time", "time_signature", "time_sig"}:
                song.time_signature = value
            elif name in {"comment", "c", "section"}:
                flush_section()
                current_label = value
            continue

        if not stripped:
            current_lines.append(ChordProLine(""))
            continue

        current_lines.append(parse_chordpro_line(raw_line))

    flush_section()
    if not song.title:
        song.title = "Untitled"
    return song


def opensong_chord_line(line: ChordProLine) -> str:
    lyric_text = line.text or ""
    chord_line = ["."] + [" "] * max(len(lyric_text), 1)
    for chord in sorted(line.chords, key=lambda item: item.offset):
        chord_text = chord.text.strip()
        if not chord_text:
            continue
        pos = max(1, int(chord.offset) + 1)
        while len(chord_line) < pos + len(chord_text):
            chord_line.append(" ")
        while any(
            index < len(chord_line) and chord_line[index] not in (" ", ".")
            for index in range(pos, pos + len(chord_text))
        ):
            pos += 1
            while len(chord_line) < pos + len(chord_text):
                chord_line.append(" ")
        for offset, char in enumerate(chord_text):
            chord_line[pos + offset] = char
    return "".join(chord_line).rstrip()


def chordpro_song_to_opensong_lyrics(song: ChordProSong) -> tuple[str, str]:
    rows: List[str] = []
    presentation_parts: List[str] = []
    last_label: Optional[str] = None
    unlabeled_started = False

    for raw_label, section_lines in song.sections:
        label = opensong_section_label(raw_label)
        if label and label != last_label:
            if rows and rows[-1] != "":
                rows.append("")
            rows.append(f"[{label}]")
            presentation_parts.append(label)
            last_label = label
        elif not label and not unlabeled_started:
            unlabeled_started = True

        for line in section_lines:
            if not line.text and not line.chords:
                if rows and rows[-1] != "":
                    rows.append("")
                continue
            if line.chords:
                rows.append(opensong_chord_line(line))
            if line.text:
                rows.append(" " + line.text.rstrip())

    return "\n".join(rows).rstrip(), " ".join(presentation_parts)


def chordpro_song_to_opensong_root(song: ChordProSong) -> ET.Element:
    lyrics_text, presentation = chordpro_song_to_opensong_lyrics(song)
    root = ET.Element("song")

    def add(tag: str, value: str = "", attrib: Optional[Dict[str, str]] = None) -> ET.Element:
        elem = ET.SubElement(root, tag, attrib or {})
        elem.text = value or ""
        return elem

    last_modified = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    author = song.author or song.artist
    add("uuid", str(uuid.uuid4()))
    add("last_modified", last_modified)
    add("title", song.title or "Untitled")
    add("author", author)
    add("copyright", song.copyright)
    add("presentation", presentation)
    add("hymn_number", "")
    add("capo", "", {"print": ""})
    add("tempo", song.tempo)
    add("time_sig", song.time_signature)
    add("duration", "")
    add("predelay", "")
    add("ccli", "")
    add("theme", "")
    add("alttheme", "")
    add("user1", "Converted from ChordPro")
    add("user2", "")
    add("user3", "")
    add("beatbuddysong", "")
    add("beatbuddykit", "")
    add("drummer", "")
    add("drummerkit", "")
    add("key", song.key)
    add("keyoriginal", "")
    add("aka", "")
    add("midi", "")
    add("midi_index", "")
    add("notes", "")
    add("lyrics", lyrics_text)
    add("pad_file", "")
    add("custom_chords", "")
    add("link_youtube", "")
    add("link_web", "")
    add("link_audio", "")
    add("loop_audio", "")
    add("link_other", "")
    add("abcnotation", "")
    add("abctranspose", "0")
    return root


def chordpro_text_to_opensong_root(text: str, source_name: str = "") -> ET.Element:
    return chordpro_song_to_opensong_root(parse_chordpro_text(text, source_name=source_name))


def output_folder_for_chordpro_file(source_dir: Path, output_dir: Path, chordpro_path: Path) -> Path:
    relative_parent = chordpro_path.parent.relative_to(source_dir)
    parts = [
        part[:-9] + "_opensong" if part.endswith("_chordpro") else part
        for part in relative_parent.parts
    ]
    return output_dir.joinpath(*parts) if parts else output_dir


def write_opensong_file(root: ET.Element, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="  ", level=0)
    except Exception:
        pass
    tree.write(output_path, encoding="utf-8", xml_declaration=True, short_empty_elements=False)


def convert_chordpro_file_to_opensong(
    chordpro_path: Path,
    output_dir: Path,
    extension: str = DEFAULT_OPENSONG_EXTENSION,
) -> Path:
    text = builder.safe_read_text(chordpro_path)
    root = chordpro_text_to_opensong_root(text, source_name=chordpro_path.name)
    title = root.findtext("title") or chordpro_path.stem
    output_path = builder.unique_path(output_dir, safe_filename(title), extension)
    write_opensong_file(root, output_path)
    return output_path


def convert_chordpro_output_to_opensong(
    source_dir: Path,
    output_dir: Optional[Path] = None,
    extension: str = DEFAULT_OPENSONG_EXTENSION,
) -> OpenSongConversionSummary:
    source_dir = Path(source_dir)
    output_dir = Path(output_dir) if output_dir else source_dir / DEFAULT_OPENSONG_EXPORT_FOLDER
    summary = OpenSongConversionSummary(source_dir=source_dir, output_dir=output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    used_names: Dict[tuple[Path, str], int] = {}

    for chordpro_path in sorted(source_dir.rglob("*.chopro"), key=lambda path: str(path).lower()):
        try:
            if output_dir in chordpro_path.parents:
                continue
            target_dir = output_folder_for_chordpro_file(source_dir, output_dir, chordpro_path)
            text = builder.safe_read_text(chordpro_path)
            root = chordpro_text_to_opensong_root(text, source_name=chordpro_path.name)
            title = root.findtext("title") or chordpro_path.stem
            safe_base = safe_filename(title)
            key = (target_dir, safe_base.casefold())
            count = used_names.get(key, 0) + 1
            used_names[key] = count
            stem = safe_base if count == 1 else f"{safe_base}_{count}"
            write_opensong_file(root, target_dir / f"{stem}{extension}")
            summary.converted_count += 1
        except Exception as exc:
            summary.error_count += 1
            summary.errors.append(f"{chordpro_path}: {type(exc).__name__}: {exc}")
    return summary
