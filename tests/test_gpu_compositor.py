"""Unit tests for automatic hardware-compositor selection and graph building."""

import subprocess
import unittest
from unittest.mock import patch

from src import parameters


class GPUCompositorTests(unittest.TestCase):
    def setUp(self):
        parameters._COMPOSITOR_PROBES.clear()
        parameters._COMPOSITOR_FAILURES.clear()
        parameters._SCALER_PROBES.clear()
        parameters._SCALER_FAILURES.clear()

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

    def test_cuda_scaler_requires_scale_and_pad_filters(self):
        filters = subprocess.CompletedProcess([], 0, stdout=" scale_cuda\n pad_cuda\n")
        with patch("src.parameters.subprocess.run", side_effect=[filters, subprocess.CompletedProcess([], 0)]):
            scaler = parameters.gpu_scaler(
                {"video_encoder": "h264_nvenc"}, {"name": "cuda", "init": []},
            )
        self.assertEqual(scaler["name"], "cuda")

    def test_cuda_scaler_falls_back_without_padding_filter(self):
        filters = subprocess.CompletedProcess([], 0, stdout=" scale_cuda\n")
        with patch("src.parameters.subprocess.run", return_value=filters):
            self.assertIsNone(parameters.gpu_scaler(
                {"video_encoder": "h264_nvenc"}, {"name": "cuda", "init": []},
            ))
        self.assertEqual(
            parameters.gpu_scaler_failure({"video_encoder": "h264_nvenc"}),
            "missing FFmpeg filter(s): pad_cuda",
        )

    def test_vaapi_scaler_requires_scale_and_padding_filters(self):
        filters = subprocess.CompletedProcess([], 0, stdout=" scale_vaapi\n pad_vaapi\n")
        with patch("src.parameters.subprocess.run", side_effect=[filters, subprocess.CompletedProcess([], 0)]):
            scaler = parameters.gpu_scaler(
                {"video_encoder": "h264_vaapi"}, {"name": "vaapi", "init": []},
            )
        self.assertEqual(scaler["name"], "vaapi")

    def test_qsv_scaler_requires_scale_and_overlay_filters(self):
        filters = subprocess.CompletedProcess([], 0, stdout=" scale_qsv\n overlay_qsv\n")
        with patch("src.parameters.subprocess.run", side_effect=[filters, subprocess.CompletedProcess([], 0)]):
            scaler = parameters.gpu_scaler(
                {"video_encoder": "h264_qsv"}, {"name": "qsv", "init": []},
            )
        self.assertEqual(scaler["name"], "qsv")

    def test_cuda_scaler_graph_contains_aspect_preserving_scale_and_pad(self):
        graph = parameters._gpu_filtergraph(
            [(3, 1920, 1080)],
            [{"x": 0, "y": 0, "w": 960, "h": 1080, "valign": "center", "halign": "center"}],
            960, 1080, {"name": "cuda", "scale_tiles": True},
        )
        self.assertIn("scale_cuda=w=960:h=540", graph)
        self.assertIn("pad_cuda=w=960:h=1080:x=0:y=270", graph)

    def test_tile_content_rect_honors_edge_alignment(self):
        self.assertEqual(
            parameters.tile_content_rect(
                1920, 1080,
                {"w": 960, "h": 1080, "valign": "bottom", "halign": "right"},
            ),
            (960, 540, 0, 540),
        )

    def test_vaapi_scaler_graph_contains_scale_and_pad(self):
        graph = parameters._gpu_filtergraph(
            [(3, 1920, 1080)],
            [{"x": 0, "y": 0, "w": 960, "h": 1080, "valign": "center", "halign": "center"}],
            960, 1080, {"name": "vaapi", "scale_tiles": True},
        )
        self.assertIn("scale_vaapi=w=960:h=540", graph)
        self.assertIn("pad_vaapi=w=960:h=1080:x=0:y=270", graph)

    def test_qsv_scaler_graph_uses_a_gpu_canvas_for_padding(self):
        graph = parameters._gpu_filtergraph(
            [(3, 1920, 1080)],
            [{"x": 0, "y": 0, "w": 960, "h": 1080, "valign": "center", "halign": "center"}],
            960, 1080, {"name": "qsv", "scale_tiles": True},
        )
        self.assertIn("scale_qsv=w=960:h=540", graph)
        self.assertIn("[1:v]format=nv12,hwupload=extra_hw_frames=64[q0]", graph)
        self.assertIn("overlay_qsv=x=0:y=270", graph)

    def test_qsv_scaler_reserves_canvas_inputs_before_audio(self):
        cfg = {
            "fps": "30", "bitrate": 8000, "video_encoder": "h264_qsv",
            "preset": "medium",
            "tiles": [{"x": 0, "y": 0, "w": 1280, "h": 720}],
        }
        cmd = parameters.build_encoder_cmd(
            cfg, 1280, 720, [7], [(3, 1920, 1080)],
            {"name": "qsv", "init": [], "scale_tiles": True},
        )
        self.assertIn("color=black:s=1280x720:r=30", cmd)
        self.assertIn("2:a:0", cmd)


if __name__ == "__main__":
    unittest.main()
