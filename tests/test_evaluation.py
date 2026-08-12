import unittest

from src.evaluation import build_evaluation_rows


class EvaluationTests(unittest.TestCase):
    def test_builds_blank_human_rating_rows_for_selected_category(self) -> None:
        similarity = [
            {
                "seed_artist_name": "aespa",
                "rank": "1",
                "candidate_artist_name": "IVE",
                "candidate_artist_mbid": "candidate",
                "similarity_score": "0.5",
                "common_listener_count": "100",
                "confidence": "high",
            },
            {
                "seed_artist_name": "Radiohead",
                "rank": "1",
                "candidate_artist_name": "The Smile",
            },
        ]
        validation = [
            {
                "artist_name": "aespa",
                "category": "kpop_girl_group",
                "primary_genre": "kpop",
            },
            {"artist_name": "Radiohead", "category": "international"},
        ]
        rows = build_evaluation_rows(similarity, validation, "kpop_girl_group")
        self.assertEqual(1, len(rows))
        self.assertEqual("IVE", rows[0]["candidate_artist_name"])
        self.assertEqual("kpop", rows[0]["primary_genre"])
        self.assertEqual("", rows[0]["human_rating_0_1_2"])


if __name__ == "__main__":
    unittest.main()
