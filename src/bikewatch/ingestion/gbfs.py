import json
import time

import httpx
from pydantic import HttpUrl, TypeAdapter


URL = TypeAdapter(HttpUrl)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def reject_nonfinite(value):
    raise ValueError(f"Invalid JSON numeric constant: {value}")


def fetch_json(client, url):
    for attempt in range(3):
        try:
            response = client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if (
                exc.response.status_code not in RETRYABLE_STATUS_CODES
                or attempt == 2
            ):
                raise

            # Respect short Retry-After delays; fail rather than
            # retry too early if the provider requests a longer wait.
            retry_after = exc.response.headers.get("Retry-After")
            if retry_after is not None:
                if not retry_after.isdigit() or int(retry_after) > 30:
                    raise
                delay = int(retry_after)
            else:
                delay = 2 ** attempt

        except httpx.TransportError:
            if attempt == 2:
                raise
            delay = 2 ** attempt

        else:
            # Malformed JSON is not retried as a network failure.
            return json.loads(
                response.text,
                parse_constant=reject_nonfinite,
            )

        time.sleep(delay)

    raise RuntimeError("HTTP retry loop exhausted")


def discover(client, discovery_url):
    payload = fetch_json(client, discovery_url)

    if payload.get("version") != "1.1":
        raise ValueError("Unsupported GBFS discovery version")

    feeds = {
        item["name"]: str(URL.validate_python(item["url"]))
        for item in payload["data"]["en"]["feeds"]
    }

    for name in ("station_information", "station_status"):
        if name not in feeds:
            raise ValueError(f"Required feed missing: {name}")
        if not feeds[name].startswith("https://"):
            raise ValueError("An HTTPS feed URL is required")

    return feeds