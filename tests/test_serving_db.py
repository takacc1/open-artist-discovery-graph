import csv
import tempfile
import unittest
from pathlib import Path

from src.serving_db import (
    build_database,
    database_stats,
    get_neighbors,
    recommend,
    search_artists,
)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class ServingDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = root / "serving.sqlite3"
        coverage = root / "coverage.csv"
        similarity = root / "similarity.csv"
        write_csv(
            coverage,
            ["artist_name", "resolved_mbid", "resolved_name", "resolved_country", "resolved_type"],
            [
                {"artist_name": "Seed A", "resolved_mbid": "seed-a", "resolved_name": "Seed A", "resolved_country": "JP", "resolved_type": "Group"},
                {"artist_name": "Seed B", "resolved_mbid": "seed-b", "resolved_name": "Seed B", "resolved_country": "KR", "resolved_type": "Group"},
            ],
        )
        fields = [
            "seed_artist_name", "seed_artist_mbid", "candidate_artist_name",
            "candidate_artist_mbid", "similarity_score", "cosine_similarity",
            "common_listener_count", "confidence", "rank",
        ]
        write_csv(
            similarity,
            fields,
            [
                {"seed_artist_name": "Seed A", "seed_artist_mbid": "seed-a", "candidate_artist_name": "Shared", "candidate_artist_mbid": "shared", "similarity_score": 0.8, "cosine_similarity": 0.9, "common_listener_count": 30, "confidence": "high", "rank": 1},
                {"seed_artist_name": "Seed A", "seed_artist_mbid": "seed-a", "candidate_artist_name": "Only A", "candidate_artist_mbid": "only-a", "similarity_score": 0.7, "cosine_similarity": 0.8, "common_listener_count": 8, "confidence": "low", "rank": 2},
                {"seed_artist_name": "Seed B", "seed_artist_mbid": "seed-b", "candidate_artist_name": "Shared", "candidate_artist_mbid": "shared", "similarity_score": 0.6, "cosine_similarity": 0.7, "common_listener_count": 20, "confidence": "medium", "rank": 1},
                {"seed_artist_name": "Seed B", "seed_artist_mbid": "seed-b", "candidate_artist_name": "Only B", "candidate_artist_mbid": "only-b", "similarity_score": 0.5, "cosine_similarity": 0.6, "common_listener_count": 15, "confidence": "medium", "rank": 2},
            ],
        )
        build_database(
            self.database,
            coverage,
            [similarity],
            model_version="test-v1",
            window_days=30,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_builds_graph_tables_and_queries_neighbors(self) -> None:
        stats = database_stats(self.database)
        self.assertEqual(5, stats["artist_count"])
        self.assertEqual(4, stats["edge_count"])
        self.assertEqual("test-v1", stats["active_model"])
        neighbors = get_neighbors(self.database, "seed-a")
        self.assertEqual(["Shared", "Only A"], [row["artist_name"] for row in neighbors])
        self.assertEqual(30, neighbors[0]["window_days"])

    def test_searches_known_artists(self) -> None:
        self.assertEqual("Seed A", search_artists(self.database, "Seed A")[0]["name"])

    def test_bridge_prefers_candidate_connected_to_both_seeds(self) -> None:
        result = recommend(self.database, ["seed-a", "seed-b"], mode="bridge")
        self.assertEqual("Shared", result["recommendations"][0]["artist_name"])
        self.assertEqual(2, result["recommendations"][0]["matched_seed_count"])

    def test_missing_seed_is_reported_without_fake_recommendations(self) -> None:
        result = recommend(self.database, ["not-in-db"])
        self.assertEqual(["not-in-db"], result["missing_seed_mbids"])
        self.assertEqual([], result["recommendations"])


if __name__ == "__main__":
    unittest.main()
