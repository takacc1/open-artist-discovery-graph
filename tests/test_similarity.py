import unittest

from src.similarity import (
    artist_credit_records,
    artist_records,
    confidence_label,
    pseudonymize_user_id,
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


if __name__ == "__main__":
    unittest.main()
