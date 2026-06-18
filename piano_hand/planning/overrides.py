"""CSV loading and priority merge for manual hand/fingering overrides."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import FingerSource, Hand, ScoreTimeline
from piano_hand.planning.fingering_rules import raise_for_blocking_fingering_violations

_REQUIRED_COLUMNS = {"note_id", "hand", "finger"}


@dataclass(frozen=True, slots=True)
class FingeringOverride:
    note_id: str
    hand: Hand
    finger: int


def load_overrides_csv(path: str | Path) -> list[FingeringOverride]:
    """Load and validate a UTF-8 CSV with note_id, hand, and finger columns."""

    csv_path = Path(path)
    if not csv_path.is_file():
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Fingering override CSV does not exist: {csv_path}.",
            "Provide an existing readable CSV file.",
        )

    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = set(reader.fieldnames or ())
            missing = sorted(_REQUIRED_COLUMNS - headers)
            if missing:
                raise PianoHandError(
                    ErrorCode.FINGERING_ERROR,
                    f"Override CSV is missing required columns: {', '.join(missing)}.",
                    "Use the columns note_id, hand, and finger.",
                )

            overrides: list[FingeringOverride] = []
            seen: set[str] = set()
            for row_number, row in enumerate(reader, start=2):
                note_id = (row.get("note_id") or "").strip()
                hand_text = (row.get("hand") or "").strip().lower()
                finger_text = (row.get("finger") or "").strip()
                if not note_id:
                    raise _row_error(row_number, "note_id is empty")
                if note_id in seen:
                    raise _row_error(row_number, f"duplicate note_id {note_id!r}")
                seen.add(note_id)

                try:
                    hand = Hand(hand_text)
                except ValueError as exc:
                    raise _row_error(
                        row_number,
                        f"hand must be left or right, got {hand_text!r}",
                    ) from exc
                if hand == Hand.UNKNOWN:
                    raise _row_error(row_number, "hand must be left or right")

                try:
                    finger = int(finger_text)
                except ValueError as exc:
                    raise _row_error(
                        row_number,
                        f"finger must be an integer from 1 to 5, got {finger_text!r}",
                    ) from exc
                if not 1 <= finger <= 5:
                    raise _row_error(
                        row_number,
                        f"finger must be from 1 to 5, got {finger}",
                    )
                overrides.append(
                    FingeringOverride(note_id=note_id, hand=hand, finger=finger)
                )
    except UnicodeDecodeError as exc:
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Override CSV is not valid UTF-8: {csv_path}.",
            "Save the file as UTF-8 CSV and retry.",
        ) from exc
    except OSError as exc:
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Could not read fingering override CSV {csv_path}: {exc}.",
            "Check the file path and permissions.",
        ) from exc

    return overrides


def apply_overrides(
    timeline: ScoreTimeline,
    overrides: list[FingeringOverride],
    *,
    max_chord_span: int = 12,
) -> ScoreTimeline:
    """Merge manual overrides, then rerun blocking fingering checks."""

    notes_by_id = {note.id: note for note in timeline.notes}
    seen: set[str] = set()
    for override in overrides:
        _validate_override(override)
        if override.note_id in seen:
            raise PianoHandError(
                ErrorCode.FINGERING_ERROR,
                f"Duplicate manual override for note ID {override.note_id}.",
                "Keep exactly one CSV row per note ID.",
            )
        seen.add(override.note_id)
        note = notes_by_id.get(override.note_id)
        if note is None:
            raise PianoHandError(
                ErrorCode.FINGERING_ERROR,
                f"Manual override references unknown note ID {override.note_id}.",
                "Use a note_id from the generated timeline.",
            )
        notes_by_id[override.note_id] = note.model_copy(
            update={
                "hand": override.hand,
                "hand_confidence": 1.0,
                "finger": override.finger,
                "finger_source": FingerSource.MANUAL,
                "finger_confidence": 1.0,
                "explanation": [
                    *note.explanation,
                    (
                        f"Manual CSV override set {override.hand.value} hand "
                        f"finger {override.finger}."
                    ),
                ],
            }
        )

    result = timeline.model_copy(
        update={"notes": [notes_by_id[note.id] for note in timeline.notes]}
    )
    raise_for_blocking_fingering_violations(
        result.notes,
        max_chord_span=max_chord_span,
    )
    return result


def load_and_apply_overrides(
    timeline: ScoreTimeline,
    path: str | Path,
    *,
    max_chord_span: int = 12,
) -> ScoreTimeline:
    return apply_overrides(
        timeline,
        load_overrides_csv(path),
        max_chord_span=max_chord_span,
    )


def _validate_override(override: FingeringOverride) -> None:
    if not override.note_id:
        raise PianoHandError(
            ErrorCode.FINGERING_ERROR,
            "Manual override note_id is empty.",
            "Use a note_id from the generated timeline.",
        )
    if override.hand not in {Hand.LEFT, Hand.RIGHT}:
        raise PianoHandError(
            ErrorCode.FINGERING_ERROR,
            f"Manual override for {override.note_id} has invalid hand {override.hand!r}.",
            "Use left or right.",
        )
    if not isinstance(override.finger, int) or not 1 <= override.finger <= 5:
        raise PianoHandError(
            ErrorCode.FINGERING_ERROR,
            f"Manual override for {override.note_id} has invalid finger {override.finger!r}.",
            "Use an integer finger from 1 to 5.",
        )


def _row_error(row_number: int, reason: str) -> PianoHandError:
    return PianoHandError(
        ErrorCode.FINGERING_ERROR,
        f"Invalid override CSV row {row_number}: {reason}.",
        "Correct the row using a known note_id, hand left/right, and finger 1-5.",
    )
