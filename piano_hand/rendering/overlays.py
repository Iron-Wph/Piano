"""Small Pillow overlay helpers shared by the frame renderer."""

from __future__ import annotations

from collections.abc import Mapping

from PIL import ImageDraw, ImageFont

Color = tuple[int, int, int]


def default_font(size: int = 18) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Load a commonly available font, falling back to Pillow's bitmap font."""

    for name in ("DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw_status_overlay(
    draw: ImageDraw.ImageDraw,
    *,
    width: int,
    measure: int | None,
    speed: float,
    foreground: Color,
    panel: Color,
) -> None:
    """Draw measure, playback speed, and a compact hand-color legend."""

    draw.rounded_rectangle((18, 16, width - 18, 62), radius=10, fill=panel)
    font = default_font(18)
    measure_text = f"Measure {measure}" if measure is not None else "Measure --"
    draw.text((32, 28), measure_text, fill=foreground, font=font)
    speed_text = f"Speed {speed:.2f}x"
    speed_box = draw.textbbox((0, 0), speed_text, font=font)
    draw.text(
        (width - 32 - (speed_box[2] - speed_box[0]), 28),
        speed_text,
        fill=foreground,
        font=font,
    )


def draw_finger_numbers(
    draw: ImageDraw.ImageDraw,
    *,
    fingertips: Mapping[int, tuple[float, float]],
    color: Color,
) -> None:
    font = default_font(16)
    for finger, (x, y) in fingertips.items():
        radius = 10
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
        text = str(finger)
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(
            (x - (box[2] - box[0]) / 2, y - (box[3] - box[1]) / 2 - 1),
            text,
            fill=(255, 255, 255),
            font=font,
        )
