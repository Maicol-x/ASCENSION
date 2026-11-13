import json
import os
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import store
from scrapers.utils import build_feature


class CompactGenerationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "cameras.db")
        self.conn = store.connect(self.db_path)
        store.upsert_features(self.conn, [
            build_feature(
                str(index), f"Camera {index}", 34.05, -118.25,
                f"https://synthetic.invalid/{index}.jpg",
                "traffic", "Test City", "US", "test",
            )
            for index in range(3)
        ])
        self.core_path = os.path.join(self.temp.name, "public", "cameras.core.json")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def read_json(self, path):
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    def test_manifest_points_to_complete_matching_generation(self):
        self.assertEqual(store.export_compact(self.conn, self.core_path), 3)
        manifest_path = os.path.join(self.temp.name, "public", "cameras.manifest.json")
        manifest = self.read_json(manifest_path)
        generation = manifest["generation"]
        generation_root = os.path.join(
            self.temp.name, "public", "cameras.generations", generation
        )
        core = self.read_json(os.path.join(generation_root, "core.json"))
        labels = self.read_json(os.path.join(generation_root, "labels.json"))
        detail = self.read_json(os.path.join(generation_root, "detail", "0.json"))

        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["count"], 3)
        self.assertEqual(core["generation"], generation)
        self.assertEqual(labels["generation"], generation)
        self.assertEqual(detail["generation"], generation)
        self.assertEqual(core["count"], labels["count"])
        self.assertEqual(len(detail["id"]), 3)

    def test_failed_manifest_switch_keeps_previous_generation_readable(self):
        store.export_compact(self.conn, self.core_path)
        manifest_path = os.path.join(self.temp.name, "public", "cameras.manifest.json")
        previous = self.read_json(manifest_path)
        original_write = store._atomic_write_json

        def fail_manifest(path, obj, **kwargs):
            if os.path.basename(path) == "cameras.manifest.json":
                raise OSError("controlled manifest publication failure")
            return original_write(path, obj, **kwargs)

        with mock.patch.object(store, "_atomic_write_json", side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, "manifest publication failure"):
                store.export_compact(self.conn, self.core_path)

        current = self.read_json(manifest_path)
        self.assertEqual(current, previous)
        old_core = os.path.join(
            self.temp.name, "public", "cameras.generations",
            previous["generation"], "core.json",
        )
        self.assertEqual(self.read_json(old_core)["generation"], previous["generation"])

    def test_subsequent_export_switches_manifest_without_removing_old_snapshot(self):
        store.export_compact(self.conn, self.core_path)
        manifest_path = os.path.join(self.temp.name, "public", "cameras.manifest.json")
        previous = self.read_json(manifest_path)
        store.upsert_features(self.conn, [
            build_feature(
                "new", "New camera", 35, -119, "https://synthetic.invalid/new.jpg",
                "traffic", "New City", "US", "test",
            )
        ])

        store.export_compact(self.conn, self.core_path)
        current = self.read_json(manifest_path)
        self.assertNotEqual(current["generation"], previous["generation"])
        old_core = os.path.join(
            self.temp.name, "public", "cameras.generations",
            previous["generation"], "core.json",
        )
        new_core = os.path.join(
            self.temp.name, "public", "cameras.generations",
            current["generation"], "core.json",
        )
        self.assertTrue(os.path.isfile(old_core))
        self.assertEqual(self.read_json(new_core)["count"], 4)


if __name__ == "__main__":
    unittest.main()
