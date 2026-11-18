"""Fractional broadcast frame rate selection and encoder configuration."""

import unittest
from fractions import Fraction

from src import config, parameters, server


class OutputFrameRateTests(unittest.TestCase):
    def test_fractional_options_reach_ffmpeg_unchanged(self):
        field = next(field for field in config._VIDEO_OUTPUT_FIELDS if field["id"] == "output_fps")
        options = {option["value"]: option["label"] for option in field["options"]}
        for value, label, rate in (
            ("30000/1001", "29.97 fps", Fraction(30000, 1001)),
            ("60000/1001", "59.94 fps", Fraction(60000, 1001)),
        ):
            with self.subTest(value=value):
                self.assertEqual(options[value], label)
                self.assertEqual(server.fps_string({"output_fps": value}), value)
                self.assertEqual(parameters.fps_fraction(value), rate)
                cmd = parameters.build_encoder_cmd({"fps": value}, 1920, 1080, [])
                self.assertEqual(cmd[cmd.index("-r") + 1], value)


if __name__ == "__main__":
    unittest.main()
