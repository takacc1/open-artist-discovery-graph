import math
import unittest

from src.evaluation_metrics import ndcg, parse_rating, summarize_ranked_rows


class EvaluationMetricsTests(unittest.TestCase):
    def test_parses_only_supported_ratings(self) -> None:
        self.assertIsNone(parse_rating(""))
        self.assertEqual(0, parse_rating("0"))
        self.assertEqual(0, parse_rating(0))
        self.assertEqual(2, parse_rating(2))
        with self.assertRaises(ValueError):
            parse_rating("3")

    def test_ndcg_rewards_putting_rating_two_first(self) -> None:
        self.assertEqual(1.0, ndcg([2, 1, 0]))
        self.assertLess(ndcg([0, 1, 2]), 1.0)

    def test_summarizes_ranked_ratings(self) -> None:
        rows = [
            {"seed_artist_name": "aespa", "rank": "1", "human_rating_0_1_2": "2"},
            {"seed_artist_name": "aespa", "rank": "2", "human_rating_0_1_2": "1"},
            {"seed_artist_name": "aespa", "rank": "3", "human_rating_0_1_2": "0"},
        ]
        summary = summarize_ranked_rows(rows, top_k=3)
        self.assertEqual({"2": 1, "1": 1, "0": 1}, summary["rating_distribution"])
        self.assertTrue(math.isclose(2 / 3, summary["macro_relaxed_precision_at_10"]))
        self.assertEqual(1.0, summary["macro_ndcg_at_10"])


if __name__ == "__main__":
    unittest.main()
