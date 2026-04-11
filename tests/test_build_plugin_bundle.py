import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from scripts.build_plugin_bundle import build_bundle


class BuildPluginBundleTests(unittest.TestCase):
    def test_build_bundle_creates_zip_with_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "plugin.zip"

            result = build_bundle(output_path, bundle_root="bundle-root")

            self.assertEqual(result["status"], "ok")
            self.assertTrue(output_path.exists())
            with ZipFile(output_path) as archive:
                members = set(archive.namelist())

            self.assertIn("bundle-root/README.md", members)
            self.assertIn("bundle-root/pyproject.toml", members)
            self.assertIn("bundle-root/.codex-plugin/plugin.json", members)
            self.assertIn("bundle-root/scripts/run_server.py", members)
            self.assertIn("bundle-root/src/ssh_remote_control/remote_cli.py", members)

    def test_build_bundle_excludes_local_cache_and_build_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "plugin.zip"

            result = build_bundle(output_path, bundle_root="bundle-root")

            bundled_files = result["files"]
            self.assertFalse(
                any("__pycache__" in path for path in bundled_files),
                bundled_files,
            )
            self.assertFalse(
                any(path.endswith((".pyc", ".pyo")) for path in bundled_files),
                bundled_files,
            )
            self.assertFalse(
                any(".egg-info/" in path or path.endswith(".egg-info") for path in bundled_files),
                bundled_files,
            )


if __name__ == "__main__":
    unittest.main()
