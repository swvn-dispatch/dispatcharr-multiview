"""Regression coverage for supported output-resolution presets."""

import unittest

from src import config, layouts


class OutputResolutionTests(unittest.TestCase):
    def test_four_k_is_an_available_output_resolution(self):
        fields = config.build_plugin_fields({})
        resolution = next(field for field in fields if field["id"] == "output_resolution")

        self.assertIn(
            {"value": "3840x2160", "label": "4K (3840×2160)"},
            resolution["options"],
        )

    def test_four_k_auto_grid_gives_each_of_four_tiles_a_1080p_region(self):
        rects = layouts.tile_rects("auto", 4, 3840, 2160)

        self.assertEqual(
            rects,
            [
                (0, 0, 1920, 1080, "bottom", "center"),
                (1920, 0, 1920, 1080, "bottom", "center"),
                (0, 1080, 1920, 1080, "top", "center"),
                (1920, 1080, 1920, 1080, "top", "center"),
            ],
        )

    def test_four_k_featured_rectangles_are_even_and_within_output(self):
        rects = layouts.tile_rects("featured", 4, 3840, 2160)

        self.assertEqual(len(rects), 4)
        for x, y, width, height, _valign, _halign in rects:
            self.assertEqual(x % 2, 0)
            self.assertEqual(y % 2, 0)
            self.assertEqual(width % 2, 0)
            self.assertEqual(height % 2, 0)
            self.assertGreater(width, 0)
            self.assertGreater(height, 0)
            self.assertLessEqual(x + width, 3840)
            self.assertLessEqual(y + height, 2160)


if __name__ == "__main__":
    unittest.main()
