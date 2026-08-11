import unittest

from src.window_revaluation import build_revaluation


class WindowRevaluationTests(unittest.TestCase):
    def test_carries_existing_rating_and_marks_new_candidate(self) -> None:
        similarity = [
            {"seed_artist_name": "Seed", "candidate_artist_name": "Old", "candidate_artist_mbid": "old", "rank": "1", "similarity_score": "0.8", "common_listener_count": "20", "confidence": "medium"},
            {"seed_artist_name": "Seed", "candidate_artist_name": "New", "candidate_artist_mbid": "new", "rank": "2", "similarity_score": "0.7", "common_listener_count": "10", "confidence": "medium"},
        ]
        validation = [{"artist_name": "Seed", "category": "test", "primary_genre": "pop"}]
        previous = [{"seed_artist_name": "Seed", "candidate_artist_mbid": "old", "human_rating_0_1_2": "2", "human_notes": "good"}]
        summary_payload = {"similarity": {"target_artists_with_listeners": 1, "target_artists_with_recommendations": 1, "artists": [{"artist_name": "Seed", "listener_count_in_window": 25, "recommendation_count": 2}]}}
        visible, key_rows, summary = build_revaluation(
            similarity,
            validation,
            previous,
            summary_payload,
            category="test",
            limit=2,
            random_state=1,
        )
        by_candidate = {row["candidate_artist_name"]: row for row in visible}
        self.assertEqual("2", by_candidate["Old"]["human_rating_0_1_2"])
        self.assertEqual("no", by_candidate["Old"]["needs_rating"])
        self.assertEqual("yes", by_candidate["New"]["needs_rating"])
        self.assertEqual(1, summary["carried_rating_count"])
        self.assertEqual(1, summary["new_rating_count"])
        self.assertEqual("low_under_30", summary["per_artist"][0]["data_tier_30d"])
        self.assertEqual(2, len(key_rows))


if __name__ == "__main__":
    unittest.main()
