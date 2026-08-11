import unittest

from src.youth_popular_evaluation import listener_tier


class YouthPopularEvaluationTests(unittest.TestCase):
    def test_listener_tiers(self) -> None:
        self.assertEqual("low_under_30", listener_tier(29))
        self.assertEqual("limited_30_to_99", listener_tier(30))
        self.assertEqual("sufficient_100_plus", listener_tier(100))


if __name__ == "__main__":
    unittest.main()
