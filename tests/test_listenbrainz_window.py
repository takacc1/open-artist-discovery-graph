import unittest
from datetime import date

from src.listenbrainz_window import build_parser, parse_incremental_index, select_window


class ListenBrainzWindowTests(unittest.TestCase):
    def test_parses_dump_index_and_builds_spark_filename(self) -> None:
        html = """
        <a href="listenbrainz-dump-100-20260801-000002-incremental/">one</a>
        <a href="listenbrainz-dump-101-20260802-000003-incremental/">two</a>
        """
        dumps = parse_incremental_index(html)
        self.assertEqual(2, len(dumps))
        self.assertEqual(
            "listenbrainz-spark-dump-100-20260801-000002-incremental.tar",
            dumps[0].spark_filename,
        )

    def test_selects_contiguous_window(self) -> None:
        html = "".join(
            f'<a href="listenbrainz-dump-{100 + day}-2026080{day}-000002-incremental/">x</a>'
            for day in range(1, 4)
        )
        selected = select_window(
            parse_incremental_index(html), end_date=date(2026, 8, 3), days=3
        )
        self.assertEqual([date(2026, 8, 1), date(2026, 8, 2), date(2026, 8, 3)], [item.day for item in selected])

    def test_rejects_gap_in_requested_window(self) -> None:
        html = """
        <a href="listenbrainz-dump-100-20260801-000002-incremental/">one</a>
        <a href="listenbrainz-dump-102-20260803-000002-incremental/">three</a>
        """
        with self.assertRaisesRegex(ValueError, "2026-08-02"):
            select_window(parse_incremental_index(html), end_date=date(2026, 8, 3), days=3)

    def test_end_date_defaults_to_latest_available_day(self) -> None:
        args = build_parser().parse_args(["--output-dir", "/tmp/window"])
        self.assertIsNone(args.end_date)


if __name__ == "__main__":
    unittest.main()
