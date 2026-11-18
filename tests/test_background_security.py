"""Background uploads and saved paths must stay in plugin data."""

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from src import server, storage
from src.dash import api


class BackgroundSecurityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.instance = types.SimpleNamespace(data_dir=str(self.root / "data"), storage=storage)
        self.verify = patch.object(api, "_verify_token", return_value=True)
        self.server = patch.object(api, "_server", self.instance, create=True)
        self.verify.start()
        self.server.start()
        self.addCleanup(self.verify.stop)
        self.addCleanup(self.server.stop)

    def request(self, method, body=None, query=""):
        raw = json.dumps(body).encode() if body is not None else b""
        environ = {"REQUEST_METHOD": method, "QUERY_STRING": query,
                   "CONTENT_LENGTH": str(len(raw)), "wsgi.input": BytesIO(raw)}
        response = Mock()
        payload = b"".join(api.handle_style_background(environ, response))
        return response.call_args.args[0], payload

    def test_upload_rejects_path_components_in_style_id(self):
        for style_id in ("../outside", "/absolute", r"..\outside", "bad\x00id"):
            with self.subTest(style_id=style_id):
                status, _ = self.request("POST", {"style_id": style_id, "filename": "image.png",
                                                   "data_base64": base64.b64encode(b"image").decode()})
                self.assertEqual(status, "400 Bad Request")
        self.assertFalse((self.root / "outside_image.png").exists())

    def test_valid_upload_and_read(self):
        settings = {"multiview_custom_layouts": {"style-1": {}}}
        with patch.object(api, "_get_settings", return_value=settings), patch.object(api, "_save_settings") as save:
            status, _ = self.request("POST", {"style_id": "style-1", "filename": "image.png",
                                               "data_base64": base64.b64encode(b"image").decode()})
        self.assertEqual(status, "200 OK")
        saved = save.call_args.args[0]["multiview_custom_layouts"]
        with patch.object(api, "_get_settings", return_value={"multiview_custom_layouts": saved}):
            status, payload = self.request("GET", query="style_id=style-1")
        self.assertEqual(status, "200 OK")
        self.assertEqual(payload, b"image")

    def test_saved_path_and_symlink_cannot_escape_on_read(self):
        outside = self.root / "secret"
        outside.write_bytes(b"secret")
        backgrounds = Path(self.instance.data_dir) / "style_backgrounds"
        backgrounds.mkdir(parents=True)
        (backgrounds / "link.png").symlink_to(outside)
        for filename in ("../../secret", str(outside), "link.png"):
            with self.subTest(filename=filename), patch.object(api, "_get_settings", return_value={
                "multiview_custom_layouts": {"style": {"background_image": filename}}
            }):
                status, payload = self.request("GET", query="style_id=style")
                self.assertEqual(status, "400 Bad Request")
                self.assertNotIn(b"secret", payload)

    def test_upload_does_not_follow_existing_symlink(self):
        outside = self.root / "secret"
        outside.write_bytes(b"original")
        backgrounds = Path(self.instance.data_dir) / "style_backgrounds"
        backgrounds.mkdir(parents=True)
        (backgrounds / "style_image.png").symlink_to(outside)
        status, _ = self.request("POST", {"style_id": "style", "filename": "image.png",
                                           "data_base64": base64.b64encode(b"replacement").decode()})
        self.assertEqual(status, "400 Bad Request")
        self.assertEqual(outside.read_bytes(), b"original")

    def test_background_directory_symlink_is_rejected(self):
        data = Path(self.instance.data_dir)
        data.mkdir()
        (data / "style_backgrounds").symlink_to(self.root, target_is_directory=True)
        status, _ = self.request("POST", {"style_id": "style", "filename": "image.png",
                                           "data_base64": base64.b64encode(b"image").decode()})
        self.assertEqual(status, "400 Bad Request")
        self.assertFalse((self.root / "style_image.png").exists())

    def test_worker_ignores_traversal_background(self):
        instance = server.MultiviewServer("127.0.0.1", 9292, self.instance.data_dir)
        settings = {"multiview_custom_layouts": {"style": {"background_image": "../../secret"}}}
        tiles = [{"id": 1, "name": "One"}, {"id": 2, "name": "Two"}]
        rects = [(0, 0, 100, 100, "center", "center"), (100, 0, 100, 100, "center", "center")]
        with patch.object(server._layouts, "tile_rects", return_value=rects), self.assertLogs(server.logger, level="WARNING"):
            cfg = instance._worker_config(tiles, "custom:style", "0", settings)
        self.assertIsNone(cfg["background"])


if __name__ == "__main__":
    unittest.main()
