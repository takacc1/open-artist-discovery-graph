import unittest

from src.recommendation_fallback import (
    WindowFallbackPolicy,
    combine_window_results,
    request_route,
)


def payload(artists):
    return {"similarity": {"artists": artists}}


class RecommendationFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = WindowFallbackPolicy()

    def test_uses_primary_window_when_it_has_enough_listeners(self) -> None:
        rows, summary = combine_window_results(
            [{"seed_artist_mbid": "seed", "seed_artist_name": "Seed", "rank": 1}],
            payload([{"mbid": "seed", "artist_name": "Seed", "listener_count_in_window": 30}]),
            [{"seed_artist_mbid": "seed", "seed_artist_name": "Seed", "rank": 1}],
            payload([{"mbid": "seed", "artist_name": "Seed", "listener_count_in_window": 80}]),
            self.policy,
        )
        self.assertEqual(30, rows[0]["window_days"])
        self.assertEqual("behavior_primary", rows[0]["recommendation_source"])
        self.assertEqual(1, summary["primary_artist_count"])

    def test_uses_extended_window_below_thirty_listeners(self) -> None:
        rows, summary = combine_window_results(
            [{"seed_artist_mbid": "seed", "seed_artist_name": "Seed", "rank": 1}],
            payload([{"mbid": "seed", "artist_name": "Seed", "listener_count_in_window": 12}]),
            [{"seed_artist_mbid": "seed", "seed_artist_name": "Seed", "rank": 1}],
            payload([{"mbid": "seed", "artist_name": "Seed", "listener_count_in_window": 51}]),
            self.policy,
        )
        self.assertEqual(90, rows[0]["window_days"])
        self.assertEqual("behavior_extended", rows[0]["recommendation_source"])
        self.assertEqual("primary_listeners_below_30", rows[0]["fallback_reason"])
        self.assertEqual(1, summary["extended_artist_count"])

    def test_marks_metadata_fallback_when_both_windows_have_no_candidates(self) -> None:
        rows, summary = combine_window_results(
            [],
            payload([{"mbid": "seed", "artist_name": "Seed", "listener_count_in_window": 0}]),
            [],
            payload([{"mbid": "seed", "artist_name": "Seed", "listener_count_in_window": 0}]),
            self.policy,
        )
        self.assertEqual([], rows)
        self.assertEqual(1, summary["metadata_fallback_required_count"])
        self.assertEqual("metadata_fallback_required", summary["artists"][0]["status"])

    def test_routes_unseen_artist_without_assuming_a_fixed_neighbor(self) -> None:
        self.assertEqual(
            "resolve_mbid_and_queue",
            request_route(
                has_precomputed_edges=False,
                primary_listener_count=None,
                extended_listener_count=None,
                metadata_available=False,
            ),
        )
        self.assertEqual(
            "calculate_extended_on_demand",
            request_route(
                has_precomputed_edges=False,
                primary_listener_count=4,
                extended_listener_count=20,
                metadata_available=True,
            ),
        )
        self.assertEqual(
            "serve_precomputed",
            request_route(
                has_precomputed_edges=True,
                primary_listener_count=0,
                extended_listener_count=0,
                metadata_available=False,
            ),
        )


if __name__ == "__main__":
    unittest.main()
