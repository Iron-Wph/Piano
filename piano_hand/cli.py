"""Typer command-line interface for the local Piano Hand MVP."""

from __future__ import annotations

import importlib
import inspect
import os
import platform
import sys
import tempfile
import time
from collections.abc import Callable
from math import ceil
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import typer

from piano_hand.config import (
    PROJECT_FILENAME,
    ResolvedProject,
    load_resolved_project,
    project_file_for,
    save_project_config,
    soundfont_from_environment,
)
from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.io import (
    apply_fingering_overrides,
    atomic_copy_file,
    prepare_project_directory,
    read_fingering_csv,
    read_timeline_json,
    sha256_file,
    write_fingering_csv,
    write_timeline_json,
)
from piano_hand.models import (
    AudioConfig,
    InputConfig,
    Issue,
    IssueSeverity,
    ProjectConfig,
    ScoreTimeline,
)
from piano_hand.validation import (
    check_dependencies,
    inspect_rendered_media,
    validate_project,
    write_validation_report,
)

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Convert MIDI or MusicXML into editable virtual-hand teaching video projects.",
)

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_WARNINGS = 2
DEMO_FILENAMES = {
    "musicxml": "twinkle_twinkle_beginner.musicxml",
    "midi": "twinkle_twinkle_beginner.mid",
}


@app.command()
def analyze(
    input_file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True)
    ],
    output: Annotated[Path, typer.Option("--output", "-o", file_okay=False)],
    force: Annotated[
        bool, typer.Option("--force", help="Allow generated files to be replaced.")
    ] = False,
    mute: Annotated[
        bool,
        typer.Option(
            "--mute",
            help="Disable audio so the project can render without FluidSynth or a SoundFont.",
        ),
    ] = False,
) -> None:
    """Parse a score and create project.yaml, timeline.json, and fingering.csv."""

    try:
        project_file = _analyze_project(input_file, output, force=force, mute=mute)
    except Exception as exc:
        _fail(exc)
    typer.echo(f"Project created: {project_file}")


@app.command("validate")
def validate_command(
    project: Annotated[
        Path, typer.Argument(help="Project directory or project.yaml.")
    ],
    strict: Annotated[
        bool, typer.Option("--strict", help="Return 2 when warnings exist.")
    ] = False,
) -> None:
    """Validate project files, musical assignments, paths, and dependencies."""

    try:
        _, report = _validate_and_write(project)
    except Exception as exc:
        _fail(exc)
    _print_report_summary(report)
    if report.errors:
        raise typer.Exit(EXIT_ERROR)
    if strict and report.warnings:
        raise typer.Exit(EXIT_WARNINGS)


@app.command()
def render(
    project: Annotated[
        Path, typer.Argument(help="Project directory or project.yaml.")
    ],
    output: Annotated[
        Path | None, typer.Option("--output", "-o", dir_okay=False)
    ] = None,
    keep_temp: Annotated[bool, typer.Option("--keep-temp")] = False,
) -> None:
    """Validate and render an existing editable project."""

    try:
        rendered = _render_existing(project, output=output, keep_temp=keep_temp)
    except Exception as exc:
        _fail(exc)
    typer.echo(f"Rendered video: {rendered}")


@app.command()
def build(
    input_file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, readable=True)
    ],
    output: Annotated[Path, typer.Option("--output", "-o", file_okay=False)],
    force: Annotated[bool, typer.Option("--force")] = False,
    keep_temp: Annotated[bool, typer.Option("--keep-temp")] = False,
    mute: Annotated[
        bool,
        typer.Option(
            "--mute",
            help="Disable audio so the project can render without FluidSynth or a SoundFont.",
        ),
    ] = False,
) -> None:
    """Run analyze, validate, and render in sequence."""

    try:
        project_file = _analyze_project(input_file, output, force=force, mute=mute)
        rendered = _render_existing(project_file, output=None, keep_temp=keep_temp)
    except Exception as exc:
        _fail(exc)
    typer.echo(f"Project created: {project_file}")
    typer.echo(f"Rendered video: {rendered}")


