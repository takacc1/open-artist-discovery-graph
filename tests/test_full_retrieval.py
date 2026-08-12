import unittest

from src.full_retrieval import build_blind_evaluation, summarize_retrievals


class FullRetrievalTests(unittest.TestCase):
    def test_blind_union_deduplicates_and_preserves_previous_rating(self) -> None:
        retrievals = [
            {
                "method": "cosine_shrinkage",
                "seed_artist_name": "aespa",
                "seed_artist_mbid": "seed",
                "candidate_artist_name": "IVE",
                "candidate_artist_mbid": "ive",
                "rank": 1,
                "score": 0.8,
                "evidence_count": 10,
            },
            {
                "method": "implicit_als",
                "seed_artist_name": "aespa",
                "seed_artist_mbid": "seed",
                "candidate_artist_name": "IVE",
                "candidate_artist_mbid": "ive",
                "rank": 2,
                "score": 0.7,
                "evidence_count": "",
            },
            {
                "method": "implicit_als",
                "seed_artist_name": "aespa",
                "seed_artist_mbid": "seed",
                "candidate_artist_name": "NMIXX",
                "candidate_artist_mbid": "nmixx",
                "rank": 1,
                "score": 0.9,
                "evidence_count": "",
            },
        ]
        previous = [
            {
                "seed_artist_name": "aespa",
                "candidate_artist_mbid": "ive",
                "human_rating_0_1_2": "2",
                "human_notes": "納得",
            }
        ]
        blind, key = build_blind_evaluation(retrievals, previous, random_state=1)
        self.assertEqual(2, len(blind))
        self.assertEqual(3, len(key))
        by_mbid = {row["candidate_artist_mbid"]: row for row in blind}
        self.assertEqual(2, by_mbid["ive"]["human_rating_0_1_2"])
        self.assertEqual("no", by_mbid["ive"]["needs_rating"])
        self.assertEqual("yes", by_mbid["nmixx"]["needs_rating"])

    def test_summary_reports_method_overlap_and_new_rows(self) -> None:
        retrievals = [
            {"method": "cosine_shrinkage", "seed_artist_mbid": "s", "candidate_artist_mbid": "a"},
            {"method": "session_cooccurrence", "seed_artist_mbid": "s", "candidate_artist_mbid": "a"},
            {"method": "implicit_als", "seed_artist_mbid": "s", "candidate_artist_mbid": "b"},
        ]
        blind = [
            {"seed_artist_name": "aespa", "needs_rating": "no"},
            {"seed_artist_name": "aespa", "needs_rating": "yes"},
        ]
        summary = summarize_retrievals(retrievals, blind)
        self.assertEqual(2, summary["union_candidate_count"])
        self.assertEqual(1, summary["new_rating_count"])
        self.assertEqual(
            1,
            summary["pairwise_overlap"]["cosine_shrinkage__session_cooccurrence"]["intersection"],
        )


if __name__ == "__main__":
    unittest.main()
