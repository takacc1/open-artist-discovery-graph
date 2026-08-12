import unittest

from src.cross_genre_metrics import aggregate_groups, data_tier, join_ratings


class CrossGenreMetricsTests(unittest.TestCase):
    def test_data_tiers(self) -> None:
        self.assertEqual("low_under_30", data_tier(29))
        self.assertEqual("limited_30_to_99", data_tier(30))
        self.assertEqual("sufficient_100_plus", data_tier(100))

    def test_join_ratings_uses_blind_id_and_checks_identity(self) -> None:
        key = [{
            "blind_candidate_id": "CROSS-001",
            "seed_artist_name": "Ado",
            "candidate_artist_mbid": "abc",
            "rank": "1",
            "similarity_score": "0.5",
            "common_listener_count": "4",
        }]
        completed = [{
            "blind_candidate_id": "CROSS-001",
            "seed_artist_name": "Ado",
            "candidate_artist_mbid": "ABC",
            "human_rating_0_1_2": 2,
        }]
        rows = join_ratings(key, completed)
        self.assertEqual(1, rows[0]["rank"])
        self.assertEqual(2, rows[0]["human_rating_0_1_2"])

    def test_aggregate_groups_uses_macro_artist_average(self) -> None:
        rows = [
            {
                "primary_genre": "pop", "rated_count": 10,
                "rating_2_count": 8, "rating_1_count": 2, "rating_0_count": 0,
                "relaxed_precision_at_10": 1.0, "strict_precision_at_10": 0.8,
                "strict_precision_at_5": 1.0, "mean_rating": 1.8, "ndcg_at_10": 0.9,
            },
            {
                "primary_genre": "pop", "rated_count": 10,
                "rating_2_count": 2, "rating_1_count": 6, "rating_0_count": 2,
                "relaxed_precision_at_10": 0.8, "strict_precision_at_10": 0.2,
                "strict_precision_at_5": 0.0, "mean_rating": 1.0, "ndcg_at_10": 0.5,
            },
        ]
        result = aggregate_groups(rows, "primary_genre")[0]
        self.assertEqual(2, result["artist_count"])
        self.assertEqual(0.9, result["relaxed_precision_at_10"])
        self.assertEqual(0.1, result["rating_0_rate"])


if __name__ == "__main__":
    unittest.main()
