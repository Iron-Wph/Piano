"""Checks that editable CSV rows address valid timeline notes."""

from __future__ import annotations

from piano_hand.io.csv_io import FingeringOverride
from piano_hand.models import Issue, IssueSeverity, ScoreTimeline


def check_fingering_overrides(
    timeline: ScoreTimeline, overrides: dict[str, FingeringOverride]
) -> list[Issue]:
    note_ids = {note.id for note in timeline.notes}
    issues: list[Issue] = []
    for note_id in overrides:
        if note_id not in note_ids:
            issues.append(
                Issue(
                    code="UNKNOWN_OVERRIDE_NOTE",
                    severity=IssueSeverity.ERROR,
                    message=f"fingering.csv refers to unknown note ID {note_id!r}.",
                    location=f"fingering.csv:{note_id}",
                    suggestion="Remove the row or restore the corresponding timeline note.",
                )
            )
    return issues
