"""CPU-only Pillow renderer producing RGB frames for downstream encoders."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

from piano_hand.models.motion import HandPose, MotionFrame
from piano_hand.models.note import Hand
from piano_hand.models.project import RenderConfig
from piano_hand.motion.keyboard_geometry import KeyboardGeometry, pitch_name
from piano_hand.rendering.overlays import draw_finger_numbers, draw_status_overlay

Color = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class _Palette:
    background: Color
    foreground: Color
    panel: Color
    white_key: Color
    black_key: Color
    outline: Color
    highlight_white: Color
    highlight_black: Color
    left_hand: Color
    right_hand: Color


PALETTES = {
    "dark": _Palette(
        background=(20, 23, 30),
        foreground=(238, 241, 247),
        panel=(36, 41, 52),
        white_key=(229, 232, 238),
        black_key=(30, 33, 40),
        outline=(10, 12, 16),
        highlight_white=(104, 202, 255),
        highlight_black=(51, 145, 206),
        left_hand=(65, 151, 255),
        right_hand=(255, 142, 65),
    ),
    "light": _Palette(
        background=(242, 244, 248),
        foreground=(28, 31, 38),
        panel=(220, 225, 234),
        white_key=(255, 255, 255),
        black_key=(38, 40, 47),
        outline=(90, 94, 104),
        highlight_white=(102, 190, 235),
        highlight_black=(43, 132, 185),
        left_hand=(43, 117, 214),
        right_hand=(222, 101, 31),
    ),
}


class FrameRenderer:
    """Render a MotionFrame as a Pillow image or RGB NumPy frame."""

    def __init__(
        self,
        keyboard: KeyboardGeometry,
        config: RenderConfig | None = None,
        *,
        width: int | None = None,
        height: int | None = None,
        theme: str | None = None,
        show_finger_numbers: bool | None = None,
        show_measure: bool | None = None,
        show_note_names: bool | None = None,
    ) -> None:
        config = config or RenderConfig()
        self.keyboard = keyboard
        self.width = width if width is not None else config.width
        self.height = height if height is not None else config.height
        self.theme = theme if theme is not None else config.theme
        if self.theme not in PALETTES:
            raise ValueError(f"unsupported theme: {self.theme}")
        if self.width != keyboard.width:
            raise ValueError(
                f"renderer width {self.width} must match keyboard width {keyboard.width}"
            )
        self.show_finger_numbers = (
            config.show_finger_numbers
            if show_finger_numbers is None
            else show_finger_numbers
        )
        self.show_measure = config.show_measure if show_measure is None else show_measure
        self.show_note_names = (
            config.show_note_names if show_note_names is None else show_note_names
        )
        if keyboard.top + keyboard.white_key_height > self.height:
            raise ValueError("keyboard geometry extends beyond renderer height")

    def render(
        self,
        frame: MotionFrame,
        *,
        speed: float = 1.0,
    ) -> Image.Image:
        """Render one RGB image with deterministic layer ordering."""

        palette = PALETTES[self.theme]
        image = Image.new("RGB", (self.width, self.height), palette.background)
        draw = ImageDraw.Draw(image)
        pressed = set(frame.pressed_keys)
        self._draw_keyboard(draw, pressed, palette)
        self._draw_hand(draw, frame.left, Hand.LEFT, frame, palette)
        self._draw_hand(draw, frame.right, Hand.RIGHT, frame, palette)
        if self.show_measure:
            draw_status_overlay(
                draw,
                width=self.width,
                measure=frame.measure,
                speed=speed,
                foreground=palette.foreground,
                panel=palette.panel,
            )
        return image

    def render_rgb(self, frame: MotionFrame, *, speed: float = 1.0) -> np.ndarray:
        """Return a contiguous height × width × 3 uint8 RGB array."""

        return np.ascontiguousarray(np.asarray(self.render(frame, speed=speed), dtype=np.uint8))

    def _draw_keyboard(
        self,
        draw: ImageDraw.ImageDraw,
        pressed: set[int],
        palette: _Palette,
    ) -> None:
        for key in self.keyboard.white_keys:
            fill = palette.highlight_white if key.pitch in pressed else palette.white_key
            draw.rectangle(
                (key.x, key.y, key.right, key.bottom),
                fill=fill,
                outline=palette.outline,
                width=2,
            )
            if self.show_note_names:
                draw.text(
                    (key.x + 4, key.bottom - 22),
                    pitch_name(key.pitch),
                    fill=(55, 58, 66),
                )
        for key in self.keyboard.black_keys:
            fill = palette.highlight_black if key.pitch in pressed else palette.black_key
            draw.rounded_rectangle(
                (key.x, key.y, key.right, key.bottom),
                radius=max(2, int(key.width * 0.08)),
                fill=fill,
                outline=palette.outline,
                width=2,
            )

    def _draw_hand(
        self,
        draw: ImageDraw.ImageDraw,
        pose: HandPose | None,
        hand: Hand,
        frame: MotionFrame,
        palette: _Palette,
    ) -> None:
        if pose is None:
            return
        color = palette.left_hand if hand == Hand.LEFT else palette.right_hand
        wrist = pose.wrist
        bases = [points[0] for points in pose.fingers.values() if points]
        if bases:
            palm_points = [wrist, *bases]
            draw.polygon(palm_points, fill=(*color, 72) if draw.mode == "RGBA" else color)
            draw.line([*bases, wrist, bases[0]], fill=color, width=5, joint="curve")
        fingertips: dict[int, tuple[float, float]] = {}
        active = set(frame.active_fingers.get(hand.value, []))
        for finger, joints in pose.fingers.items():
            if not joints:
                continue
            draw.line(joints, fill=color, width=6 if finger in active else 4, joint="curve")
            for point in joints[:-1]:
                radius = 3
                draw.ellipse(
                    (
                        point[0] - radius,
                        point[1] - radius,
                        point[0] + radius,
                        point[1] + radius,
                    ),
                    fill=color,
                )
            tip = joints[-1]
            tip_radius = 7 if finger in active else 5
            draw.ellipse(
                (
                    tip[0] - tip_radius,
                    tip[1] - tip_radius,
                    tip[0] + tip_radius,
                    tip[1] + tip_radius,
                ),
                fill=color,
            )
            fingertips[finger] = tip
        if self.show_finger_numbers:
            draw_finger_numbers(draw, fingertips=fingertips, color=color)
