import unittest

from src.cross_genre_evaluation import build_blind_rows


class CrossGenreEvaluationTests(unittest.TestCase):
    def test_blind_rows_hide_rank_and_keep_a_join_key(self) -> None:
        rows = [
            {
                "seed_artist_name": "Ado",
                "primary_genre": "pop",
                "rank": str(rank),
                "candidate_artist_name": f"Candidate {rank}",
                "candidate_artist_mbid": f"mbid-{rank}",
                "similarity_score": str(1 / rank),
                "common_listener_count": str(100 - rank),
                "confidence": "high",
            }
            for rank in range(1, 11)
        ]
        blind, key = build_blind_rows(rows, random_state=42)
        self.assertEqual(10, len(blind))
        self.assertNotIn("rank", blind[0])
        self.assertEqual("", blind[0]["human_rating_0_1_2"])
        self.assertEqual(
            {row["blind_candidate_id"] for row in blind},
            {row["blind_candidate_id"] for row in key},
        )
        self.assertNotEqual(
            list(range(1, 11)),
            [row["rank"] for row in key],
        )


if __name__ == "__main__":
    unittest.main()
