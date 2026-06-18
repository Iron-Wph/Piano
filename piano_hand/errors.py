"""Stable application error types and codes."""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    INPUT_ERROR = "INPUT_ERROR"
    PARSE_ERROR = "PARSE_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"
    FINGERING_ERROR = "FINGERING_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    RENDER_ERROR = "RENDER_ERROR"
    ENCODE_ERROR = "ENCODE_ERROR"
    OUTPUT_ERROR = "OUTPUT_ERROR"


class PianoHandError(RuntimeError):
    """Base exception carrying a stable machine-readable error code."""

    def __init__(self, code: ErrorCode, message: str, suggestion: str | None = None) -> None:
        self.code = code
        self.message = message
        self.suggestion = suggestion
        text = f"{code}: {message}"
        if suggestion:
            text += f" Suggestion: {suggestion}"
        super().__init__(text)

