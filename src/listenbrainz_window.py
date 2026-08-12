from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable


DEFAULT_BASE_URL = "https://ftp.musicbrainz.org/pub/musicbrainz/listenbrainz/incremental/"
DUMP_PATTERN = re.compile(
    r'href="(listenbrainz-dump-(\d+)-(\d{8}-\d{6})-incremental/)"'
)


@dataclass(frozen=True)
class IncrementalDump:
    dump_id: int
    timestamp: str
    directory_name: str

    @property
    def day(self) -> date:
        return datetime.strptime(self.timestamp[:8], "%Y%m%d").date()

    @property
    def spark_filename(self) -> str:
        suffix = self.directory_name.removeprefix("listenbrainz-dump-").rstrip("/")
        return f"listenbrainz-spark-dump-{suffix}.tar"


def parse_incremental_index(html: str) -> list[IncrementalDump]:
    dumps = {
        match.group(1): IncrementalDump(
            dump_id=int(match.group(2)),
            timestamp=match.group(3),
            directory_name=match.group(1),
        )
        for match in DUMP_PATTERN.finditer(html)
    }
    return sorted(dumps.values(), key=lambda item: (item.day, item.dump_id))


def select_window(
    dumps: list[IncrementalDump], *, end_date: date, days: int
) -> list[IncrementalDump]:
    if days <= 0:
        raise ValueError("days must be positive")
    start_date = end_date - timedelta(days=days - 1)
    by_day = {item.day: item for item in dumps if start_date <= item.day <= end_date}
    missing = [
        start_date + timedelta(days=offset)
        for offset in range(days)
        if start_date + timedelta(days=offset) not in by_day
    ]
    if missing:
        raise ValueError(
            "Official index does not contain every requested day: "
            + ", ".join(day.isoformat() for day in missing)
        )
    return [by_day[start_date + timedelta(days=offset)] for offset in range(days)]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_text(url: str, timeout: int = 60) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "open-artist-discovery-graph/phase1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


def download_file(url: str, destination: Path, timeout: int = 300) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "open-artist-discovery-graph/phase1"})
    with urllib.request.urlopen(request, timeout=timeout) as response, partial.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 1024)
    partial.replace(destination)


def existing_files(directories: Iterable[Path]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.rglob("listenbrainz-spark-dump-*-incremental.tar"):
            result[path.name] = path
    return result


def prepare_window(
    output_dir: Path,
    *,
    days: int,
    end_date: date | None,
    existing_dirs: list[Path],
    base_url: str = DEFAULT_BASE_URL,
) -> dict[str, object]:
    index_html = fetch_text(base_url)
    dumps = parse_incremental_index(index_html)
    if not dumps:
        raise ValueError("Official index does not contain incremental dumps")
    effective_end_date = end_date or dumps[-1].day
    selected = select_window(dumps, end_date=effective_end_date, days=days)
    reusable = existing_files(existing_dirs)
    sources: list[dict[str, object]] = []

    for number, item in enumerate(selected, 1):
        filename = item.spark_filename
        directory_url = base_url + item.directory_name
        checksum_url = directory_url + filename + ".sha256"
        expected = fetch_text(checksum_url).strip().split()[0]
        path = reusable.get(filename, output_dir / filename)
        reused = path.exists()
        if path.exists() and sha256(path) != expected:
            raise ValueError(f"Checksum mismatch for existing file: {path}")
        if not path.exists():
            print(f"Download {number}/{len(selected)} {filename}", flush=True)
            download_file(directory_url + filename, path)
            if sha256(path) != expected:
                raise ValueError(f"Checksum mismatch after download: {path}")
        else:
            print(f"Reuse {number}/{len(selected)} {filename}", flush=True)
        sources.append(
            {
                "date": item.day.isoformat(),
                "dump_id": item.dump_id,
                "path": str(path),
                "sha256": expected,
                "reused": reused,
                "size_bytes": path.stat().st_size,
            }
        )

    manifest = {
        "source": base_url,
        "window_days": days,
        "start_date": selected[0].day.isoformat(),
        "end_date": selected[-1].day.isoformat(),
        "source_count": len(sources),
        "total_bytes": sum(int(item["size_bytes"]) for item in sources),
        "downloaded_count": sum(not bool(item["reused"]) for item in sources),
        "reused_count": sum(bool(item["reused"]) for item in sources),
        "sources": sources,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download and verify a contiguous ListenBrainz Spark incremental window"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        help="Last day in the window (default: latest day in the official index)",
    )
    parser.add_argument("--existing-dir", type=Path, action="append", default=[])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    manifest = prepare_window(
        args.output_dir,
        days=args.days,
        end_date=args.end_date,
        existing_dirs=args.existing_dir,
        base_url=args.base_url,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
