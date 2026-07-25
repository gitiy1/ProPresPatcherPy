#!/usr/bin/env python3
"""Download an installer URL returned by the OTA API using the shared Chrome UA."""

from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from propres_patcher.user_agent import CHROME_USER_AGENT


def download(url: str, output: Path, timeout: float) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("installer URL must be an HTTPS URL")
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/octet-stream", "User-Agent": CHROME_USER_AGENT},
        method="GET",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".part")
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as target:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                digest.update(block)
                target.write(block)
        temporary.replace(output)
    except urllib.error.URLError as error:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"installer download failed: {error.reason}") from error
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download an API-returned ProPresenter installer")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args(argv)
    try:
        digest = download(args.url, args.output, args.timeout)
        print(f"Downloaded {args.output} sha256={digest}")
        return 0
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
