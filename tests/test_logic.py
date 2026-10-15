"""Synthetic logic tests; do not require a Windows desktop or network."""
import sys
import types
import unittest
from unittest.mock import MagicMock

import numpy as np
import cv2

# The tested tracking functions don't use hardware APIs; replace import-only
# modules missing on a headless build server.
sys.modules.setdefault("mss", types.ModuleType("mss"))
pynput_stub = types.ModuleType("pynput")
pynput_stub.mouse = MagicMock()
pynput_stub.keyboard = MagicMock()
sys.modules.setdefault("pynput", pynput_stub)

from app import pick_random_delay, predicted_position, validate_number
from square_tracking import detect_squares, SquareTrack, associate_and_update


class LogicTests(unittest.TestCase):
    def test_random_range_endpoints(self):
        self.assertEqual(pick_random_delay(7.95, 8.05, lambda lo, hi: lo), 7.95)
        self.assertEqual(pick_random_delay(7.95, 8.05, lambda lo, hi: hi), 8.05)
        with self.assertRaises(ValueError):
            pick_random_delay(9, 8)

    def test_prediction(self):
        self.assertEqual(predicted_position(100, 200, 50, -25, .04), (102, 199))

    def test_validators(self):
        self.assertEqual(validate_number("1.5", "Delay", 0, 5), 1.5)
        for bad in ("nan", "inf", "wrong", "-1"):
            with self.assertRaises(ValueError):
                validate_number(bad, "Delay", 0, 5)

    def test_blue_and_white_squares_detected(self):
        bgr = np.zeros((180, 300, 3), dtype=np.uint8)
        cv2.rectangle(bgr, (30, 30), (64, 64), (255, 0, 0), -1)
        cv2.rectangle(bgr, (150, 90), (185, 125), (255, 255, 255), -1)
        detections = detect_squares(bgr)
        self.assertEqual(len(detections), 2)
        self.assertEqual({d["color"] for d in detections}, {"blue", "white"})

    def test_square_blue_identity_survives_color_change(self):
        blue = {"x": 20.0,"y": 20.0,"side": 25.0,"color": "blue"}
        white = {"x": 120.0,"y": 20.0,"side": 25.0,"color": "white"}
        tracks = [SquareTrack(1, blue), SquareTrack(2, white)]
        now = tracks[0].last_seen
        for index in range(1,7):
            now += .03
            positions = [{"x": 20 + 2*index,"y":20,"side":25,"color":"white"},
                         {"x":120-2*index,"y":20,"side":25,"color":"white"}]
            associate_and_update(tracks, positions, .03, 25, now)
        self.assertTrue(tracks[0].was_blue)
        self.assertFalse(tracks[1].was_blue)
        self.assertGreater(tracks[0].state[0], 20)

    def test_occlusion_marks_uncertain(self):
        tracks = [SquareTrack(1, {"x":40,"y":40,"side":20,"color":"blue"}),
                  SquareTrack(2, {"x":48,"y":40,"side":20,"color":"white"})]
        now = tracks[0].last_seen
        for _ in range(15):
            now += .03
            associate_and_update(tracks, [], .03, 20, now)
        self.assertTrue(all(t.uncertain for t in tracks))


if __name__ == '__main__':
    unittest.main()
