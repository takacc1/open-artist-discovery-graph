import unittest

import numpy as np

from src.algorithm_comparison import (
    SessionConfig,
    add_method_ranks,
    bootstrap_ndcg_deltas,
    cosine_factor_score,
    score_session_event_groups,
)


class AlgorithmComparisonTests(unittest.TestCase):
    def test_session_score_uses_30_minute_boundaries(self) -> None:
        pairs = {("aespa", "ive")}
        events = {
            1: [
                (0, ("aespa",)),
                (60, ("ive",)),
                (4_000, ("aespa",)),
            ],
            2: [(0, ("aespa",)), (2_000, ("ive",))],
        }
        score = score_session_event_groups(events, pairs, SessionConfig())["aespa", "ive"]
        self.assertEqual(1, score["common_sessions"])
        self.assertEqual(3, score["seed_sessions"])
        self.assertEqual(2, score["candidate_sessions"])

    def test_factor_score_is_cosine(self) -> None:
        self.assertAlmostEqual(
            1.0,
            cosine_factor_score(np.array([1.0, 1.0]), np.array([2.0, 2.0])),
        )
        self.assertAlmostEqual(
            0.0,
            cosine_factor_score(np.array([1.0, 0.0]), np.array([0.0, 1.0])),
        )

    def test_add_method_ranks_is_per_seed(self) -> None:
        rows = [
            {"seed_artist_mbid": "a", "candidate_artist_mbid": "x", "score": 0.1},
            {"seed_artist_mbid": "a", "candidate_artist_mbid": "y", "score": 0.2},
            {"seed_artist_mbid": "b", "candidate_artist_mbid": "z", "score": 0.3},
        ]
        add_method_ranks(rows, "score", "method_rank")
        ranks = {(row["seed_artist_mbid"], row["candidate_artist_mbid"]): row["method_rank"] for row in rows}
        self.assertEqual(1, ranks["a", "y"])
        self.assertEqual(2, ranks["a", "x"])
        self.assertEqual(1, ranks["b", "z"])

    def test_bootstrap_reports_paired_ndcg_delta(self) -> None:
        methods = {
            "cosine_shrinkage": {
                "by_artist": [
                    {"seed_artist_name": "a", "ndcg_at_10": 0.8},
                    {"seed_artist_name": "b", "ndcg_at_10": 0.7},
                ]
            },
            "session_cooccurrence": {
                "by_artist": [
                    {"seed_artist_name": "a", "ndcg_at_10": 0.9},
                    {"seed_artist_name": "b", "ndcg_at_10": 0.8},
                ]
            },
        }
        result = bootstrap_ndcg_deltas(methods, samples=100, random_state=1)
        comparison = result["comparisons"]["session_cooccurrence"]
        self.assertAlmostEqual(0.1, comparison["mean_ndcg_delta"])
        self.assertEqual(1.0, comparison["bootstrap_probability_better"])


if __name__ == "__main__":
    unittest.main()
