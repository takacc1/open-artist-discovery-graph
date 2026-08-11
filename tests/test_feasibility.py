import unittest
from pathlib import Path

from src.feasibility import MusicBrainzResolver, normalize_name, read_rows, score_candidate


class FeasibilityTests(unittest.TestCase):
    def test_validation_set_has_baseline_plus_cross_genre_artists(self) -> None:
        rows = read_rows(Path("data/validation_artists.csv"))
        self.assertEqual(90, len(rows))
        self.assertEqual(90, len({normalize_name(row["artist_name"]) for row in rows}))

    def test_validation_set_contains_kpop_girl_group_cohort(self) -> None:
        rows = read_rows(Path("data/validation_artists.csv"))
        kpop_names = {row["artist_name"] for row in rows if row["category"] == "kpop_girl_group"}
        self.assertEqual(10, len(kpop_names))
        self.assertTrue({"aespa", "IVE", "TWICE"}.issubset(kpop_names))

    def test_validation_set_contains_balanced_cross_genre_cohort(self) -> None:
        rows = read_rows(Path("data/validation_artists.csv"))
        cross_genre = [row for row in rows if row["category"] == "cross_genre_validation"]
        self.assertEqual(15, len(cross_genre))
        genre_counts = {}
        for row in cross_genre:
            genre = row["primary_genre"]
            genre_counts[genre] = genre_counts.get(genre, 0) + 1
        self.assertEqual(
            {
                "pop": 4,
                "rock_alternative": 2,
                "hiphop": 2,
                "rnb": 2,
                "electronic": 2,
                "classical_soundtrack": 2,
                "jazz": 1,
            },
            genre_counts,
        )

    def test_validation_set_contains_youth_popular_cohort(self) -> None:
        rows = read_rows(Path("data/validation_artists.csv"))
        youth = [row for row in rows if row["category"] == "youth_popular_validation"]
        self.assertEqual(26, len(youth))
        self.assertTrue(
            {"tuki.", "Official髭男dism", "Stray Kids", "NCT", "CORTIS"}.issubset(
                {row["artist_name"] for row in youth}
            )
        )
        self.assertTrue(all(row["manual_identity_status"] == "confirmed" for row in youth))
        self.assertTrue(all(row["manual_mbid"] for row in youth))

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

    def test_manual_mbid_pins_a_reviewed_candidate(self) -> None:
        resolver = MusicBrainzResolver.__new__(MusicBrainzResolver)
        resolver.search = lambda _name: [
            {
                "id": "wrong",
                "name": "Aphex Twin",
                "score": 100,
                "type": "Person",
                "life-span": {},
            },
            {
                "id": "correct",
                "name": "Aphex Twin",
                "score": 90,
                "country": "GB",
                "type": "Person",
                "life-span": {},
            },
        ]
        result = resolver.resolve(
            {
                "artist_name": "Aphex Twin",
                "expected_country": "GB",
                "expected_type": "Person",
                "expected_ended": "false",
                "disambiguation_hint": "",
                "category": "international",
                "manual_mbid": "correct",
                "manual_identity_status": "confirmed",
            }
        )
        self.assertEqual("manual_confirmed", result["resolution_status"])
        self.assertEqual("correct", result["resolved_mbid"])


if __name__ == "__main__":
    unittest.main()
