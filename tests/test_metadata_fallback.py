import unittest

from src.metadata_fallback import ArtistMetadata, blend_low_data_results, metadata_similarity


def feature(mbid, name, *, genre="", country="JP", begin=2020, related=None):
    return ArtistMetadata(
        mbid=mbid,
        name=name,
        artist_type="Group",
        countries=[country],
        begin_year=begin,
        related_artist_mbids=related or [],
        curated_primary_genre=genre,
    )


class MetadataFallbackTests(unittest.TestCase):
    def test_country_and_type_alone_do_not_create_similarity(self):
        score = metadata_similarity(feature("a", "A"), feature("b", "B"))
        self.assertEqual(0, score["metadata_score"])

    def test_shared_genre_is_stronger_with_year_and_country_evidence(self):
        score = metadata_similarity(
            feature("a", "A", genre="rock", begin=2020),
            feature("b", "B", genre="rock", begin=2022),
        )
        self.assertGreater(score["metadata_score"], 0.4)
        self.assertTrue(score["curated_genre_match"])

    def test_direct_relation_is_accepted_as_strong_evidence(self):
        score = metadata_similarity(
            feature("a", "A", related=["b"]),
            feature("b", "B"),
        )
        self.assertGreater(score["metadata_score"], 0)
        self.assertTrue(score["direct_relation"])

    def test_blends_only_artist_below_listener_threshold(self):
        rows = [
            {
                "seed_artist_name": "Sparse",
                "seed_artist_mbid": "s",
                "candidate_artist_name": "Behavior",
                "candidate_artist_mbid": "b",
                "similarity_score": "0.1",
                "rank": "1",
                "confidence": "low",
            },
            {
                "seed_artist_name": "Dense",
                "seed_artist_mbid": "d",
                "candidate_artist_name": "Behavior",
                "candidate_artist_mbid": "b",
                "similarity_score": "0.5",
                "rank": "1",
                "confidence": "high",
            },
        ]
        summary = {
            "similarity": {
                "artists": [
                    {"mbid": "s", "listener_count_in_window": 3},
                    {"mbid": "d", "listener_count_in_window": 30},
                ]
            }
        }
        metadata = {
            "artists": [
                feature("s", "Sparse", genre="rock").__dict__,
                feature("d", "Dense", genre="pop").__dict__,
                feature("m", "Metadata", genre="rock").__dict__,
            ]
        }
        blended, report = blend_low_data_results(rows, summary, metadata, limit=10)
        sparse = [row for row in blended if row["seed_artist_mbid"] == "s"]
        dense = [row for row in blended if row["seed_artist_mbid"] == "d"]
        self.assertEqual("m", sparse[0]["candidate_artist_mbid"])
        self.assertEqual("metadata_fallback", sparse[0]["recommendation_source"])
        self.assertEqual("behavior_primary", dense[0]["recommendation_source"])
        self.assertEqual(1, report["low_data_artist_count"])

    def test_top10_keeps_at_least_five_behavior_candidates(self):
        rows = [
            {
                "seed_artist_name": "Sparse",
                "seed_artist_mbid": "s",
                "candidate_artist_name": f"Behavior {index}",
                "candidate_artist_mbid": f"b{index}",
                "similarity_score": "0.1",
                "rank": str(index),
                "confidence": "low",
            }
            for index in range(1, 11)
        ]
        summary = {"similarity": {"artists": [{"mbid": "s", "listener_count_in_window": 1}]}}
        artists = [feature("s", "Sparse", genre="rock").__dict__]
        artists.extend(feature(f"m{index}", f"Metadata {index}", genre="rock").__dict__ for index in range(10))
        blended, _ = blend_low_data_results(rows, summary, {"artists": artists}, limit=10)
        metadata_only = [row for row in blended if row["recommendation_source"] == "metadata_fallback"]
        self.assertEqual(5, len(metadata_only))


if __name__ == "__main__":
    unittest.main()
