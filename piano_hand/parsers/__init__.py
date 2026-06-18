"""Score parsing entry points."""

from __future__ import annotations

from pathlib import Path

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import ScoreTimeline


def parse_score(path: str | Path) -> ScoreTimeline:
    """Parse a supported score file into a normalized timeline."""

    source_path = Path(path)
    if not source_path.exists():
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Input score does not exist: {source_path}",
            "Check the file path and try again.",
        )
    if not source_path.is_file():
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Input score is not a file: {source_path}",
            "Provide a MIDI, MusicXML, or MXL file.",
        )

    suffix = source_path.suffix.lower()
    if suffix in {".mid", ".midi"}:
        from piano_hand.parsers.midi_parser import parse_midi

        return parse_midi(source_path)
    if suffix in {".musicxml", ".xml", ".mxl"}:
        from piano_hand.parsers.musicxml_parser import parse_musicxml

        return parse_musicxml(source_path)

    raise PianoHandError(
        ErrorCode.INPUT_ERROR,
        f"Unsupported score format for {source_path}",
        "Use a .mid, .midi, .musicxml, .xml, or .mxl file.",
    )


__all__ = ["parse_score"]