@app.command()
def demo(
    source_format: Annotated[
        Literal["musicxml", "midi"],
        typer.Option(
            "--format",
            help="Bundled beginner score format to exercise.",
        ),
    ] = "musicxml",
    output: Annotated[
        Path,
        typer.Option("--output", "-o", file_okay=False),
    ] = Path("work/demo"),
    force: Annotated[
        bool,
        typer.Option("--force", help="Allow generated demo files to be replaced."),
    ] = False,
    keep_temp: Annotated[bool, typer.Option("--keep-temp")] = False,
) -> None:
    """Render the bundled beginner score with a full 88-key silent keyboard."""

    try:
        source = _demo_source_path(source_format)
        project_file = _analyze_project(source, output, force=force, mute=True)
        _configure_demo_project(project_file)
        rendered = _render_existing(project_file, output=None, keep_temp=keep_temp)
    except Exception as exc:
        _fail(exc)
    typer.echo(f"Demo score: {source}")
    typer.echo(f"Project created: {project_file}")
    typer.echo(f"Rendered video: {rendered}")


@app.command()
def doctor(
    soundfont: Annotated[
        Path | None,
        typer.Option(
            "--soundfont",
            dir_okay=False,
            help="SoundFont to check; defaults to PIANO_HAND_SOUNDFONT.",
        ),
    ] = None,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            file_okay=False,
            help="Directory whose write access should be checked.",
        ),
    ] = Path("."),
    sample: Annotated[
        Path | None,
        typer.Option(
            "--sample",
            exists=True,
            dir_okay=False,
            readable=True,
            help="Optional MIDI/MusicXML file for a minimum parser check.",
        ),
    ] = None,
    mute: Annotated[
        bool,
        typer.Option(
            "--mute",
            help="Check only the silent-video toolchain; FluidSynth and SoundFont become optional.",
        ),
    ] = False,
) -> None:
    """Check Python, external media tools, SoundFont, and local write access."""

    active_soundfont = soundfont.resolve() if soundfont else soundfont_from_environment()
    issues, environment = check_dependencies(
        soundfont=active_soundfont,
        require_audio=not mute,
    )
    python_ok = sys.version_info >= (3, 11)
    typer.echo(f"Python: {'OK' if python_ok else 'ERROR'} {platform.python_version()}")
    if not python_ok:
        issues.append(
            Issue(
                code="PYTHON_VERSION",
                severity=IssueSeverity.ERROR,
                message="Python 3.11 or newer is required.",
                location=sys.executable,
                suggestion="Run Piano Hand with Python 3.11+.",
            )
        )
    for name in ("ffmpeg", "ffprobe"):
        typer.echo(f"{name}: {'OK' if environment.get(name) else 'ERROR'}")
    if mute:
        typer.echo(
            f"fluidsynth: {'OK' if environment.get('fluidsynth') else 'OPTIONAL'}"
        )
        typer.echo(
            f"SoundFont: {'OK' if active_soundfont and active_soundfont.is_file() else 'OPTIONAL'}"
        )
    else:
        typer.echo(f"fluidsynth: {'OK' if environment.get('fluidsynth') else 'ERROR'}")
        typer.echo(
            f"SoundFont: {'OK' if active_soundfont and active_soundfont.is_file() else 'ERROR'}"
        )

    output_writable = _directory_is_writable(output_dir)
    if not output_writable:
        issues.append(
            Issue(
                code="OUTPUT_PATH_UNWRITABLE",
                severity=IssueSeverity.ERROR,
                message=f"Output directory is not writable: {output_dir}",
                location=str(output_dir),
                suggestion="Choose a writable --output-dir.",
            )
        )
    typer.echo(f"Output directory: {'OK' if output_writable else 'ERROR'}")

    if sample is not None:
        try:
            timeline = _parse_input(sample.resolve())
            typer.echo(f"Sample parse: OK ({len(timeline.notes)} notes)")
        except Exception as exc:
            issues.append(
                Issue(
                    code="SAMPLE_PARSE_FAILED",
                    severity=IssueSeverity.ERROR,
                    message=str(exc),
                    location=str(sample),
                    suggestion="Check the sample format and parser installation.",
                )
            )
            typer.echo("Sample parse: ERROR")

    for issue in issues:
        typer.echo(f"{issue.severity.value.upper()} {issue.code}: {issue.message}", err=True)
    if any(issue.severity == IssueSeverity.ERROR for issue in issues):
        raise typer.Exit(EXIT_ERROR)


