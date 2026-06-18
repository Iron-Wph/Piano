"""Smooth interpolation helpers for hand trajectories."""

from __future__ import annotations

from piano_hand.models.motion import HandPose, Point


def cubic_ease(value: float) -> float:
    """Smoothstep easing clamped to [0, 1]."""

    value = max(0.0, min(1.0, float(value)))
    return value * value * (3.0 - 2.0 * value)


def hermite_point(
    start: Point,
    end: Point,
    value: float,
    start_tangent: Point = (0.0, 0.0),
    end_tangent: Point = (0.0, 0.0),
) -> Point:
    """Interpolate a point with cubic Hermite basis functions."""

    t = max(0.0, min(1.0, float(value)))
    t2 = t * t
    t3 = t2 * t
    h00 = 2 * t3 - 3 * t2 + 1
    h10 = t3 - 2 * t2 + t
    h01 = -2 * t3 + 3 * t2
    h11 = t3 - t2
    return (
        h00 * start[0] + h10 * start_tangent[0] + h01 * end[0] + h11 * end_tangent[0],
        h00 * start[1] + h10 * start_tangent[1] + h01 * end[1] + h11 * end_tangent[1],
    )


def _point_lerp(start: Point, end: Point, value: float) -> Point:
    eased = cubic_ease(value)
    return (
        start[0] + (end[0] - start[0]) * eased,
        start[1] + (end[1] - start[1]) * eased,
    )


def interpolate_hand_pose(start: HandPose, end: HandPose, value: float) -> HandPose:
    """Cubic-ease all matching joints in two hand poses."""

    fingers: dict[int, list[Point]] = {}
    for finger in range(1, 6):
        start_points = start.fingers.get(finger, [])
        end_points = end.fingers.get(finger, [])
        if len(start_points) != len(end_points):
            fingers[finger] = list(end_points if value >= 0.5 else start_points)
            continue
        fingers[finger] = [
            _point_lerp(start_point, end_point, value)
            for start_point, end_point in zip(start_points, end_points, strict=True)
        ]
    return HandPose(
        wrist=_point_lerp(start.wrist, end.wrist, value),
        fingers=fingers,
    )
