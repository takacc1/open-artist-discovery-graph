import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api_service.index import app


SEED = "00000000-0000-4000-8000-000000000001"
RESULT = "00000000-0000-4000-8000-000000000002"
SEARCH = "00000000-0000-4000-8000-000000000003"


def recommendation_result():
    return {
        "mode": "near",
        "model_version": "test-v1",
        "seed_count": 1,
        "missing_seed_mbids": [],
        "recommendations": [
            {
                "artist_mbid": RESULT,
                "artist_name": "Result",
                "score": 0.8,
                "confidence": "high",
                "matched_seed_count": 1,
                "seed_scores": [
                    {
                        "seed_artist_mbid": SEED,
                        "seed_artist_name": "Seed",
                        "similarity_score": 0.8,
                    }
                ],
                "recommendation_source": "behavior_primary",
                "window_days": 30,
                "reason": "入力1組との類似関係を確認",
            }
        ],
    }


class HostedApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    @patch("api_service.index.record_search", return_value=SEARCH)
    @patch("api_service.index.recommend", side_effect=lambda *args, **kwargs: recommendation_result())
    def test_recommendation_records_anonymous_search(self, mocked_recommend, mocked_record):
        response = self.client.post(
            "/recommendations",
            json={"seed_artist_mbids": [SEED], "mode": "near", "limit": 10},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(SEARCH, response.json()["search_id"])
        mocked_recommend.assert_called_once()
        mocked_record.assert_called_once()

    @patch("api_service.index.record_feedback", return_value=True)
    def test_feedback_accepts_only_three_ratings(self, mocked_feedback):
        saved = self.client.post(f"/searches/{SEARCH}/feedback", json={"rating": 2})
        self.assertEqual(200, saved.status_code)
        self.assertTrue(saved.json()["saved"])
        mocked_feedback.assert_called_once_with(SEARCH, 2)

        invalid = self.client.post(f"/searches/{SEARCH}/feedback", json={"rating": 3})
        self.assertEqual(422, invalid.status_code)


if __name__ == "__main__":
    unittest.main()