def _analyze_project(
    input_file: Path,
    output: Path,
    *,
    force: bool,
    mute: bool = False,
) -> Path:
    source = input_file.expanduser().resolve()
    if not source.is_file():
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Input file does not exist: {source}",
            "Provide a readable MIDI or MusicXML file.",
        )
    source_type_value = ScoreTimeline.source_type_for_path(source)
    source_type = cast(Literal["midi", "musicxml"], source_type_value)
    if source_type == "unknown":
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Unsupported input format: {source.suffix or '<no extension>'}",
            "Use .mid, .midi, .musicxml, .xml, or .mxl.",
        )

    project_root = prepare_project_directory(output, force=force)
    project_source = project_root / f"source{source.suffix.lower()}"
    atomic_copy_file(source, project_source)

    started = time.perf_counter()
    timeline = _parse_input(project_source)
    timeline = _apply_planning(timeline)
    timeline.source.path = f"./{project_source.name}"
    timeline.source.type = source_type
    timeline.source.sha256 = sha256_file(source)

    timeline_path = project_root / "timeline.json"
    fingering_path = project_root / "fingering.csv"
    project_file = project_root / PROJECT_FILENAME
    write_timeline_json(timeline, timeline_path)
    write_fingering_csv(timeline, fingering_path)

    configured_soundfont = soundfont_from_environment()
    config = ProjectConfig(
        input=InputConfig(path=f"./{project_source.name}", type=source_type),
        audio=AudioConfig(
            enabled=not mute,
            soundfont_path=(
                str(configured_soundfont)
                if configured_soundfont is not None and not mute
                else None
            ),
        ),
    )
    save_project_config(config, project_file)
    typer.echo(
        f"Analyzed {len(timeline.notes)} notes in {time.perf_counter() - started:.2f}s"
    )
    return project_file


def _demo_source_path(source_format: Literal["musicxml", "midi"]) -> Path:
    filename = DEMO_FILENAMES[source_format]
    source = Path(__file__).resolve().parent.parent / "examples" / filename
    if not source.is_file():
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Bundled demo score is unavailable: {source}",
            "Run the demo command from a complete Piano Hand repository checkout.",
        )
    return source


def _configure_demo_project(project_file: Path) -> None:
    resolved = load_resolved_project(project_file)
    config = resolved.config.model_copy(
        update={
            "render": resolved.config.render.model_copy(
                update={"keyboard_mode": "full"}
            ),
            "audio": resolved.config.audio.model_copy(
                update={"enabled": False, "soundfont_path": None}
            ),
        }
    )
    save_project_config(config, project_file)


def _validate_and_write(
    project: str | Path,
) -> tuple[ResolvedProject, Any]:
    resolved, report = validate_project(project)
    write_validation_report(report, resolved.report_path)
    return resolved, report


def _render_existing(
    project: str | Path,
    *,
    output: Path | None,
    keep_temp: bool,
) -> Path:
    try:
        preliminary_project = load_resolved_project(project)
    except Exception as exc:
        _write_project_validation_failure(project, exc)
        raise

    output_path = _resolve_render_output_path(preliminary_project, output)
    _validate_render_output_path(output_path, preliminary_project)

    try:
        resolved, report = _validate_and_write(project)
    except Exception as exc:
        _write_project_validation_failure(project, exc)
        raise
    if report.errors:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Project validation failed with {len(report.errors)} blocking error(s). "
            f"Report: {resolved.report_path}",
            "Fix the validation report before rendering.",
        )

    timeline = read_timeline_json(resolved.timeline_path)
    timeline = _timeline_with_overrides(timeline, resolved.fingering_path)
    started = time.perf_counter()

    temp_context: tempfile.TemporaryDirectory[str] | None = None
    if keep_temp:
        temp_dir = Path(tempfile.mkdtemp(prefix=".piano-hand-", dir=resolved.root))
        typer.echo(f"Temporary files retained at: {temp_dir}")
    else:
        temp_context = tempfile.TemporaryDirectory(
            prefix=".piano-hand-", dir=resolved.root
        )
        temp_dir = Path(temp_context.name)

    try:
        rendered = _render_pipeline(
            timeline=timeline,
            project=resolved,
            output_path=output_path,
            temp_dir=temp_dir,
        )
        media_issues = inspect_rendered_media(rendered, resolved)
        report.issues.extend(media_issues)
        report.valid = not report.errors
        report.stage_timings_sec["render_total"] = time.perf_counter() - started
        write_validation_report(report, resolved.report_path)
        if report.errors:
            raise PianoHandError(
                ErrorCode.ENCODE_ERROR,
                f"Rendered media failed validation. Report: {resolved.report_path}",
                "Review media validation errors and encoder settings.",
            )
        return rendered
    except Exception as exc:
        if not any(issue.code == "RENDER_FAILED" for issue in report.issues):
            report.issues.append(
                Issue(
                    code="RENDER_FAILED",
                    severity=IssueSeverity.ERROR,
                    message=str(exc),
                    location=str(output_path),
                    suggestion="Review the rendering stage error and dependency checks.",
                )
            )
        report.valid = False
        report.stage_timings_sec["render_total"] = time.perf_counter() - started
        write_validation_report(report, resolved.report_path)
        raise
    finally:
        if temp_context is not None:
            temp_context.cleanup()


