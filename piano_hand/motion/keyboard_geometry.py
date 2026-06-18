"""Deterministic MIDI-pitch to piano-key geometry mapping."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

BLACK_PITCH_CLASSES = frozenset({1, 3, 6, 8, 10})
WHITE_PITCH_CLASSES = frozenset({0, 2, 4, 5, 7, 9, 11})
PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def is_black_pitch(pitch: int) -> bool:
    """Return whether a MIDI pitch is a black piano key."""

    _validate_pitch(pitch)
    return pitch % 12 in BLACK_PITCH_CLASSES


def pitch_name(pitch: int) -> str:
    """Return a scientific pitch name, where MIDI 60 is C4."""

    _validate_pitch(pitch)
    return f"{PITCH_CLASS_NAMES[pitch % 12]}{pitch // 12 - 1}"


def _validate_pitch(pitch: int) -> None:
    if not 0 <= pitch <= 127:
        raise ValueError(f"MIDI pitch must be in 0..127, got {pitch}")


def _white_pitches() -> tuple[int, ...]:
    return tuple(pitch for pitch in range(128) if pitch % 12 in WHITE_PITCH_CLASSES)


ALL_WHITE_PITCHES = _white_pitches()
WHITE_RANK = {pitch: index for index, pitch in enumerate(ALL_WHITE_PITCHES)}


def _white_at_or_below(pitch: int) -> int:
    while pitch > 0 and is_black_pitch(pitch):
        pitch -= 1
    return pitch


def _white_at_or_above(pitch: int) -> int:
    while pitch < 127 and is_black_pitch(pitch):
        pitch += 1
    return pitch


@dataclass(frozen=True, slots=True)
class KeyGeometry:
    """One visible piano key in canvas coordinates."""

    pitch: int
    x: float
    y: float
    width: float
    height: float
    is_black: bool
    contact_point: tuple[float, float]

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)


class KeyboardGeometry:
    """A fixed keyboard viewport sized to the score's pitch range.

    Horizontal positions derive from global white-key ranks. This makes the
    mapping deterministic while allowing a score-specific viewport and scale.
    """

    def __init__(
        self,
        *,
        keys: Iterable[KeyGeometry],
        width: int,
        top: float,
        white_key_height: float,
        low_white_pitch: int,
        high_white_pitch: int,
    ) -> None:
        self.width = int(width)
        self.top = float(top)
        self.white_key_height = float(white_key_height)
        self.low_white_pitch = low_white_pitch
        self.high_white_pitch = high_white_pitch
        self._keys = {key.pitch: key for key in keys}
        self.keys = tuple(sorted(self._keys.values(), key=lambda key: (key.is_black, key.pitch)))

    @classmethod
    def from_pitches(
        cls,
        pitches: Iterable[int],
        *,
        width: int = 1280,
        top: float = 390.0,
        white_key_height: float = 260.0,
        context_white_keys: int = 2,
        black_width_ratio: float = 0.62,
        black_height_ratio: float = 0.62,
        horizontal_padding: float = 24.0,
    ) -> KeyboardGeometry:
        """Build a fixed viewport with white-key context on each side."""

        pitch_list = sorted(set(int(pitch) for pitch in pitches))
        if not pitch_list:
            pitch_list = [60]
        for pitch in pitch_list:
            _validate_pitch(pitch)
        if width <= 0 or white_key_height <= 0:
            raise ValueError("width and white_key_height must be positive")
        if context_white_keys < 0:
            raise ValueError("context_white_keys must be non-negative")
        if not 0 < black_width_ratio < 1 or not 0 < black_height_ratio < 1:
            raise ValueError("black key ratios must be between zero and one")
        if horizontal_padding < 0 or horizontal_padding * 2 >= width:
            raise ValueError("horizontal_padding leaves no drawable keyboard width")

        first_white = _white_at_or_below(pitch_list[0])
        last_white = _white_at_or_above(pitch_list[-1])
        low_rank = max(0, WHITE_RANK[first_white] - context_white_keys)
        high_rank = min(len(ALL_WHITE_PITCHES) - 1, WHITE_RANK[last_white] + context_white_keys)
        low_white = ALL_WHITE_PITCHES[low_rank]
        high_white = ALL_WHITE_PITCHES[high_rank]
        white_count = high_rank - low_rank + 1
        drawable_width = width - horizontal_padding * 2
        white_width = drawable_width / white_count
        black_width = white_width * black_width_ratio
        black_height = white_key_height * black_height_ratio

        keys: list[KeyGeometry] = []
        for rank in range(low_rank, high_rank + 1):
            pitch = ALL_WHITE_PITCHES[rank]
            x = horizontal_padding + (rank - low_rank) * white_width
            keys.append(
                KeyGeometry(
                    pitch=pitch,
                    x=x,
                    y=top,
                    width=white_width,
                    height=white_key_height,
                    is_black=False,
                    contact_point=(x + white_width / 2.0, top + white_key_height * 0.82),
                )
            )

        # A black key belongs to the gap after its lower neighboring white key.
        for pitch in range(low_white, min(127, high_white + 1)):
            if not is_black_pitch(pitch):
                continue
            lower_white = _white_at_or_below(pitch)
            lower_rank = WHITE_RANK[lower_white]
            if not low_rank <= lower_rank < high_rank:
                continue
            boundary_x = horizontal_padding + (lower_rank - low_rank + 1) * white_width
            x = boundary_x - black_width / 2.0
            keys.append(
                KeyGeometry(
                    pitch=pitch,
                    x=x,
                    y=top,
                    width=black_width,
                    height=black_height,
                    is_black=True,
                    contact_point=(x + black_width / 2.0, top + black_height * 0.72),
                )
            )

        return cls(
            keys=keys,
            width=width,
            top=top,
            white_key_height=white_key_height,
            low_white_pitch=low_white,
            high_white_pitch=high_white,
        )

    @property
    def white_keys(self) -> tuple[KeyGeometry, ...]:
        return tuple(key for key in self.keys if not key.is_black)

    @property
    def black_keys(self) -> tuple[KeyGeometry, ...]:
        return tuple(key for key in self.keys if key.is_black)

    @property
    def visible_pitches(self) -> tuple[int, ...]:
        return tuple(sorted(self._keys))

    def key_for_pitch(self, pitch: int) -> KeyGeometry:
        """Return visible key geometry or raise a precise range error."""

        _validate_pitch(pitch)
        try:
            return self._keys[pitch]
        except KeyError as exc:
            raise KeyError(
                f"pitch {pitch} ({pitch_name(pitch)}) is outside keyboard viewport "
                f"{self.low_white_pitch}..{self.high_white_pitch}"
            ) from exc

    def contact_point(self, pitch: int) -> tuple[float, float]:
        return self.key_for_pitch(pitch).contact_point

    def cluster_center(self, pitches: Iterable[int]) -> tuple[float, float]:
        points = [self.contact_point(int(pitch)) for pitch in pitches]
        if not points:
            raise ValueError("at least one pitch is required")
        return (
            sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points),
        )
