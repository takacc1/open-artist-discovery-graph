import unittest

from src.metadata_evidence_metrics import (
    EXPECTED_CANDIDATE_COUNTS,
    V4_EXPECTED_CANDIDATE_COUNTS,
    build_result,
    validate_rows,
)


def rating_rows(default_rating=2):
    rows = []
    for artist, count in EXPECTED_CANDIDATE_COUNTS.items():
        for rank in range(1, count + 1):
            rows.append(
                {
                    "seed_artist_name": artist,
                    "candidate_artist_name": f"{artist} candidate {rank}",
                    "rank": rank,
                    "recommendation_source": "metadata_fallback",
                    "rating_status": "要入力" if rank > 5 else "引継ぎ",
                    "human_rating_0_1_2": default_rating,
                }
            )
    return rows


def v4_rating_rows(default_rating=2):
    rows = []
    for artist, count in V4_EXPECTED_CANDIDATE_COUNTS.items():
        for rank in range(1, count + 1):
            rows.append(
                {
                    "seed_artist_name": artist,
                    "candidate_artist_name": f"{artist} candidate {rank}",
                    "rank": rank,
                    "recommendation_source": "behavior_metadata_blend",
                    "rating_status": "要入力" if rank > 8 else "引継ぎ",
                    "human_rating_0_1_2": default_rating,
                }
            )
    return rows


class MetadataEvidenceMetricsTests(unittest.TestCase):
    def test_all_twos_pass_with_six_candidate_safety_stop(self):
        result = build_result(rating_rows())

        self.assertEqual("PASS", result["decision"])
        self.assertEqual(0.95, result["coverage"]["candidate_fill_rate"])
        self.assertEqual(76, result["overall"]["rated_count"])

    def test_eight_zero_ratings_keep_model_in_review(self):
        rows = rating_rows()
        for row in rows[:8]:
            row["human_rating_0_1_2"] = 0

        result = build_result(rows)

        self.assertEqual("REVIEW", result["decision"])
        self.assertAlmostEqual(8 / 76, result["overall"]["rating_0_rate"])

    def test_rank_outside_configured_safety_stop_is_rejected(self):
        rows = rating_rows()
        rows.append(
            {
                "seed_artist_name": "ヤングスキニー",
                "candidate_artist_name": "Padded weak candidate",
                "rank": 7,
                "recommendation_source": "behavior_low_data",
                "rating_status": "要入力",
                "human_rating_0_1_2": 1,
            }
        )

        with self.assertRaises(ValueError):
            validate_rows(rows)

    def test_v4_passes_with_four_zero_ratings(self):
        rows = v4_rating_rows()
        for row in rows[:4]:
            row["human_rating_0_1_2"] = 0

        result = build_result(rows, profile="v4")

        self.assertEqual("PASS", result["decision"])
        self.assertEqual(0.925, result["coverage"]["candidate_fill_rate"])
        self.assertAlmostEqual(4 / 74, result["overall"]["rating_0_rate"])


if __name__ == "__main__":
    unittest.main()