def _parse_input(path: Path) -> ScoreTimeline:
    source_type = ScoreTimeline.source_type_for_path(path)
    if source_type == "midi":
        function = _load_callable(
            "piano_hand.parsers.midi_parser",
            ("parse_midi", "parse_midi_file"),
            ErrorCode.PARSE_ERROR,
        )
    elif source_type == "musicxml":
        function = _load_callable(
            "piano_hand.parsers.musicxml_parser",
            ("parse_musicxml", "parse_musicxml_file"),
            ErrorCode.PARSE_ERROR,
        )
    else:
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Unsupported score format: {path}",
            "Use MIDI or MusicXML.",
        )
    result = _invoke(function, path=path, input_path=path, source=path)
    try:
        return result if isinstance(result, ScoreTimeline) else ScoreTimeline.model_validate(result)
    except Exception as exc:
        raise PianoHandError(
            ErrorCode.PARSE_ERROR,
            f"Parser returned an invalid ScoreTimeline for {path}: {exc}",
            "Update the parser to return piano_hand.models.ScoreTimeline.",
        ) from exc


def _apply_planning(timeline: ScoreTimeline) -> ScoreTimeline:
    hand_function = _load_callable(
        "piano_hand.planning.hand_assignment",
        ("assign_hands", "plan_hands"),
        ErrorCode.FINGERING_ERROR,
    )
    timeline = _coerce_planning_result(
        _invoke(hand_function, timeline=timeline, score=timeline, notes=timeline.notes),
        timeline,
        "hand assignment",
    )
    finger_function = _load_callable(
        "piano_hand.planning.fingering_dp",
        ("plan_fingering", "assign_fingering", "generate_fingering"),
        ErrorCode.FINGERING_ERROR,
    )
    return _coerce_planning_result(
        _invoke(finger_function, timeline=timeline, score=timeline, notes=timeline.notes),
        timeline,
        "fingering planning",
    )


def _coerce_planning_result(
    result: object, timeline: ScoreTimeline, stage: str
) -> ScoreTimeline:
    if result is None:
        return timeline
    if isinstance(result, ScoreTimeline):
        return result
    if isinstance(result, list):
        try:
            return timeline.model_copy(update={"notes": result})
        except Exception as exc:
            raise PianoHandError(
                ErrorCode.FINGERING_ERROR,
                f"Invalid note list returned by {stage}: {exc}",
                "Return a ScoreTimeline, a list of NoteEvent, or mutate the supplied timeline.",
            ) from exc
    raise PianoHandError(
        ErrorCode.FINGERING_ERROR,
        f"Unsupported result from {stage}: {type(result).__name__}",
        "Return a ScoreTimeline, a list of NoteEvent, or mutate the supplied timeline.",
    )


def _timeline_with_overrides(timeline: ScoreTimeline, csv_path: Path) -> ScoreTimeline:
    return apply_fingering_overrides(timeline, read_fingering_csv(csv_path))


