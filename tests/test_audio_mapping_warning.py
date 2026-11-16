"""Regression tests for default stream profile audio warnings."""

import unittest
from unittest.mock import patch

from src.config import _audio_copy_may_drop_tracks, _build_warnings_fields


class AudioMappingWarningTests(unittest.TestCase):
    def test_safe_mappings(self):
        profiles = [
            "-fflags +genpts+discardcorrupt -user_agent {userAgent} -i {streamUrl} "
            "-map 0 -max_interleave_delta 100K -c copy -mpegts_flags "
            "+pat_pmt_at_frames+resend_headers+initial_discontinuity -f mpegts pipe:1",
            "-i input -map 0:v -map 0:a -c:a copy output",
            "-i input -map '0:a?' -map 0:v -codec:a copy output",
            "-i input -map 0? -acodec copy output",
        ]
        for profile in profiles:
            with self.subTest(profile=profile):
                self.assertFalse(_audio_copy_may_drop_tracks(profile))

    def test_unsafe_or_uncertain_mappings(self):
        profiles = [
            "-i input -c copy output",
            "-i input -map 0:a:0 -c:a copy output",
            "-i input -map 1:a -c:a copy output",
            "-i input -map 0 -map -0:a:1 -c copy output",
            "-i input -map 0 -an -c copy output",
            "-i input -metadata 'comment=-map 0' -c copy output",
            "-i input -map 0 -c copy 'unterminated",
        ]
        for profile in profiles:
            with self.subTest(profile=profile):
                self.assertTrue(_audio_copy_may_drop_tracks(profile))

    def test_no_copy(self):
        for profile in ("", "-i input -c:a aac output", "-metadata 'title=-c copy'"):
            with self.subTest(profile=profile):
                self.assertFalse(_audio_copy_may_drop_tracks(profile))

    def test_warning_fields(self):
        for profile, expected in (("-i input -map 0 -c copy output", False),
                                  ("-i input -c copy output", True)):
            with self.subTest(profile=profile), patch(
                "src.config._get_multiview_profile_params", return_value=profile
            ):
                warnings = _build_warnings_fields({})
                audio = [field for field in warnings if field["id"] == "_warn_audio_copy"]
                self.assertEqual(bool(audio), expected)
                if audio:
                    self.assertIn("-map 0:v", audio[0]["description"])


if __name__ == "__main__":
    unittest.main()
