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


class GeoJsonStreamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = store.connect(os.path.join(self.temp.name, "cameras.db"))
        store.upsert_features(self.conn, [
            build_feature(
                "one", "Camera One", 34.05, -118.25,
                "https://synthetic.invalid/one.jpg",
                "traffic", "Test City", "US", "test",
            ),
            build_feature(
                "two", "Camera Two", 35.0, -119.0,
                "https://synthetic.invalid/two.jpg",
                "traffic", "Other City", "CA", "other",
            ),
        ])
        self.path = os.path.join(self.temp.name, "cameras.geojson")

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_streamed_export_preserves_features_and_metadata(self):
        self.assertEqual(store.export_geojson(self.conn, self.path), 2)
        with open(self.path, "r", encoding="utf-8") as file:
            data = json.load(file)

        self.assertEqual(data["type"], "FeatureCollection")
        self.assertEqual(data["metadata"]["total_cameras"], 2)
        self.assertEqual(data["metadata"]["sources"], ["other", "test"])
        self.assertEqual(data["metadata"]["per_source_counts"], {"test": 1, "other": 1})
        features = {feature["properties"]["id"]: feature for feature in data["features"]}
        self.assertEqual(set(features), {"test_one", "other_two"})
        self.assertEqual(features["test_one"]["geometry"]["type"], "Point")
        self.assertEqual(features["test_one"]["geometry"]["coordinates"], [-118.25, 34.05])
        self.assertEqual(features["test_one"]["properties"]["feedUrl"],
                         "https://synthetic.invalid/one.jpg")

    def test_stream_failure_preserves_previous_export(self):
        with open(self.path, "w", encoding="utf-8") as file:
            file.write("previous export")

        with mock.patch.object(
            store, "_feature_from_row", side_effect=RuntimeError("controlled failure")
        ):
            with self.assertRaisesRegex(RuntimeError, "controlled failure"):
                store.export_geojson(self.conn, self.path)

        with open(self.path, "r", encoding="utf-8") as file:
            self.assertEqual(file.read(), "previous export")
        self.assertFalse(any(name.startswith(".tmp-") for name in os.listdir(self.temp.name)))


if __name__ == "__main__":
    unittest.main()