def _render_pipeline(
    *,
    timeline: ScoreTimeline,
    project: ResolvedProject,
    output_path: Path,
    temp_dir: Path,
) -> Path:
    try:
        from piano_hand.motion.keyboard_geometry import KeyboardGeometry
        from piano_hand.motion.trajectory import MotionPlanner
        from piano_hand.rendering.audio_renderer import render_timeline_audio
        from piano_hand.rendering.frame_renderer import FrameRenderer
        from piano_hand.rendering.video_encoder import encode_rgb_frames
    except ImportError as exc:
        raise PianoHandError(
            ErrorCode.RENDER_ERROR,
            f"Rendering modules are unavailable: {exc}",
            "Complete the motion and rendering modules before rendering.",
        ) from exc

    playback = _prepare_playback_timeline(timeline, project.config)
    render_config = project.config.render
    keyboard_top = max(120.0, render_config.height * 0.54)
    keyboard_height = max(80.0, render_config.height - keyboard_top - 24.0)
    keyboard = KeyboardGeometry.from_pitches(
        (note.pitch for note in playback.notes),
        width=render_config.width,
        top=keyboard_top,
        white_key_height=keyboard_height,
        mode=render_config.keyboard_mode,
    )
    motion_plan = MotionPlanner(playback.notes, keyboard)
    frame_renderer = FrameRenderer(keyboard, render_config)
    duration_sec = max(playback.duration_sec, motion_plan.duration_sec)
    frame_count = max(1, ceil(duration_sec * render_config.fps))

    def frames() -> Any:
        for frame_index in range(frame_count):
            time_sec = frame_index / render_config.fps
            yield frame_renderer.render_rgb(
                motion_plan.frame_at(time_sec),
                speed=project.config.playback.tempo_value,
            )

    audio_path = render_timeline_audio(
        playback,
        temp_dir / "audio.wav",
        soundfont_path=project.soundfont_path,
        sample_rate=project.config.audio.sample_rate,
        enabled=project.config.audio.enabled,
    )
    return encode_rgb_frames(
        frames(),
        output_path,
        width=render_config.width,
        height=render_config.height,
        fps=render_config.fps,
        audio_path=audio_path,
        audio_sample_rate=project.config.audio.sample_rate,
    )


def _prepare_playback_timeline(
    timeline: ScoreTimeline, config: ProjectConfig
) -> ScoreTimeline:
    """Apply measure cropping, count-in, and tempo scaling to a timeline copy."""

    start_measure = config.playback.start_measure
    end_measure = config.playback.end_measure
    selected = [
        note
        for note in timeline.notes
        if note.measure >= start_measure
        and (end_measure is None or note.measure <= end_measure)
    ]
    if not selected:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Playback measure range {start_measure}..{end_measure or 'end'} contains no notes.",
            "Choose a measure range present in the score.",
        )
    first_onset = min(note.onset_sec for note in selected)
    source_bpm = timeline.tempo_map[0].bpm if timeline.tempo_map else 120.0
    if config.playback.tempo_mode == "multiplier":
        multiplier = config.playback.tempo_value
        effective_bpm = source_bpm * multiplier
    else:
        effective_bpm = config.playback.tempo_value
        multiplier = effective_bpm / source_bpm
    count_in_sec = config.playback.count_in_beats * 60.0 / effective_bpm

    notes = [
        note.model_copy(
            update={
                "onset_sec": (note.onset_sec - first_onset) / multiplier + count_in_sec,
                "duration_sec": note.duration_sec / multiplier,
            }
        )
        for note in selected
    ]
    selected_ids = {note.id for note in selected}
    pedal_events = [
        pedal.model_copy(
            update={
                "time_sec": (pedal.time_sec - first_onset) / multiplier + count_in_sec
            }
        )
        for pedal in timeline.pedal_events
        if pedal.time_sec >= first_onset
    ]
    duration_sec = max(note.offset_sec for note in notes)
    return timeline.model_copy(
        update={
            "notes": [note for note in notes if note.id in selected_ids],
            "pedal_events": [
                pedal for pedal in pedal_events if 0 <= pedal.time_sec <= duration_sec
            ],
            "duration_sec": duration_sec,
        }
    )


def _load_callable(
    module_name: str, names: tuple[str, ...], error_code: ErrorCode
) -> Callable[..., Any]:
    try:
        module = importlib.import_module(module_name)
    except (ImportError, ModuleNotFoundError) as exc:
        raise PianoHandError(
            error_code,
            f"Required module is unavailable: {module_name} ({exc})",
            f"Implement {module_name} before running this command.",
        ) from exc
    for name in names:
        function = getattr(module, name, None)
        if callable(function):
            return function
    raise PianoHandError(
        error_code,
        f"{module_name} does not expose any supported function: {', '.join(names)}",
        f"Implement one of: {', '.join(names)}.",
    )


