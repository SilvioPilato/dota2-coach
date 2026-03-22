"""odota parser sidecar HTTP client."""
from __future__ import annotations

import json
from pathlib import Path

import httpx

SIDECAR_URL = "http://127.0.0.1:5600"
DOCKER_START_CMD = "docker run -d -p 5600:5600 odota/parser"


class ParserNotRunningError(Exception):
    pass


def check_sidecar_health() -> None:
    """
    Verify the odota parser sidecar is reachable.
    The parser has no /health endpoint — we probe with a minimal POST instead.
    Raises ParserNotRunningError if the sidecar is not running.
    """
    try:
        with httpx.Client(timeout=3.0) as client:
            # A POST with empty body will fail fast but proves the port is open
            client.post(
                SIDECAR_URL,
                content=b"",
                headers={"Content-Type": "application/octet-stream"},
            )
    except httpx.ConnectError:
        raise ParserNotRunningError(
            f"odota parser is not running. Start it with: {DOCKER_START_CMD}"
        )
    except (httpx.ReadTimeout, httpx.HTTPStatusError):
        # Port is open and responding — parser is running
        pass


def parse_replay(dem_path: Path) -> list[dict]:
    """
    POST .dem file to odota parser sidecar. Returns list of event records (NDJSON parsed).
    Timeout: 120s (parsing a 45-min replay takes ~10-30s locally).
    """
    with httpx.Client(timeout=120.0) as client:
        with open(dem_path, "rb") as f:
            data = f.read()
        response = client.post(
            SIDECAR_URL,
            content=data,
            headers={"Content-Type": "application/octet-stream"},
        )
        response.raise_for_status()

    return [
        json.loads(line)
        for line in response.text.strip().splitlines()
        if line.strip()
    ]
