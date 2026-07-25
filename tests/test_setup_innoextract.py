import tempfile
import unittest
import zipfile
from pathlib import Path

from setup_innoextract import extract_zip, expected_digest, safe_member_path, select_asset


class InnoextractSetupTests(unittest.TestCase):
    def test_select_asset_requires_the_single_windows_zip(self):
        asset = {
            "name": "innoextract670.zip",
            "browser_download_url": "https://github.com/UserUnknownFactor/innoextract_win/releases/download/670/innoextract670.zip",
            "digest": "sha256:" + "a" * 64,
        }
        self.assertIs(select_asset({"assets": [asset]}), asset)
        self.assertEqual(expected_digest(asset), "a" * 64)

    def test_select_asset_rejects_ambiguous_assets(self):
        with self.assertRaises(RuntimeError):
            select_asset({"assets": [{"name": "innoextract-a.zip"}, {"name": "innoextract-b.zip"}]})

    def test_zip_extraction_rejects_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "tool.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("innoextract.exe", b"tool")
                output.writestr("../outside.txt", b"escape")
            with self.assertRaises(RuntimeError):
                extract_zip(archive, root / "out")
            self.assertFalse((root / "outside.txt").exists())

    def test_safe_member_path_rejects_windows_absolute_names(self):
        with self.assertRaises(RuntimeError):
            safe_member_path(Path("/tmp/out"), "C:/Windows/system32/tool.exe")


if __name__ == "__main__":
    unittest.main()
