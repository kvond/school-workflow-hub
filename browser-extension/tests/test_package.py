import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

spec = importlib.util.spec_from_file_location("package_extension", Path(__file__).parents[1] / "package_extension.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class PackageTest(unittest.TestCase):
    def test_exact_origins_and_deterministic_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            one, first = packager.package("https://hub.example.test", "https://school.example.test", root / "one")
            _, second = packager.package("https://hub.example.test", "https://school.example.test", root / "two")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            manifest = json.loads((one / "manifest.json").read_text())
            self.assertEqual(manifest["host_permissions"], ["https://hub.example.test/*", "https://school.example.test/*"])
            # Session storage only: the Hub's lists, in memory until the browser closes.
            self.assertEqual(manifest["permissions"], ["storage"])
            scripts = {entry["matches"][0]: entry["js"] for entry in manifest["content_scripts"]}
            self.assertEqual(scripts["https://hub.example.test/*"][-1], "hub-entry.js")
            self.assertEqual(scripts["https://school.example.test/*"][-2:], ["entry.js", "ic-entry.js"])
            self.assertNotIn("externally_connectable", manifest)
            self.assertNotIn("web_accessible_resources", manifest)
            with ZipFile(first) as zipped:
                self.assertEqual(set(zipped.namelist()), set(packager.FILES) | {"manifest.json", "config.js"})

    def test_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "existing"
            output.mkdir()
            (output / "preserve").write_text("keep")
            with self.assertRaises(ValueError):
                packager.package("https://hub.example.test", "https://school.example.test", output)
            self.assertEqual((output / "preserve").read_text(), "keep")

    def test_unbounded_origins_rejected(self):
        for value in ["http://hub.example.test", "https://hub.example.test/", "https://hub.example.test/path", "https://hub.example.test:443", "https://user:password@hub.example.test", "https://*.example.test", "https://hub.example.test?x=1"]:
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                packager.origin(value)


if __name__ == "__main__":
    unittest.main()