def _invoke(function: Callable[..., Any], **available: object) -> Any:
    """Call a parallel module by matching its named parameters to the explicit contract."""

    signature = inspect.signature(function)
    parameters = signature.parameters
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return function(**available)
    kwargs = {
        name: available[name]
        for name, parameter in parameters.items()
        if name in available
        and parameter.kind
        in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    }
    missing = [
        name
        for name, parameter in parameters.items()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind
        in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD}
        and name not in kwargs
    ]
    if missing:
        raise PianoHandError(
            ErrorCode.RENDER_ERROR,
            (
                f"Unsupported callable contract for "
                f"{function.__module__}.{function.__name__}; "
                f"unmapped parameters: {', '.join(missing)}"
            ),
            "Use the documented timeline/config/temp_dir/output_path parameter names.",
        )
    return function(**kwargs)


def _contract_error(stage: str, expected: str) -> PianoHandError:
    return PianoHandError(
        ErrorCode.RENDER_ERROR,
        f"The {stage} returned no result.",
        f"Return {expected}.",
    )


def _resolve_render_output_path(project: ResolvedProject, output: Path | None) -> Path:
    candidate = project.video_path if output is None else output.expanduser()
    if output is not None and not candidate.is_absolute():
        candidate = project.root / candidate
    try:
        return candidate.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Invalid video output path '{candidate}': {exc}",
            "Choose a valid file path in a writable directory.",
        ) from exc


def _validate_render_output_path(
    output_path: Path, project: ResolvedProject
) -> None:
    protected_paths = (
        ("project.yaml", project.project_file),
        ("input.path", project.input_path),
        ("timeline.json", project.timeline_path),
        ("fingering.csv", project.fingering_path),
        ("validation-report.json", project.report_path),
    )
    for label, protected_path in protected_paths:
        if _paths_refer_to_same_file(output_path, protected_path):
            raise PianoHandError(
                ErrorCode.OUTPUT_ERROR,
                f"Video output path conflicts with protected project file "
                f"'{label}': {output_path}",
                "Choose a different video output path.",
            )

    if output_path.exists() and not output_path.is_file():
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Video output target is not a regular file: {output_path}",
            "Choose a file path rather than a directory or special file.",
        )
    if not _directory_is_writable(output_path.parent):
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Video output parent directory is not writable: {output_path.parent}",
            "Choose a writable video output directory.",
        )
    if output_path.exists() and not os.access(output_path, os.W_OK):
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Video output target is not writable: {output_path}",
            "Choose a writable video output file.",
        )


def _paths_refer_to_same_file(left: Path, right: Path) -> bool:
    if left == right:
        return True
    try:
        return os.path.samefile(left, right)
    except (OSError, ValueError):
        return False


def _directory_is_writable(path: Path) -> bool:
    directory = path.expanduser().resolve()
    if not directory.exists():
        parent = directory.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        return parent.is_dir() and os.access(parent, os.W_OK)
    return directory.is_dir() and os.access(directory, os.W_OK)


def _print_report_summary(report: Any) -> None:
    typer.echo(
        f"Validation: {'PASS' if report.valid else 'FAIL'} "
        f"({len(report.errors)} errors, {len(report.warnings)} warnings)"
    )
    for issue in report.issues:
        typer.echo(
            f"{issue.severity.value.upper()} {issue.code}: {issue.message}",
            err=issue.severity == IssueSeverity.ERROR,
        )


def _failure_report(
    *,
    code: str,
    message: str,
    location: str,
    suggestion: str,
) -> Any:
    from piano_hand.models import ValidationReport

    return ValidationReport(
        valid=False,
        issues=[
            Issue(
                code=code,
                severity=IssueSeverity.ERROR,
                message=message,
                location=location,
                suggestion=suggestion,
            )
        ],
        environment={"python": platform.python_version()},
    )


def _write_project_validation_failure(project: str | Path, exc: Exception) -> None:
    fallback_report = project_file_for(project).parent / "validation-report.json"
    failure_report = _failure_report(
        code="PROJECT_VALIDATION_FAILED",
        message=str(exc),
        location=str(project),
        suggestion="Correct project.yaml and configured paths.",
    )
    write_validation_report(failure_report, fallback_report)


def _fail(exc: Exception) -> None:
    if isinstance(exc, typer.Exit):
        raise exc
    if isinstance(exc, PianoHandError):
        typer.echo(str(exc), err=True)
    else:
        typer.echo(
            str(
                PianoHandError(
                    ErrorCode.RENDER_ERROR,
                    f"Unexpected failure: {exc}",
                    "Run with a valid project and review the validation report.",
                )
            ),
            err=True,
        )
    raise typer.Exit(EXIT_ERROR)


if __name__ == "__main__":
    app()
