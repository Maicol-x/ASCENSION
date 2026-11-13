import contextlib
import io
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import scraper
import store
from scrapers.utils import build_feature


_connect = store.connect


def _camera(camera_id, name, source="caltrans"):
    return build_feature(
        camera_id, name, 34.05, -118.25,
        f"https://synthetic.invalid/{camera_id}.jpg",
        "traffic", "Test City", "US", source,
    )


class ReplaceSourceTests(unittest.TestCase):
    def run_pipeline(self, temp_dir, fetch, allow_empty=False, plugins=None):
        db_path = os.path.join(temp_dir, "cameras.db")
        output_path = os.path.join(temp_dir, "cameras.geojson")
        plugins = plugins or ["caltrans"]
        argv = [
            "scraper.py", "--plugins", *plugins,
            "--replace-source", "--output", output_path,
        ]
        if allow_empty:
            argv.append("--allow-empty-replace")

        modules = {
            scraper.PLUGIN_REGISTRY[plugin]["module"]: SimpleNamespace(fetch=fetch[plugin])
            for plugin in plugins
        }
        with (
            mock.patch.object(scraper.store, "connect",
                              side_effect=lambda *args, **kwargs: _connect(db_path)),
            mock.patch.object(scraper.importlib, "import_module",
                              side_effect=lambda name: modules[name]),
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            scraper.main()

        conn = _connect(db_path)
        try:
            return {
                row["id"]: row["name"]
                for row in conn.execute("SELECT id, name FROM cameras")
            }
        finally:
            conn.close()

    def seed(self, temp_dir, features):
        conn = _connect(os.path.join(temp_dir, "cameras.db"))
        try:
            store.upsert_features(conn, features)
        finally:
            conn.close()

    def test_failed_plugin_preserves_existing_source_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.seed(temp_dir, [_camera("old", "Existing camera")])

            def fail(_config):
                raise RuntimeError("controlled plugin failure")

            rows = self.run_pipeline(temp_dir, {"caltrans": fail})
            self.assertEqual(rows, {"caltrans_old": "Existing camera"})

    def test_empty_result_preserves_existing_rows_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.seed(temp_dir, [_camera("old", "Existing camera")])
            rows = self.run_pipeline(temp_dir, {"caltrans": lambda _config: []})
            self.assertEqual(rows, {"caltrans_old": "Existing camera"})

    def test_explicit_empty_confirmation_replaces_existing_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.seed(temp_dir, [_camera("old", "Existing camera")])
            rows = self.run_pipeline(
                temp_dir, {"caltrans": lambda _config: []}, allow_empty=True
            )
            self.assertEqual(rows, {})

    def test_successful_nonempty_result_replaces_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.seed(temp_dir, [_camera("old", "Existing camera")])
            rows = self.run_pipeline(
                temp_dir,
                {"caltrans": lambda _config: [_camera("new", "Fresh camera")]},
            )
            self.assertEqual(rows, {"caltrans_new": "Fresh camera"})

    def test_failed_source_is_preserved_while_successful_source_is_replaced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.seed(temp_dir, [
                _camera("old-caltrans", "Old Caltrans"),
                _camera("old-drivebc", "Old DriveBC", source="drivebc"),
            ])

            def fail(_config):
                raise RuntimeError("controlled plugin failure")

            rows = self.run_pipeline(
                temp_dir,
                {
                    "caltrans": fail,
                    "drivebc": lambda _config: [
                        _camera("new-drivebc", "New DriveBC", source="drivebc")
                    ],
                },
                plugins=["caltrans", "drivebc"],
            )
            self.assertEqual(rows, {
                "caltrans_old-caltrans": "Old Caltrans",
                "drivebc_new-drivebc": "New DriveBC",
            })


if __name__ == "__main__":
    unittest.main()
