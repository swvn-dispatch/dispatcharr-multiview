"""Unit tests for automatic hardware-compositor selection and graph building."""

import subprocess
import unittest
from unittest.mock import patch

from src import parameters


class GPUCompositorTests(unittest.TestCase):
    def setUp(self):
        parameters._COMPOSITOR_PROBES.clear()
        parameters._COMPOSITOR_FAILURES.clear()

    def test_hardware_profile_uses_matching_compositor_after_probe(self):
        completed = subprocess.CompletedProcess([], 0, stdout=" hwupload_cuda\n overlay_cuda\n")
        with patch("src.parameters.subprocess.run", side_effect=[completed, subprocess.CompletedProcess([], 0)]):
            compositor = parameters.gpu_compositor({"video_encoder": "h264_nvenc"})

        self.assertEqual(compositor["name"], "cuda")

    def test_missing_filter_falls_back_to_cpu(self):
        completed = subprocess.CompletedProcess([], 0, stdout=" hwupload_cuda\n")
        with patch("src.parameters.subprocess.run", return_value=completed):
            self.assertIsNone(parameters.gpu_compositor({"video_encoder": "h264_nvenc"}))
        self.assertEqual(
            parameters.gpu_compositor_failure({"video_encoder": "h264_nvenc"}),
            "missing FFmpeg filter(s): overlay_cuda",
        )

    def test_background_always_uses_cpu_composition(self):
        with patch("src.parameters.subprocess.run") as run:
            self.assertIsNone(parameters.gpu_compositor({
                "video_encoder": "h264_vaapi", "background": "/tmp/background.png",
            }))
        run.assert_not_called()

    def test_cuda_graph_overlays_tiles_in_layout_order(self):
        graph = parameters._gpu_filtergraph(
            [(3, 640, 360), (4, 640, 360)],
            [{"x": 0, "y": 0}, {"x": 640, "y": 0}], 1280, 720,
            {"name": "cuda"},
        )
        self.assertIn("[2:v]hwupload_cuda[base]", graph)
        self.assertIn("overlay_cuda=x=0:y=0[o0]", graph)
        self.assertIn("overlay_cuda=x=640:y=0[mvout]", graph)

    def test_vaapi_graph_keeps_custom_tile_positions(self):
        graph = parameters._gpu_filtergraph(
            [(3, 640, 360), (4, 320, 180)],
            [{"x": 14, "y": 22}, {"x": 700, "y": 444}], 1280, 720,
            {"name": "vaapi"},
        )
        self.assertIn("xstack_vaapi=inputs=2:layout=14_22|700_444", graph)

    def test_cuda_compositor_does_not_force_a_software_encoder_format(self):
        cfg = {
            "fps": "30", "bitrate": 8000, "video_encoder": "h264_nvenc",
            "preset": "p4", "tiles": [{"x": 0, "y": 0}],
        }
        cmd = parameters.build_encoder_cmd(
            cfg, 1280, 720, [], [(3, 1280, 720)], {"name": "cuda", "init": []},
        )
        encoder_args = cmd[cmd.index("-c:v"):]
        self.assertNotIn("-pix_fmt", encoder_args)


if __name__ == "__main__":
    unittest.main()
