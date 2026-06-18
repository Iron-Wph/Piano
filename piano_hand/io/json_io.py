"""JSON serialization for timelines and reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.io.project_files import atomic_write_text
from piano_hand.models import ScoreTimeline


def write_json(data: Any, path: str | Path) -> Path:
    """Write JSON deterministically and atomically."""

    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    return atomic_write_text(path, text)


def write_timeline_json(timeline: ScoreTimeline, path: str | Path) -> Path:
    return write_json(timeline, path)


def read_timeline_json(path: str | Path) -> ScoreTimeline:
    """Read a timeline and report JSON/Pydantic locations in errors."""

    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Cannot read timeline JSON {source}: {exc}",
            "Check timeline.path in project.yaml.",
        ) from exc
    except json.JSONDecodeError as exc:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Invalid timeline JSON {source}:{exc.lineno}:{exc.colno}: {exc.msg}",
            "Fix the JSON syntax at the reported location.",
        ) from exc

    try:
        return ScoreTimeline.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Invalid timeline data {source}: {details}",
            "Correct the listed timeline fields.",
        ) from exc
