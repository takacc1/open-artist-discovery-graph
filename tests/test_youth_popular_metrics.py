import unittest

from src.youth_popular_metrics import market_segment, validate_ranked_rows


class YouthPopularMetricsTests(unittest.TestCase):
    def test_market_segment(self) -> None:
        self.assertEqual("kpop", market_segment("kpop"))
        self.assertEqual("japan", market_segment("pop_rock"))

    def test_validate_ranked_rows_accepts_22_complete_top_tens(self) -> None:
        rows = [
            {"seed_artist_name": f"seed-{seed}", "rank": rank}
            for seed in range(22)
            for rank in range(1, 11)
        ]
        validate_ranked_rows(rows)


if __name__ == "__main__":
    unittest.main()
