import json
import time

import httpx
from pydantic import HttpUrl, TypeAdapter


URL = TypeAdapter(HttpUrl)

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def reject_nonfinite(value):
    raise ValueError(f"Invalid JSON numeric constant: {value}")


def fetch_json(client, url):
    max_attempts = 3
    max_retry_after = 30

    for attempt in range(max_attempts):
        status_code = None
        delay = 2 ** attempt

        try:
            response = client.get(url)
            response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code

            if (
                status_code not in RETRYABLE_STATUS_CODES
                or attempt == max_attempts - 1
            ):
                raise

            retry_after = exc.response.headers.get("Retry-After")

            if retry_after is not None:
                value = retry_after.strip()

                # Support numeric Retry-After values.
                # Unsupported values stop this request.
                if not value.isascii() or not value.isdecimal():
                    raise

                requested_delay = int(value)

                # Do not retry earlier than the provider permits.
                if requested_delay > max_retry_after:
                    raise

                # Preserve our increasing backoff even if the
                # provider sends Retry-After: 0.
                delay = max(delay, requested_delay)

            error_type = type(exc).__name__

        except httpx.TransportError as exc:
            if attempt == max_attempts - 1:
                raise

            error_type = type(exc).__name__

        else:
            # JSON parsing errors are not network failures.
            return json.loads(
                response.text,
                parse_constant=reject_nonfinite,
            )

        # Only controlled diagnostic fields enter the log.
        print(json.dumps({
            "event": "http_retry",
            "attempt_failed": attempt + 1,
            "next_attempt": attempt + 2,
            "delay_seconds": delay,
            "http_status": status_code,
            "error_type": error_type,
        }), flush=True)

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