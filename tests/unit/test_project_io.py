from __future__ import annotations

from pathlib import Path

import pytest

from piano_hand.config import (
    load_project_config,
    load_resolved_project,
    save_project_config,
)
from piano_hand.errors import PianoHandError
from piano_hand.io import (
    prepare_project_directory,
    read_fingering_csv,
    read_timeline_json,
    write_fingering_csv,
    write_timeline_json,
)
from piano_hand.models import (
    FingerSource,
    Hand,
    InputConfig,
    NoteEvent,
    ProjectConfig,
    ScoreSource,
    ScoreTimeline,
)


def make_timeline() -> ScoreTimeline:
    return ScoreTimeline(
        source=ScoreSource(path="source.mid", type="midi", sha256="a" * 64),
        notes=[
            NoteEvent(
                id="n1",
                pitch=60,
                pitch_name="C4",
                onset_beat=0,
                duration_beat=1,
                onset_sec=0,
                duration_sec=0.5,
                measure=1,
                hand=Hand.RIGHT,
                hand_confidence=0.9,
                finger=1,
                finger_source=FingerSource.GENERATED,
                finger_confidence=0.8,
            )
        ],
        duration_sec=0.5,
    )


def test_project_config_round_trip_and_relative_paths(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    config = ProjectConfig(input=InputConfig(path="./source.mid", type="midi"))

    project_file = save_project_config(config, project_dir)
    loaded = load_project_config(project_file)
    resolved = load_resolved_project(project_dir)

    assert loaded == config
    assert project_file == project_dir / "project.yaml"
    assert resolved.input_path == project_dir / "source.mid"
    assert resolved.timeline_path == project_dir / "timeline.json"


def test_timeline_json_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "timeline.json"
    timeline = make_timeline()

    write_timeline_json(timeline, path)

    assert read_timeline_json(path) == timeline
    assert not list(tmp_path.glob("*.tmp"))


def test_fingering_csv_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "fingering.csv"

    write_fingering_csv(make_timeline(), path)
    overrides = read_fingering_csv(path)

    assert overrides["n1"].hand == Hand.RIGHT
    assert overrides["n1"].finger == 1


def test_fingering_csv_reports_row_number(tmp_path: Path) -> None:
    path = tmp_path / "fingering.csv"
    path.write_text("note_id,hand,finger\nn1,right,8\n", encoding="utf-8")

    with pytest.raises(PianoHandError, match=r":2: finger must be between 1 and 5"):
        read_fingering_csv(path)


def test_non_empty_project_directory_requires_force(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "keep.txt").write_text("user data", encoding="utf-8")

    with pytest.raises(PianoHandError, match="not empty"):
        prepare_project_directory(project)

    assert prepare_project_directory(project, force=True) == project
    assert (project / "keep.txt").read_text(encoding="utf-8") == "user data"
