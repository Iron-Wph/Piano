"""Safe project-directory and atomic file operations."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from piano_hand.errors import ErrorCode, PianoHandError


def atomic_write_text(path: str | Path, text: str, *, encoding: str = "utf-8") -> Path:
    """Write text through a sibling temporary file and atomically replace the target."""

    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            newline="",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Cannot write output file {target}: {exc}",
            "Check the destination path and write permissions.",
        ) from exc
    return target


def atomic_copy_file(source: str | Path, target: str | Path) -> Path:
    """Copy a file through a sibling temporary file before replacing the target."""

    source_path = Path(source)
    target_path = Path(target)
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        shutil.copyfile(source_path, temporary)
        os.replace(temporary, target_path)
    except OSError as exc:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Cannot copy {source_path} to {target_path}: {exc}",
            "Check the source and destination permissions.",
        ) from exc
    return target_path


def prepare_project_directory(path: str | Path, *, force: bool = False) -> Path:
    """Create an output directory and reject silent writes into non-empty directories."""

    directory = Path(path).expanduser().resolve()
    if directory.exists() and not directory.is_dir():
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Project output is not a directory: {directory}",
            "Choose a directory path.",
        )
    if directory.exists() and any(directory.iterdir()) and not force:
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Project directory is not empty: {directory}",
            "Choose an empty directory or pass --force to replace generated files.",
        )
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Cannot create project directory {directory}: {exc}",
            "Choose a writable output location.",
        ) from exc
    return directory


def sha256_file(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Compute a file SHA-256 without loading the complete input into memory."""

    source = Path(path)
    digest = hashlib.sha256()
    try:
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Cannot read input file {source}: {exc}",
            "Check that the input exists and is readable.",
        ) from exc
    return digest.hexdigest()
