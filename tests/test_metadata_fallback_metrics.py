import unittest

from src.metadata_fallback_metrics import build_result, validate_rows


def rating_rows(default_rating=2):
    rows = []
    for artist_index in range(8):
        for rank in range(1, 11):
            rows.append(
                {
                    "seed_artist_name": f"Artist {artist_index}",
                    "candidate_artist_name": f"Candidate {artist_index}-{rank}",
                    "rank": rank,
                    "recommendation_source": "metadata_fallback",
                    "human_rating_0_1_2": default_rating,
                    "human_notes": "rated",
                }
            )
    return rows


class MetadataFallbackMetricsTests(unittest.TestCase):
    def test_all_twos_pass_every_criterion(self):
        result = build_result(rating_rows())
        self.assertEqual("PASS", result["decision"])
        self.assertEqual(1.0, result["overall"]["macro_strict_precision_at_5"])
        self.assertEqual(80, result["by_source"][0]["rated_count"])

    def test_rating_zero_rate_forces_review(self):
        rows = rating_rows()
        for row in rows[:8]:
            row["human_rating_0_1_2"] = 0
        result = build_result(rows)
        self.assertEqual("REVIEW", result["decision"])
        self.assertEqual(0.1, result["overall"]["rating_0_rate"])

    def test_missing_rank_is_rejected(self):
        rows = rating_rows()[:-1]
        with self.assertRaises(ValueError):
            validate_rows(rows)


if __name__ == "__main__":
    unittest.main()
