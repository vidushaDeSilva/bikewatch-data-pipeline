import copy
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import ValidationError

from bikewatch.ingestion.gbfs import discover, fetch_json
from bikewatch.ingestion.models import (
    StationEnvelope,
    StationInformation,
    StationStatus,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tests/fixtures/gbfs_v1_1"

MODELS = {
    "station_information": StationInformation,
    "station_status": StationStatus,
}


def main():
    if OUTPUT.exists():
        raise SystemExit(
            "Fixtures already exist; keep them for repeatable tests."
        )

    discovery_url = os.getenv(
        "GBFS_DISCOVERY_URL",
        "https://gbfs.citibikenyc.com/gbfs/gbfs.json",
    )

    payloads = {}
    indexed = {}

    with httpx.Client(timeout=20, follow_redirects=True) as client:
        urls = discover(client, discovery_url)

        for name, model in MODELS.items():
            payloads[name] = fetch_json(client, urls[name])
            envelope = StationEnvelope.model_validate(payloads[name])
            indexed[name] = {}

            for record in envelope.data.stations:
                try:
                    station = model.model_validate(record)
                except ValidationError:
                    continue

                indexed[name].setdefault(station.station_id, record)

    # Use the same four real stations in both saved responses.
    selected = sorted(
        set(indexed["station_information"])
        & set(indexed["station_status"])
    )[:4]

    if len(selected) != 4:
        raise SystemExit(
            "Need four valid stations present in both feeds."
        )

    manifest = {
        "captured_at": datetime.now(UTC).isoformat(),
        "discovery_url": discovery_url,
        "station_ids": selected,
        "description": (
            "Real response envelopes reduced to four station records."
        ),
        "feeds": {},
    }

    OUTPUT.mkdir(parents=True)

    for name in MODELS:
        sample = copy.deepcopy(payloads[name])
        source_count = len(sample["data"]["stations"])

        sample["data"]["stations"] = [
            indexed[name][station_id]
            for station_id in selected
        ]

        data = (
            json.dumps(sample, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")

        (OUTPUT / f"{name}.json").write_bytes(data)

        manifest["feeds"][name] = {
            "url": urls[name],
            "source_record_count": source_count,
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    print("Saved four real stations from each feed, plus provenance.")


if __name__ == "__main__":
    main()