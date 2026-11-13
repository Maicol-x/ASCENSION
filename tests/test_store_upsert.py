import os
import sys
import tempfile
import unittest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import store
from scrapers.utils import build_feature


def camera(camera_id, name):
    return build_feature(
        camera_id, name, 34.05, -118.25,
        f"https://synthetic.invalid/{camera_id}.jpg",
        "traffic", "Test City", "US", "test",
    )


class StoreUpsertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "cameras.db")
        self.conn = store.connect(self.db_path)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_upsert_counts_duplicates_and_preserves_first_seen(self):
        existing = camera("existing", "Original")
        self.assertEqual(store.upsert_features(self.conn, [existing]), (1, 0))
        existing_id = "test_existing"
        self.conn.execute(
            "UPDATE cameras SET first_seen=?, consecutive_failures=2, deactivated_at=? "
            "WHERE id=?",
            ("historic", "2026-01-01T00:00:00Z", existing_id),
        )
        self.conn.commit()

        added, updated = store.upsert_features(self.conn, [
            camera("existing", "Updated"),
            camera("new", "New"),
            camera("new", "Newer"),
            camera("existing", "Latest"),
        ])

        self.assertEqual((added, updated), (1, 3))
        row = self.conn.execute(
            "SELECT name, first_seen, consecutive_failures, deactivated_at "
            "FROM cameras WHERE id=?",
            (existing_id,),
        ).fetchone()
        self.assertEqual(
            (row["name"], row["first_seen"], row["consecutive_failures"],
             row["deactivated_at"]),
            ("Latest", "historic", 0, None),
        )
        new_row = self.conn.execute(
            "SELECT name, first_seen FROM cameras WHERE id='test_new'"
        ).fetchone()
        self.assertEqual(new_row["name"], "Newer")
        self.assertNotEqual(new_row["first_seen"], "historic")

    def test_commit_false_remains_rollbackable(self):
        self.assertEqual(
            store.upsert_features(self.conn, [camera("pending", "Pending")], commit=False),
            (1, 0),
        )
        reader = store.connect(self.db_path)
        try:
            self.assertEqual(
                reader.execute(
                    "SELECT COUNT(*) FROM cameras WHERE id='test_pending'"
                ).fetchone()[0],
                0,
            )
        finally:
            reader.close()

        self.conn.rollback()
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM cameras WHERE id='test_pending'"
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
