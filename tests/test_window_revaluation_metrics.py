import unittest

from src.window_revaluation_metrics import build_result, validate_ranked_rows


class WindowRevaluationMetricsTests(unittest.TestCase):
    def make_rows(self):
        key_rows = []
        completed_rows = []
        per_artist = []
        counter = 1
        for artist_index in range(26):
            seed = f"Artist {artist_index:02d}"
            per_artist.append(
                {
                    "artist_name": seed,
                    "primary_genre": "kpop" if artist_index < 7 else "pop",
                    "listener_count_30d": 100 + artist_index,
                }
            )
            for rank in range(1, 11):
                blind_id = f"Y30-{counter:03d}"
                mbid = f"candidate-{counter:03d}"
                key_rows.append(
                    {
                        "blind_candidate_id": blind_id,
                        "seed_artist_name": seed,
                        "candidate_artist_mbid": mbid,
                        "rank": rank,
                        "similarity_score": 0.5,
                        "common_listener_count": 50,
                    }
                )
                completed_rows.append(
                    {
                        "blind_candidate_id": blind_id,
                        "seed_artist_name": seed,
                        "candidate_artist_mbid": mbid,
                        "human_rating_0_1_2": 2 if rank <= 5 else 1,
                    }
                )
                counter += 1
        summary = {
            "artist_count": 26,
            "artists_with_30d_listeners": 26,
            "artists_with_30d_recommendations": 26,
            "low_under_30_count": 0,
            "carried_rating_count": 100,
            "new_rating_count": 160,
            "per_artist": per_artist,
        }
        return key_rows, completed_rows, summary

    def test_accepts_26_complete_top_tens(self) -> None:
        key_rows, _, _ = self.make_rows()
        validate_ranked_rows(key_rows)

    def test_build_result_scores_all_260_candidates(self) -> None:
        key_rows, completed_rows, summary = self.make_rows()
        result, scored = build_result(key_rows, completed_rows, summary)
        self.assertEqual("PASS", result["decision"])
        self.assertEqual(260, len(scored))
        self.assertEqual(1.0, result["coverage"]["recommendation_coverage"])
        self.assertEqual(0.5, result["overall"]["macro_strict_precision_at_10"])


if __name__ == "__main__":
    unittest.main()
