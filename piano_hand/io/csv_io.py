"""Editable fingering CSV import and export."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.io.project_files import atomic_write_text
from piano_hand.models import FingerSource, Hand, ScoreTimeline

FINGERING_COLUMNS = ("note_id", "hand", "finger")


@dataclass(frozen=True)
class FingeringOverride:
    note_id: str
    hand: Hand | None = None
    finger: int | None = None


def write_fingering_csv(timeline: ScoreTimeline, path: str | Path) -> Path:
    """Write the editable hand/finger fields in deterministic timeline order."""

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=FINGERING_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for note in timeline.sorted_notes():
        writer.writerow(
            {
                "note_id": note.id,
                "hand": note.hand.value,
                "finger": "" if note.finger is None else note.finger,
            }
        )
    return atomic_write_text(path, buffer.getvalue())


def read_fingering_csv(path: str | Path) -> dict[str, FingeringOverride]:
    """Read overrides keyed by note ID with row-specific validation messages."""

    source = Path(path)
    try:
        handle = source.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Cannot read fingering CSV {source}: {exc}",
            "Check timeline.fingering_overrides in project.yaml.",
        ) from exc

    overrides: dict[str, FingeringOverride] = {}
    with handle:
        reader = csv.DictReader(handle)
        missing = set(FINGERING_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise PianoHandError(
                ErrorCode.FINGERING_ERROR,
                f"Fingering CSV {source}:1 is missing columns: {', '.join(sorted(missing))}",
                f"Use columns: {', '.join(FINGERING_COLUMNS)}.",
            )
        for row_number, row in enumerate(reader, start=2):
            note_id = (row.get("note_id") or "").strip()
            if not note_id:
                raise _csv_error(source, row_number, "note_id is required")
            if note_id in overrides:
                raise _csv_error(source, row_number, f"duplicate note_id {note_id!r}")

            hand_text = (row.get("hand") or "").strip().lower()
            hand: Hand | None
            if not hand_text:
                hand = None
            else:
                try:
                    hand = Hand(hand_text)
                except ValueError as exc:
                    raise _csv_error(
                        source,
                        row_number,
                        f"hand must be left, right, unknown, or blank; got {hand_text!r}",
                    ) from exc

            finger_text = (row.get("finger") or "").strip()
            finger: int | None = None
            if finger_text:
                try:
                    finger = int(finger_text)
                except ValueError as exc:
                    raise _csv_error(
                        source, row_number, f"finger must be an integer; got {finger_text!r}"
                    ) from exc
                if finger not in range(1, 6):
                    raise _csv_error(source, row_number, "finger must be between 1 and 5")

            overrides[note_id] = FingeringOverride(note_id, hand, finger)
    return overrides


def apply_fingering_overrides(
    timeline: ScoreTimeline,
    overrides: dict[str, FingeringOverride],
) -> ScoreTimeline:
    """Return a timeline with non-blank CSV fields applied as manual values."""

    notes = []
    for note in timeline.notes:
        override = overrides.get(note.id)
        if override is None:
            notes.append(note)
            continue
        updates: dict[str, object] = {}
        if override.hand is not None:
            updates["hand"] = override.hand
            updates["hand_confidence"] = 1.0
        if override.finger is not None:
            updates["finger"] = override.finger
            updates["finger_source"] = FingerSource.MANUAL
            updates["finger_confidence"] = 1.0
        notes.append(note.model_copy(update=updates))
    return timeline.model_copy(update={"notes": notes})


def _csv_error(path: Path, row: int, message: str) -> PianoHandError:
    return PianoHandError(
        ErrorCode.FINGERING_ERROR,
        f"Invalid fingering CSV {path}:{row}: {message}",
        "Correct the reported CSV row.",
    )
