"""Project configuration loading and path resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from pydantic import ValidationError

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import ProjectConfig

PROJECT_FILENAME = "project.yaml"


@dataclass(frozen=True)
class ResolvedProject:
    """A validated project configuration with paths anchored to project.yaml."""

    project_file: Path
    root: Path
    config: ProjectConfig
    input_path: Path
    timeline_path: Path
    fingering_path: Path
    video_path: Path
    report_path: Path
    soundfont_path: Path | None


def project_file_for(path: str | Path) -> Path:
    """Return the project YAML path for either a project directory or YAML file."""

    candidate = Path(path).expanduser()
    if candidate.is_dir() or candidate.suffix.lower() not in {".yaml", ".yml"}:
        candidate = candidate / PROJECT_FILENAME
    return candidate.resolve()


def resolve_config_path(project_file: str | Path, configured_path: str | Path) -> Path:
    """Resolve a configured path relative to the directory containing project.yaml."""

    configured = Path(configured_path).expanduser()
    if configured.is_absolute():
        return configured.resolve()
    return (project_file_for(project_file).parent / configured).resolve()


def load_project_config(path: str | Path) -> ProjectConfig:
    """Load and validate a safe YAML project configuration."""

    project_file = project_file_for(path)
    try:
        raw = project_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Cannot read project configuration: {project_file} ({exc})",
            "Check that project.yaml exists and is readable.",
        ) from exc

    try:
        data: Any = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Invalid YAML in {project_file}: {exc}",
            "Fix the YAML syntax at the reported line and column.",
        ) from exc

    if not isinstance(data, dict):
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Project configuration must be a YAML mapping: {project_file}",
            "Use the documented project.yaml object structure.",
        )

    try:
        return ProjectConfig.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Invalid project configuration {project_file}: {details}",
            "Correct the listed field values.",
        ) from exc


def save_project_config(config: ProjectConfig, path: str | Path) -> Path:
    """Serialize a project configuration atomically as UTF-8 YAML."""

    from piano_hand.io.project_files import atomic_write_text

    project_file = project_file_for(path)
    data = config.model_dump(mode="json")
    text = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    atomic_write_text(project_file, text)
    return project_file


def load_resolved_project(path: str | Path) -> ResolvedProject:
    """Load a project and resolve all file paths without changing the config values."""

    project_file = project_file_for(path)
    config = load_project_config(project_file)
    soundfont = (
        resolve_config_path(project_file, config.audio.soundfont_path)
        if config.audio.soundfont_path
        else None
    )
    return ResolvedProject(
        project_file=project_file,
        root=project_file.parent,
        config=config,
        input_path=resolve_config_path(project_file, config.input.path),
        timeline_path=resolve_config_path(project_file, config.timeline.path),
        fingering_path=resolve_config_path(
            project_file, config.timeline.fingering_overrides
        ),
        video_path=resolve_config_path(project_file, config.output.video_path),
        report_path=resolve_config_path(project_file, config.output.report_path),
        soundfont_path=soundfont,
    )


def soundfont_from_environment() -> Path | None:
    """Return the optional SoundFont configured for doctor outside a project."""

    value = os.environ.get("PIANO_HAND_SOUNDFONT")
    return Path(value).expanduser().resolve() if value else None
