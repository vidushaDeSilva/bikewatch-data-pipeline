import hashlib
import json
from pathlib import Path

import httpx
import pytest

from src.bikewatch.ingestion import gbfs
from src.bikewatch.ingestion.models import (
    StationEnvelope,
    StationInformation,
    StationStatus,
)


@pytest.mark.parametrize(
    "steps,header,delays,error",
    [
        ([200], None, [], None),
        (["timeout", 200], None, [1], None),
        (
            ["timeout"] * 3,
            None,
            [1, 2],
            httpx.ReadTimeout,
        ),
        ([503, 503, 200], None, [1, 2], None),
        (
            [503] * 3,
            None,
            [1, 2],
            httpx.HTTPStatusError,
        ),
        ([404], None, [], httpx.HTTPStatusError),
        ([429, 200], "0", [1], None),
        ([429, 200], "5", [5], None),
        ([429], "31", [], httpx.HTTPStatusError),
        ([429], "unsupported", [], httpx.HTTPStatusError),
        (["bad_json"], None, [], ValueError),
        (["nonfinite"], None, [], ValueError),
    ],
)
def test_retry_policy(
    steps,
    header,
    delays,
    error,
    monkeypatch,
    capsys,
):
    pending = list(steps)
    waits = []

    # Record requested delays without actually sleeping.
    monkeypatch.setattr(gbfs.time, "sleep", waits.append)

    secret = "DO_NOT_LOG_THIS_TEST_SECRET"

    def handler(request):
        item = pending.pop(0)

        if item == "timeout":
            raise httpx.ReadTimeout(secret, request=request)

        if item in ("bad_json", "nonfinite"):
            text = "{" if item == "bad_json" else "NaN"
            return httpx.Response(200, text=text)

        headers = (
            {}
            if header is None
            else {"Retry-After": header}
        )

        return httpx.Response(
            item,
            json={"ok": True},
            headers=headers,
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler)
    ) as client:
        url = f"https://fixture.test/feed?token={secret}"

        if error is None:
            assert gbfs.fetch_json(client, url) == {"ok": True}
        else:
            with pytest.raises(error):
                gbfs.fetch_json(client, url)

    assert pending == []
    assert waits == delays

    logs = capsys.readouterr()
    assert secret not in logs.out + logs.err


def test_saved_source_contract(source_feeds):
    folder = (
        Path(__file__).resolve().parents[2]
        / "tests/fixtures/gbfs_v1_1"
    )

    manifest = json.loads(
        (folder / "manifest.json").read_text(encoding="utf-8")
    )

    expected_ids = set(manifest["station_ids"])
    assert len(expected_ids) == 4

    for name, model in (
        ("station_information", StationInformation),
        ("station_status", StationStatus),
    ):
        raw = (folder / f"{name}.json").read_bytes()

        assert (
            hashlib.sha256(raw).hexdigest()
            == manifest["feeds"][name]["sha256"]
        )

        envelope = StationEnvelope.model_validate(
            source_feeds[name]
        )

        rows = [
            model.model_validate(row)
            for row in envelope.data.stations
        ]

        assert len(rows) == 4
        assert {row.station_id for row in rows} == expected_ids