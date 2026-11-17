"""Tests for safe replacement of generated plugin files."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.storage import atomic_write_text, copy_legacy_file, migrate_backgrounds, migrate_legacy_files


class AtomicWriteTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "multiview.m3u"

    def test_creates_and_replaces_complete_utf8_content(self):
        atomic_write_text(self.path, "first\n")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "first\n")
        atomic_write_text(self.path, "new café\n")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "new café\n")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_replace_failure_preserves_old_file_and_cleans_temporary(self):
        self.path.write_text("old", encoding="utf-8")
        with patch("src.storage.os.replace", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(OSError, "disk error"):
                atomic_write_text(self.path, "new")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "old")
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_temporary_name_collision_preserves_existing_file(self):
        collision = self.path.parent / ".multiview-existing.tmp"
        collision.write_text("other writer", encoding="utf-8")
        with patch("src.storage.secrets.token_hex", side_effect=["existing", "new"]):
            atomic_write_text(self.path, "content")
        self.assertEqual(collision.read_text(encoding="utf-8"), "other writer")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "content")


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.code = self.root / "code"
        self.data = self.root / "data"
        (self.code / "style_backgrounds").mkdir(parents=True)

    def test_background_copy_is_repeatable_and_keeps_settings(self):
        source = self.code / "style_backgrounds" / "background.png"
        source.write_bytes(b"old image")
        settings = {"multiview_custom_layouts": {"layout": {"background_image": source.name}}}
        self.assertEqual(migrate_backgrounds(settings, self.code, self.data), [])
        destination = self.data / "style_backgrounds" / source.name
        self.assertEqual(destination.read_bytes(), b"old image")
        destination.write_bytes(b"new image")
        self.assertEqual(migrate_backgrounds(settings, self.code, self.data), [])
        self.assertEqual(destination.read_bytes(), b"new image")
        self.assertEqual(source.read_bytes(), b"old image")
        self.assertEqual(settings["multiview_custom_layouts"]["layout"]["background_image"], source.name)

    def test_missing_file_is_reported_without_modifying_settings(self):
        settings = {"multiview_custom_layouts": {"layout": {"background_image": "missing.png"}}}
        self.assertEqual(migrate_backgrounds(settings, self.code, self.data), ["missing.png"])
        self.assertEqual(settings["multiview_custom_layouts"]["layout"]["background_image"], "missing.png")

    def test_invalid_filename_and_symlink_destination_are_rejected(self):
        settings = {"multiview_custom_layouts": {"layout": {"background_image": "../outside"}}}
        with self.assertRaises(ValueError):
            migrate_backgrounds(settings, self.code, self.data)
        settings["multiview_custom_layouts"]["layout"]["background_image"] = "image.png"
        destination = self.data / "style_backgrounds" / "image.png"
        destination.parent.mkdir(parents=True)
        destination.symlink_to(self.root / "outside")
        with self.assertRaises(OSError):
            migrate_backgrounds(settings, self.code, self.data)

    def test_failed_copy_preserves_source_and_does_not_publish_partial_file(self):
        source = self.code / "style_backgrounds" / "image.png"
        source.write_bytes(b"image")
        destination = self.data / "style_backgrounds" / "image.png"
        with patch("src.storage.shutil.copyfileobj", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(OSError, "disk error"):
                copy_legacy_file(source, destination)
        self.assertEqual(source.read_bytes(), b"image")
        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.iterdir()), [])

    def test_generated_files_migrate_without_overwriting_new_data(self):
        (self.code / "multiview.m3u").write_text("legacy", encoding="utf-8")
        result = migrate_legacy_files({}, self.code, self.data)
        self.assertEqual(result["files"], {"multiview.m3u": "copied", "multiview_epg.xml": "missing"})
        destination = self.data / "multiview.m3u"
        destination.write_text("updated", encoding="utf-8")
        result = migrate_legacy_files({}, self.code, self.data)
        self.assertEqual(result["files"]["multiview.m3u"], "existing")
        self.assertEqual(destination.read_text(encoding="utf-8"), "updated")


if __name__ == "__main__":
    unittest.main()
