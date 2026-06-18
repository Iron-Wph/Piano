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
        wrist_inset: float | None = None,
        palm_depth: float | None = None,
        finger_spacing: float | None = None,
        hover_height: float | None = None,
    ) -> None:
        self.keyboard = keyboard
        self.wrist_inset = (
            max(4.0, keyboard.white_key_height * 0.07)
            if wrist_inset is None
            else wrist_inset
        )
        self.palm_depth = (
            max(12.0, min(34.0, keyboard.white_key_height * 0.16))
            if palm_depth is None
            else palm_depth
        )
        self.finger_spacing = (
            keyboard.white_key_width * 0.9
            if finger_spacing is None
            else finger_spacing
        )
        self.hover_height = (
            max(8.0, min(22.0, keyboard.white_key_height * 0.1))
            if hover_height is None
            else hover_height
        )

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
                center_x = self.center_x_for_targets(hand_value, finger_targets)
            else:
                center_x = self.keyboard.width / 2.0
        wrist_y = self.keyboard.bottom - self.wrist_inset
        wrist = self._clamp_point((float(center_x), wrist_y))
        fingers: dict[int, list[Point]] = {}
        for finger in range(1, 6):
            natural_tip = self._natural_tip(hand_value, finger, center_x)
            target = finger_targets.get(finger, natural_tip)
            if finger in finger_targets and not pressed:
                target = (target[0], target[1] + self.hover_height)
            fingers[finger] = self._finger_joints(hand_value, finger, wrist, target)
        return HandPose(wrist=wrist, fingers=fingers)

    def center_x_for_targets(
        self,
        hand: Hand | str,
        finger_targets: Mapping[int, Point],
    ) -> float:
        """Infer the wrist center by removing each finger's natural key offset."""

        hand_value = Hand(hand)
        if hand_value == Hand.UNKNOWN:
            raise ValueError("cannot plan a center for an unknown hand")
        if not finger_targets:
            return self.keyboard.width / 2.0
        direction = 1.0 if hand_value == Hand.RIGHT else -1.0
        centers = [
            target[0] - direction * (finger - 3) * self.finger_spacing
            for finger, target in finger_targets.items()
        ]
        return sum(centers) / len(centers)

    def _natural_tip(self, hand: Hand, finger: int, center_x: float) -> Point:
        direction = 1.0 if hand == Hand.RIGHT else -1.0
        # Finger 3 anchors the hand; shorter fingers stay closer to the lower wrist.
        x = center_x + direction * (finger - 3) * self.finger_spacing
        scale = min(1.0, self.keyboard.white_key_height / 200.0)
        length_adjustment = {
            1: 16.0,
            2: 4.0,
            3: 0.0,
            4: 3.0,
            5: 13.0,
        }[finger] * scale
        y = self.keyboard.top + self.keyboard.white_key_height * 0.58 + length_adjustment
        return (x, y)

    def _finger_joints(self, hand: Hand, finger: int, wrist: Point, tip: Point) -> list[Point]:
        direction = 1.0 if hand == Hand.RIGHT else -1.0
        base_spread = direction * (finger - 3) * self.finger_spacing * 0.72
        base = (wrist[0] + base_spread, wrist[1] - self.palm_depth)
        bend = self.palm_depth * (0.42 + abs(finger - 3) * 0.07)
        proximal = (
            base[0] + (tip[0] - base[0]) * 0.36,
            base[1] + (tip[1] - base[1]) * 0.33 + bend,
        )
        distal = (
            base[0] + (tip[0] - base[0]) * 0.72,
            base[1] + (tip[1] - base[1]) * 0.70 + bend * 0.55,
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
                min(self.keyboard.bottom - 1.0, point[1]),
            ),
        )
