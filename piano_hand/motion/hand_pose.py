"""Parameterized readable two-dimensional piano-hand skeletons."""

from __future__ import annotations

from collections.abc import Mapping

from piano_hand.models.motion import HandPose, Point
from piano_hand.models.note import Hand
from piano_hand.motion.keyboard_geometry import KeyboardGeometry


class HandPoseFactory:
    """Create mirrored left/right skeletons for chord targets."""

    def __init__(
        self,
        keyboard: KeyboardGeometry,
        *,
        wrist_clearance: float = 92.0,
        palm_depth: float = 34.0,
        finger_spacing: float = 18.0,
        hover_height: float = 22.0,
    ) -> None:
        self.keyboard = keyboard
        self.wrist_clearance = wrist_clearance
        self.palm_depth = palm_depth
        self.finger_spacing = finger_spacing
        self.hover_height = hover_height

    def neutral_pose(self, hand: Hand | str, center_x: float | None = None) -> HandPose:
        hand_value = Hand(hand)
        if center_x is None:
            center_x = self.keyboard.width * (0.34 if hand_value == Hand.LEFT else 0.66)
        return self.pose_for_targets(hand_value, {}, center_x=center_x, pressed=False)

    def pose_for_targets(
        self,
        hand: Hand | str,
        finger_targets: Mapping[int, Point],
        *,
        center_x: float | None = None,
        pressed: bool,
    ) -> HandPose:
        """Build a hand pose, placing active fingertips on their target keys."""

        hand_value = Hand(hand)
        if hand_value == Hand.UNKNOWN:
            raise ValueError("cannot create a pose for an unknown hand")
        if center_x is None:
            if finger_targets:
                center_x = sum(point[0] for point in finger_targets.values()) / len(finger_targets)
            else:
                center_x = self.keyboard.width / 2.0
        wrist_y = self.keyboard.top - self.wrist_clearance
        wrist = self._clamp_point((float(center_x), wrist_y))
        fingers: dict[int, list[Point]] = {}
        for finger in range(1, 6):
            natural_tip = self._natural_tip(hand_value, finger, center_x)
            target = finger_targets.get(finger, natural_tip)
            if finger in finger_targets and not pressed:
                target = (target[0], target[1] - self.hover_height)
            fingers[finger] = self._finger_joints(hand_value, finger, wrist, target)
        return HandPose(wrist=wrist, fingers=fingers)

    def _natural_tip(self, hand: Hand, finger: int, center_x: float) -> Point:
        direction = 1.0 if hand == Hand.RIGHT else -1.0
        # Finger 3 anchors the hand; the thumb stays slightly closer to the wrist.
        x = center_x + direction * (finger - 3) * self.finger_spacing
        length_adjustment = {1: -16.0, 2: -4.0, 3: 0.0, 4: -3.0, 5: -13.0}[finger]
        y = self.keyboard.top + self.keyboard.white_key_height * 0.68 + length_adjustment
        return (x, y)

    def _finger_joints(self, hand: Hand, finger: int, wrist: Point, tip: Point) -> list[Point]:
        direction = 1.0 if hand == Hand.RIGHT else -1.0
        base_spread = direction * (finger - 3) * self.finger_spacing * 0.72
        base = (wrist[0] + base_spread, wrist[1] + self.palm_depth)
        bend = 15.0 + abs(finger - 3) * 2.5
        proximal = (
            base[0] + (tip[0] - base[0]) * 0.36,
            base[1] + (tip[1] - base[1]) * 0.33 - bend,
        )
        distal = (
            base[0] + (tip[0] - base[0]) * 0.72,
            base[1] + (tip[1] - base[1]) * 0.70 - bend * 0.55,
        )
        return [
            self._clamp_point(base),
            self._clamp_point(proximal),
            self._clamp_point(distal),
            self._clamp_point((float(tip[0]), float(tip[1]))),
        ]

    def _clamp_point(self, point: Point) -> Point:
        return (
            max(0.0, min(self.keyboard.width - 1.0, point[0])),
            max(
                0.0,
                min(self.keyboard.top + self.keyboard.white_key_height - 1.0, point[1]),
            ),
        )
