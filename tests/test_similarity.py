import json
import unittest
import sqlite3
import tempfile
from pathlib import Path

from src.similarity import (
    SimilarityConfig,
    artist_credit_records,
    artist_records,
    compute_similarities_sequential,
    confidence_label,
    load_spark_manifest,
    pseudonymize_user_id,
    similarity_output_name,
    valid_candidate_name,
    weight,
)


class SimilarityTests(unittest.TestCase):
    def test_artist_records_uses_mbids_without_exposing_username(self) -> None:
        listen = {
            "user_id": 123,
            "user_name": "must-not-be-returned",
            "track_metadata": {
                "artist_name": "aespa",
                "additional_info": {
                    "artist_mbids": ["F1E2D3C4-AAAA-BBBB-CCCC-123456789000"],
                    "artist_names": ["aespa"],
                },
            },
        }
        self.assertEqual(
            [("f1e2d3c4-aaaa-bbbb-cccc-123456789000", "aespa")],
            artist_records(listen),
        )

    def test_log_weight_caps_extreme_listen_counts(self) -> None:
        self.assertEqual(weight(100, 100), weight(10_000, 100))
        self.assertLess(weight(1, 100), weight(10, 100))

    def test_similarity_output_name_reflects_limit(self) -> None:
        self.assertEqual("similarity_top10.csv", similarity_output_name(10))
        self.assertEqual("similarity_top50.csv", similarity_output_name(50))

    def test_spark_artist_credit_mbids_are_normalized_and_deduplicated(self) -> None:
        self.assertEqual(
            [("abc", "aespa")],
            artist_credit_records(" aespa ", ["ABC", "abc", None]),
        )
        self.assertEqual(
            [("abc", ""), ("def", "")],
            artist_credit_records("aespa feat. IVE", ["ABC", "DEF"]),
        )
        self.assertEqual([], artist_credit_records("aespa", None))

    def test_user_id_is_keyed_before_it_reaches_the_work_database(self) -> None:
        key = b"test-key"
        self.assertEqual(pseudonymize_user_id(123, key), pseudonymize_user_id(123, key))
        self.assertNotEqual(pseudonymize_user_id(123, key), pseudonymize_user_id(124, key))
        self.assertNotEqual(pseudonymize_user_id(123, key), pseudonymize_user_id(123, b"other-key"))

    def test_confidence_is_based_on_aggregate_common_listener_count(self) -> None:
        self.assertEqual("low", confidence_label(2))
        self.assertEqual("medium", confidence_label(10))
        self.assertEqual("high", confidence_label(30))

    def test_placeholder_artist_names_are_not_recommendable(self) -> None:
        self.assertFalse(valid_candidate_name("[unknown]"))
        self.assertFalse(valid_candidate_name("Various Artists"))
        self.assertTrue(valid_candidate_name("Unknown Mortal Orchestra"))

    def test_loads_ordered_sources_from_window_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(
                json.dumps({"sources": [{"path": "/tmp/day-1.tar"}, {"path": "/tmp/day-2.tar"}]}),
                encoding="utf-8",
            )
            self.assertEqual(
                [Path("/tmp/day-1.tar"), Path("/tmp/day-2.tar")],
                load_spark_manifest(manifest),
            )

    def test_sequential_similarity_ranks_shared_listeners_without_raw_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "aggregate.sqlite3"
            connection = sqlite3.connect(database)
            connection.execute(
                "CREATE TABLE user_artist ("
                "user_key BLOB, artist_mbid TEXT, artist_name TEXT, listen_count INTEGER, "
                "PRIMARY KEY (user_key, artist_mbid)) WITHOUT ROWID"
            )
            connection.executemany(
                "INSERT INTO user_artist VALUES (?, ?, ?, ?)",
                [
                    (b"u1", "seed", "Seed", 10),
                    (b"u1", "near", "Near", 8),
                    (b"u1", "far", "Far", 1),
                    (b"u2", "seed", "Seed", 5),
                    (b"u2", "near", "Near", 4),
                ],
            )
            connection.execute(
                "CREATE INDEX user_artist_by_artist ON user_artist (artist_mbid, user_key)"
            )
            connection.commit()
            connection.close()

            rows, summary = compute_similarities_sequential(
                database,
                {"seed": {"artist_name": "Seed", "expected_similar": "Near"}},
                SimilarityConfig(min_common_listeners=1, limit=2),
            )
            self.assertEqual("Near", rows[0]["candidate_artist_name"])
            self.assertEqual(2, rows[0]["common_listener_count"])
            self.assertEqual(1, summary["target_artists_with_recommendations"])


if __name__ == "__main__":
    unittest.main()
