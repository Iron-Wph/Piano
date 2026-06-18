"""MusicXML and compressed MXL parser."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree
from xml.etree.ElementTree import Element

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import FingerSource, ScoreTimeline
from piano_hand.parsers.normalizer import RawNote, build_timeline

_STEP_TO_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_BEAT_UNIT_TO_QUARTERS = {
    "maxima": 32.0,
    "long": 16.0,
    "breve": 8.0,
    "whole": 4.0,
    "half": 2.0,
    "quarter": 1.0,
    "eighth": 0.5,
    "16th": 0.25,
    "32nd": 0.125,
    "64th": 0.0625,
}

MXL_MAX_ENTRIES = 256
MXL_MAX_ENTRY_UNCOMPRESSED_BYTES = 32 * 1024 * 1024
MXL_MAX_TOTAL_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MXL_MAX_CONTAINER_BYTES = 1024 * 1024
MXL_MAX_SCORE_BYTES = 32 * 1024 * 1024
MXL_MAX_COMPRESSION_RATIO = 100.0
MXL_READ_CHUNK_BYTES = 64 * 1024


def _local_name(element: Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _children(element: Element, name: str) -> list[Element]:
    return [child for child in element if _local_name(child) == name]


def _child(element: Element, name: str) -> Element | None:
    return next((child for child in element if _local_name(child) == name), None)


def _descendants(element: Element, name: str) -> list[Element]:
    return [candidate for candidate in element.iter() if _local_name(candidate) == name]


def _text(element: Element | None, default: str | None = None) -> str | None:
    if element is None or element.text is None:
        return default
    value = element.text.strip()
    return value if value else default


def _number(element: Element | None, default: float = 0.0) -> float:
    value = _text(element)
    return default if value is None else float(value)


def _measure_number(measure: Element, fallback: int) -> int:
    raw = measure.attrib.get("number", "")
    match = re.search(r"-?\d+", raw)
    if match is None:
        return max(1, fallback)
    return max(1, int(match.group()))


def _mxl_input_error(path: Path, detail: str) -> PianoHandError:
    return PianoHandError(
        ErrorCode.INPUT_ERROR,
        f"Unsafe or oversized MXL input {path}: {detail}",
        "Use an unencrypted MXL archive with bounded, safe member paths and sizes.",
    )


def _safe_mxl_member_name(path: Path, name: str) -> str:
    normalized = name.replace("\\", "/")
    path_text = normalized[:-1] if normalized.endswith("/") else normalized
    parts = path_text.split("/")
    if (
        not path_text
        or "\x00" in normalized
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or (parts and re.fullmatch(r"[A-Za-z]:", parts[0]) is not None)
    ):
        raise _mxl_input_error(path, f"unsafe archive member path: {name!r}")
    safe_path = PurePosixPath(path_text)
    if safe_path.is_absolute() or ".." in safe_path.parts:
        raise _mxl_input_error(path, f"unsafe archive member path: {name!r}")
    return safe_path.as_posix()


def _validate_mxl_archive(
    path: Path,
    archive: zipfile.ZipFile,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MXL_MAX_ENTRIES:
        raise _mxl_input_error(
            path,
            f"archive has {len(infos)} entries; limit is {MXL_MAX_ENTRIES}",
        )

    members: dict[str, zipfile.ZipInfo] = {}
    total_uncompressed = 0
    for info in infos:
        safe_name = _safe_mxl_member_name(path, info.filename)
        if safe_name in members:
            raise _mxl_input_error(path, f"duplicate archive member path: {safe_name!r}")
        members[safe_name] = info

        if info.flag_bits & 0x1:
            raise _mxl_input_error(path, f"encrypted archive member: {info.filename!r}")
        if info.file_size < 0 or info.compress_size < 0:
            raise _mxl_input_error(path, f"invalid archive member size: {info.filename!r}")
        if info.file_size > MXL_MAX_ENTRY_UNCOMPRESSED_BYTES:
            raise _mxl_input_error(
                path,
                (
                    f"archive member {info.filename!r} declares {info.file_size} bytes; "
                    f"per-entry limit is {MXL_MAX_ENTRY_UNCOMPRESSED_BYTES}"
                ),
            )

        total_uncompressed += info.file_size
        if total_uncompressed > MXL_MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise _mxl_input_error(
                path,
                (
                    f"archive declares more than "
                    f"{MXL_MAX_TOTAL_UNCOMPRESSED_BYTES} uncompressed bytes"
                ),
            )
        if info.file_size > 0:
            if info.compress_size == 0:
                raise _mxl_input_error(
                    path,
                    f"archive member {info.filename!r} has an invalid compression ratio",
                )
            compression_ratio = info.file_size / info.compress_size
            if compression_ratio > MXL_MAX_COMPRESSION_RATIO:
                raise _mxl_input_error(
                    path,
                    (
                        f"archive member {info.filename!r} has compression ratio "
                        f"{compression_ratio:.1f}; limit is {MXL_MAX_COMPRESSION_RATIO:.1f}"
                    ),
                )
    return members


def _read_mxl_member(
    path: Path,
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    max_bytes: int,
) -> bytes:
    if info.file_size > max_bytes:
        raise _mxl_input_error(
            path,
            (
                f"archive member {info.filename!r} declares {info.file_size} bytes; "
                f"read limit is {max_bytes}"
            ),
        )

    data = bytearray()
    with archive.open(info, "r") as source:
        while True:
            remaining_with_sentinel = max_bytes - len(data) + 1
            chunk = source.read(min(MXL_READ_CHUNK_BYTES, remaining_with_sentinel))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise _mxl_input_error(
                    path,
                    (
                        f"archive member {info.filename!r} expands beyond "
                        f"the {max_bytes}-byte read limit"
                    ),
                )
    return bytes(data)


def _read_xml_bytes(path: Path) -> bytes:
    if path.suffix.lower() != ".mxl":
        return path.read_bytes()

    try:
        with zipfile.ZipFile(path) as archive:
            members = _validate_mxl_archive(path, archive)
            root_name: str | None = None
            container_info = next(
                (
                    info
                    for name, info in members.items()
                    if name.lower() == "meta-inf/container.xml"
                ),
                None,
            )
            container_data = (
                _read_mxl_member(
                    path,
                    archive,
                    container_info,
                    max_bytes=MXL_MAX_CONTAINER_BYTES,
                )
                if container_info is not None
                else b""
            )
            if container_data:
                container = ElementTree.fromstring(container_data)
                rootfile = next(
                    (
                        item
                        for item in container.iter()
                        if _local_name(item) == "rootfile" and item.attrib.get("full-path")
                    ),
                    None,
                )
                if rootfile is not None:
                    root_name = _safe_mxl_member_name(
                        path,
                        rootfile.attrib["full-path"],
                    )

            if root_name is None:
                root_name = next(
                    (
                        name
                        for name, info in members.items()
                        if not info.is_dir()
                        if name.lower().endswith((".musicxml", ".xml"))
                        and name.lower() != "meta-inf/container.xml"
                    ),
                    None,
                )
            if root_name is None:
                raise ValueError("MXL archive contains no MusicXML score")

            root_info = members.get(root_name)
            if root_info is None or root_info.is_dir():
                raise ValueError(f"MXL rootfile does not exist: {root_name}")
            return _read_mxl_member(
                path,
                archive,
                root_info,
                max_bytes=MXL_MAX_SCORE_BYTES,
            )
    except PianoHandError:
        raise
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        ElementTree.ParseError,
        KeyError,
        NotImplementedError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        raise PianoHandError(
            ErrorCode.PARSE_ERROR,
            f"Failed to read compressed MusicXML file {path}: {exc}",
            "Verify that the MXL archive and META-INF/container.xml are valid.",
        ) from exc


def _parse_pitch(note: Element) -> int | None:
    pitch = _child(note, "pitch")
    if pitch is None:
        return None
    step = (_text(_child(pitch, "step"), "") or "").upper()
    if step not in _STEP_TO_SEMITONE:
        raise ValueError(f"invalid pitch step: {step!r}")
    alter = _number(_child(pitch, "alter"), 0.0)
    octave = int(_number(_child(pitch, "octave")))
    midi_pitch = int(round((octave + 1) * 12 + _STEP_TO_SEMITONE[step] + alter))
    if not 0 <= midi_pitch <= 127:
        raise ValueError(f"pitch is outside MIDI range: {midi_pitch}")
    return midi_pitch


def _parse_fingering(note: Element) -> int | None:
    for fingering in _descendants(note, "fingering"):
        value = _text(fingering)
        if value is None:
            continue
        match = re.search(r"[1-5]", value)
        if match:
            return int(match.group())
    return None


def _tie_types(note: Element) -> set[str]:
    values = {
        item.attrib.get("type", "")
        for item in [*_children(note, "tie"), *_descendants(note, "tied")]
    }
    return {value for value in values if value in {"start", "stop"}}


def _parse_direction_tempo(direction: Element) -> float | None:
    sound = _child(direction, "sound")
    if sound is not None and sound.attrib.get("tempo"):
        bpm = float(sound.attrib["tempo"])
        return bpm if bpm > 0 else None

    metronomes = _descendants(direction, "metronome")
    if not metronomes:
        return None
    metronome = metronomes[0]
    per_minute = _number(_child(metronome, "per-minute"), 0.0)
    beat_unit = (_text(_child(metronome, "beat-unit"), "quarter") or "quarter").lower()
    factor = _BEAT_UNIT_TO_QUARTERS.get(beat_unit, 1.0)
    dot_count = len(_children(metronome, "beat-unit-dot"))
    if dot_count:
        factor *= sum(0.5**index for index in range(dot_count + 1))
    bpm = per_minute * factor
    return bpm if bpm > 0 else None


def _parse_time_signature(time: Element) -> tuple[int, int] | None:
    beats_text = _text(_child(time, "beats"))
    beat_type_text = _text(_child(time, "beat-type"))
    if beats_text is None or beat_type_text is None:
        return None
    numerator = sum(int(part) for part in beats_text.split("+"))
    denominator = int(beat_type_text)
    if numerator <= 0 or denominator <= 0:
        return None
    return numerator, denominator


def _parse_root(root: Element, path: Path) -> ScoreTimeline:
    if _local_name(root) != "score-partwise":
        raise ValueError("only score-partwise MusicXML is supported")

    parts = _children(root, "part")
    if not parts:
        raise ValueError("score contains no parts")

    raw_notes: list[RawNote] = []
    tempo_changes: list[tuple[float, float]] = []
    time_signatures: list[tuple[int, int, int]] = []
    pending_warnings: list[str] = []
    tie_open: dict[tuple[int, int, str, int], int] = {}
    known_measure_starts: dict[int, float] = {}

    for part_index, part in enumerate(parts):
        measure_start = 0.0
        divisions = 1.0
        current_meter = (4, 4)

        for measure_index, measure in enumerate(_children(part, "measure"), start=1):
            measure_number = _measure_number(measure, measure_index)
            if measure_number in known_measure_starts:
                measure_start = known_measure_starts[measure_number]
            else:
                known_measure_starts[measure_number] = measure_start

            cursor = 0.0
            max_cursor = 0.0
            last_onset: dict[tuple[str, int], float] = {}

            for item in measure:
                item_name = _local_name(item)
                if item_name == "attributes":
                    divisions_element = _child(item, "divisions")
                    if divisions_element is not None:
                        divisions = _number(divisions_element)
                        if divisions <= 0:
                            raise ValueError(
                                f"measure {measure_number} has non-positive divisions"
                            )
                    for time in _children(item, "time"):
                        signature = _parse_time_signature(time)
                        if signature is not None:
                            current_meter = signature
                            time_signatures.append((measure_number, *signature))
                    continue

                if item_name == "direction":
                    bpm = _parse_direction_tempo(item)
                    if bpm is not None:
                        offset = _number(_child(item, "offset"), 0.0) / divisions
                        tempo_changes.append((max(0.0, measure_start + cursor + offset), bpm))
                    continue

                if item_name in {"backup", "forward"}:
                    duration = _number(_child(item, "duration"), 0.0) / divisions
                    if item_name == "backup":
                        cursor = max(0.0, cursor - duration)
                    else:
                        cursor += duration
                        max_cursor = max(max_cursor, cursor)
                    continue

                if item_name != "note":
                    continue

                voice = _text(_child(item, "voice"), "1") or "1"
                staff = max(1, int(_number(_child(item, "staff"), 1.0)))
                duration_element = _child(item, "duration")
                is_grace = _child(item, "grace") is not None
                if duration_element is None or _number(duration_element) <= 0:
                    warning_kind = "grace note" if is_grace else "zero-duration note"
                    pending_warnings.append(
                        f"warning: {warning_kind} ignored in measure {measure_number}"
                    )
                    continue

                duration = _number(duration_element) / divisions
                chord = _child(item, "chord") is not None
                position_key = (voice, staff)
                if chord:
                    onset_in_measure = last_onset.get(position_key, cursor)
                else:
                    onset_in_measure = cursor
                    last_onset[position_key] = onset_in_measure
                    cursor += duration
                max_cursor = max(max_cursor, onset_in_measure + duration, cursor)

                if _child(item, "rest") is not None:
                    continue
                pitch = _parse_pitch(item)
                if pitch is None:
                    pending_warnings.append(
                        f"warning: unpitched note ignored in measure {measure_number}"
                    )
                    continue

                onset_beat = measure_start + onset_in_measure
                fingering = _parse_fingering(item)
                ties = _tie_types(item)
                explanations: list[str] = []
                if _descendants(item, "ornaments"):
                    explanations.append(
                        "warning: MusicXML ornament retained as a simplified written note"
                    )
                if pending_warnings:
                    explanations.extend(pending_warnings)
                    pending_warnings.clear()

                tie_key = (part_index, staff, voice, pitch)
                if "stop" in ties and tie_key in tie_open:
                    original = raw_notes[tie_open[tie_key]]
                    original.duration_beat = max(
                        original.duration_beat,
                        onset_beat + duration - original.onset_beat,
                    )
                    original.explanation.extend(explanations)
                    if original.finger is None and fingering is not None:
                        original.finger = fingering
                        original.finger_source = FingerSource.SCORE
                        original.finger_confidence = 1.0
                    if "start" not in ties:
                        tie_open.pop(tie_key, None)
                    continue

                if "stop" in ties:
                    explanations.append("warning: unmatched MusicXML tie stop")
                raw = RawNote(
                    pitch=pitch,
                    onset_beat=onset_beat,
                    duration_beat=duration,
                    measure=measure_number,
                    voice=voice,
                    staff=staff,
                    track=part_index,
                    finger=fingering,
                    finger_source=(
                        FingerSource.SCORE if fingering is not None else FingerSource.UNKNOWN
                    ),
                    finger_confidence=1.0 if fingering is not None else 0.0,
                    explanation=explanations,
                )
                raw_notes.append(raw)
                if "start" in ties:
                    if tie_key in tie_open:
                        raw.explanation.append("warning: overlapping MusicXML tie start")
                    tie_open[tie_key] = len(raw_notes) - 1

            expected_duration = current_meter[0] * 4.0 / current_meter[1]
            measure_start += max_cursor if max_cursor > 0 else expected_duration

    for note_index in tie_open.values():
        raw_notes[note_index].explanation.append("warning: unclosed MusicXML tie")
    if pending_warnings and raw_notes:
        raw_notes[0].explanation.extend(pending_warnings)
    if not raw_notes:
        raise ValueError("score contains no supported pitched notes")

    return build_timeline(
        path=path,
        source_type="musicxml",
        raw_notes=raw_notes,
        tempo_changes=tempo_changes,
        time_signatures=time_signatures,
    )


def parse_musicxml(path: str | Path) -> ScoreTimeline:
    """Parse .musicxml, .xml, or .mxl without mutating the source file."""

    source_path = Path(path)
    if not source_path.is_file():
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"MusicXML input does not exist or is not a file: {source_path}",
            "Provide an existing .musicxml, .xml, or .mxl file.",
        )
    if source_path.suffix.lower() not in {".musicxml", ".xml", ".mxl"}:
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Unsupported MusicXML extension: {source_path.suffix}",
            "Use .musicxml, .xml, or .mxl.",
        )

    try:
        xml_data = _read_xml_bytes(source_path)
        root = ElementTree.fromstring(xml_data)
        return _parse_root(root, source_path)
    except PianoHandError:
        raise
    except (ElementTree.ParseError, OSError, ValueError, TypeError, OverflowError) as exc:
        raise PianoHandError(
            ErrorCode.PARSE_ERROR,
            f"Failed to parse MusicXML file {source_path}: {exc}",
            "Verify divisions, note durations, pitches, and XML structure.",
        ) from exc


__all__ = ["parse_musicxml"]
