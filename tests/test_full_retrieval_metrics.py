import unittest

from src.full_retrieval import METHODS
from src.full_retrieval_metrics import build_result, join_ratings


class FullRetrievalMetricsTests(unittest.TestCase):
    def test_joins_one_human_rating_to_every_method_occurrence(self) -> None:
        completed = [
            {
                "seed_artist_name": "aespa",
                "candidate_artist_mbid": "IVE",
                "human_rating_0_1_2": 2,
                "human_notes": "near",
            }
        ]
        keys = [
            {
                "method": method,
                "seed_artist_name": "aespa",
                "candidate_artist_mbid": "ive",
                "rank": "1",
                "score": "0.5",
                "evidence_count": "10",
            }
            for method in METHODS
        ]
        joined = join_ratings(keys, completed)
        self.assertEqual(3, len(joined))
        self.assertTrue(all(row["human_rating_0_1_2"] == 2 for row in joined))

    def test_scores_each_methods_independent_top_ten(self) -> None:
        completed = []
        keys = []
        for rank in range(1, 11):
            candidate = f"candidate-{rank}"
            completed.append(
                {
                    "seed_artist_name": "aespa",
                    "candidate_artist_mbid": candidate,
                    "human_rating_0_1_2": 2 if rank <= 5 else 1,
                }
            )
            for method in METHODS:
                keys.append(
                    {
                        "method": method,
                        "seed_artist_name": "aespa",
                        "candidate_artist_mbid": candidate,
                        "rank": str(rank),
                        "score": str(1 / rank),
                        "evidence_count": "",
                    }
                )
        result, scored, by_artist = build_result(
            keys,
            {"blank_count": 0, "new_row_count": 10, "new_rated_count": 10},
            completed,
            {},
            bootstrap_samples=100,
        )
        self.assertEqual(30, len(scored))
        self.assertEqual(3, len(by_artist))
        self.assertEqual(1.0, result["methods"]["cosine_shrinkage"]["macro_strict_precision_at_5"])
        self.assertEqual(1.0, result["methods"]["implicit_als"]["macro_ndcg_at_10"])

    def test_missing_rating_fails(self) -> None:
        with self.assertRaises(ValueError):
            join_ratings(
                [
                    {
                        "seed_artist_name": "aespa",
                        "candidate_artist_mbid": "missing",
                        "rank": "1",
                        "score": "0.1",
                        "evidence_count": "",
                    }
                ],
                [],
            )


if __name__ == "__main__":
    unittest.main()
