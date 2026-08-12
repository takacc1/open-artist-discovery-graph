import csv
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.api import create_app
from src.serving_db import build_database


SEED_A = "00000000-0000-4000-8000-000000000001"
SEED_B = "00000000-0000-4000-8000-000000000002"
SHARED = "00000000-0000-4000-8000-000000000003"
ONLY_A = "00000000-0000-4000-8000-000000000004"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        database = root / "serving.sqlite3"
        coverage = root / "coverage.csv"
        similarity = root / "similarity.csv"
        write_csv(
            coverage,
            ["artist_name", "resolved_mbid", "resolved_name", "resolved_country", "resolved_type"],
            [
                {"artist_name": "Seed A", "resolved_mbid": SEED_A, "resolved_name": "Seed A", "resolved_country": "JP", "resolved_type": "Group"},
                {"artist_name": "Seed B", "resolved_mbid": SEED_B, "resolved_name": "Seed B", "resolved_country": "KR", "resolved_type": "Group"},
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
                {"seed_artist_name": "Seed A", "seed_artist_mbid": SEED_A, "candidate_artist_name": "Shared", "candidate_artist_mbid": SHARED, "similarity_score": 0.8, "cosine_similarity": 0.9, "common_listener_count": 30, "confidence": "high", "rank": 1},
                {"seed_artist_name": "Seed A", "seed_artist_mbid": SEED_A, "candidate_artist_name": "Only A", "candidate_artist_mbid": ONLY_A, "similarity_score": 0.7, "cosine_similarity": 0.8, "common_listener_count": 8, "confidence": "low", "rank": 2},
                {"seed_artist_name": "Seed B", "seed_artist_mbid": SEED_B, "candidate_artist_name": "Shared", "candidate_artist_mbid": SHARED, "similarity_score": 0.6, "cosine_similarity": 0.7, "common_listener_count": 20, "confidence": "medium", "rank": 1},
            ],
        )
        build_database(
            database,
            coverage,
            [similarity],
            model_version="test-api-v1",
            window_days=30,
        )
        self.client = TestClient(create_app(database))

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def test_health_and_data_version(self) -> None:
        health = self.client.get("/health")
        self.assertEqual(200, health.status_code)
        self.assertEqual("test-api-v1", health.json()["active_model"])

        version = self.client.get("/data-version")
        self.assertEqual(200, version.status_code)
        self.assertEqual(3, version.json()["edge_count"])
        self.assertEqual(2, version.json()["source_artist_count"])

    def test_search_and_artist_detail(self) -> None:
        search = self.client.get("/artists/search", params={"q": "Seed"})
        self.assertEqual(200, search.status_code)
        self.assertEqual(["Seed A", "Seed B"], [row["name"] for row in search.json()])
        self.assertNotIn("artist_id", search.json()[0])

        detail = self.client.get(f"/artists/{SEED_A}")
        self.assertEqual(200, detail.status_code)
        self.assertEqual("Seed A", detail.json()["name"])
        self.assertEqual(2, detail.json()["neighbor_count"])

    def test_neighbors_uses_active_model(self) -> None:
        response = self.client.get(f"/artists/{SEED_A}/neighbors")
        self.assertEqual(200, response.status_code)
        self.assertEqual(["Shared", "Only A"], [row["artist_name"] for row in response.json()])
        self.assertEqual("test-api-v1", response.json()[0]["model_version"])

    def test_bridge_recommendation_combines_seed_edges(self) -> None:
        response = self.client.post(
            "/recommendations",
            json={"seed_artist_mbids": [SEED_A, SEED_B], "mode": "bridge", "limit": 10},
        )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("bridge", payload["mode"])
        self.assertEqual("Shared", payload["recommendations"][0]["artist_name"])
        self.assertEqual(2, payload["recommendations"][0]["matched_seed_count"])

    def test_request_validation_rejects_bad_mbid_and_limit(self) -> None:
        bad_mbid = self.client.post(
            "/recommendations",
            json={"seed_artist_mbids": ["not-an-mbid"], "mode": "near", "limit": 10},
        )
        self.assertEqual(422, bad_mbid.status_code)

        bad_limit = self.client.get("/artists/search", params={"q": "Seed", "limit": 100})
        self.assertEqual(422, bad_limit.status_code)

    def test_missing_database_returns_service_unavailable(self) -> None:
        client = TestClient(create_app(Path(self.temp.name) / "missing.sqlite3"))
        try:
            response = client.get("/health")
        finally:
            client.close()
        self.assertEqual(503, response.status_code)


if __name__ == "__main__":
    unittest.main()
