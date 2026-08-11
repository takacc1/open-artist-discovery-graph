import unittest
from pathlib import Path

from src.feasibility import normalize_name, read_rows, score_candidate


class FeasibilityTests(unittest.TestCase):
    def test_validation_set_has_exactly_fifty_unique_artists(self) -> None:
        rows = read_rows(Path("data/validation_artists.csv"))
        self.assertEqual(50, len(rows))

    def test_validation_set_contains_kpop_girl_group_cohort(self) -> None:
        rows = read_rows(Path("data/validation_artists.csv"))
        kpop_names = {row["artist_name"] for row in rows if row["category"] == "kpop_girl_group"}
        self.assertEqual(10, len(kpop_names))
        self.assertTrue({"aespa", "IVE", "TWICE"}.issubset(kpop_names))

    def test_name_normalization_handles_width_case_and_punctuation(self) -> None:
        self.assertEqual(normalize_name("ＢＯØＷＹ"), normalize_name("boøwy"))

    def test_expected_identity_beats_wrong_same_name_artist(self) -> None:
        row = {
            "artist_name": "MONO",
            "expected_country": "JP",
            "expected_type": "Group",
            "expected_ended": "false",
            "disambiguation_hint": "Japanese post-rock band",
        }
        correct = {
            "name": "MONO",
            "score": 100,
            "country": "JP",
            "type": "Group",
            "life-span": {"ended": False},
            "disambiguation": "Japanese post-rock band",
        }
        wrong = {
            "name": "Mono",
            "score": 100,
            "country": "GB",
            "type": "Group",
            "life-span": {"ended": True},
            "disambiguation": "British electronic duo",
        }
        self.assertGreater(score_candidate(row, correct).total, score_candidate(row, wrong).total)


if __name__ == "__main__":
    unittest.main()
