#!/usr/bin/env python3
"""Probe the sample's discovered upgrade API and normalize its build response."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from propres_patcher.user_agent import CHROME_USER_AGENT


DEFAULT_API_BASE = "https://api.renewedvision.com/v1.1"
DEFAULT_ROUTE = "/pro/upgrade"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def parse_header(values: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values:
        name, separator, content = value.partition("=")
        if not separator or not name.strip():
            raise ValueError(f"Header must use NAME=VALUE syntax: {value}")
        headers[name.strip()] = content
    return headers


def parse_header_json(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise ValueError("--headers-json must be a JSON object of string names and values")
    return value


def api_url(base: str, route: str, extra_query: list[str]) -> str:
    parsed = urllib.parse.urlsplit(base.rstrip("/"))
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("OTA API base must be an HTTPS URL")
    path = parsed.path.rstrip("/") + "/" + route.lstrip("/")
    query: list[tuple[str, str]] = []
    for item in extra_query:
        key, separator, value = item.partition("=")
        if not separator or not key:
            raise ValueError(f"Query must use NAME=VALUE syntax: {item}")
        query.append((key, value))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, urllib.parse.urlencode(query), ""))


def merge_query(defaults: list[str], overrides: list[str]) -> list[str]:
    pairs: list[tuple[str, str]] = []
    positions: dict[str, int] = {}
    for item in [*defaults, *overrides]:
        key, separator, value = item.partition("=")
        if not separator or not key:
            raise ValueError(f"Query must use NAME=VALUE syntax: {item}")
        if key in positions:
            pairs[positions[key]] = (key, value)
        else:
            positions[key] = len(pairs)
            pairs.append((key, value))
    return [f"{key}={value}" for key, value in pairs]


def response_header(headers: dict[str, str], name: str) -> str:
    wanted = name.lower()
    return next((value for key, value in headers.items() if key.lower() == wanted), "")


def fetch(url: str, headers: dict[str, str], state: dict[str, Any], timeout: float) -> tuple[int, dict[str, str], bytes]:
    request_headers = {"Accept": "application/json", "User-Agent": CHROME_USER_AGENT, **headers}
    if state.get("etag"):
        request_headers["If-None-Match"] = str(state["etag"])
    if state.get("last_modified"):
        request_headers["If-Modified-Since"] = str(state["last_modified"])
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return 304, dict(error.headers.items()), b""
        body = error.read(512).decode("utf-8", errors="replace")
        raise RuntimeError(f"OTA API returned HTTP {error.code}: {body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"OTA API request failed: {error.reason}") from error


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        normalized = {str(key).lower(): item for key, item in value.items()}
        if "buildnumber" in normalized and "downloadurl" in normalized:
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def get_field(item: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in item.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return default


def parse_builds(payload: Any, channel: str, allowed_hosts: set[str]) -> list[dict[str, Any]]:
    builds: list[dict[str, Any]] = []
    for item in walk(payload):
        raw_url = get_field(item, "downloadUrl", "DownloadUrl")
        if not isinstance(raw_url, str) or not raw_url:
            continue
        parsed_url = urllib.parse.urlsplit(raw_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise RuntimeError(f"Refusing non-HTTPS downloadUrl from OTA API: {raw_url}")
        if allowed_hosts and parsed_url.hostname not in allowed_hosts:
            raise RuntimeError(f"Refusing downloadUrl host not in allowlist: {parsed_url.hostname}")

        is_beta = as_bool(get_field(item, "isBeta", "IsBeta"))
        item_channel = str(get_field(item, "channel", "Channel") or ("beta" if is_beta else "production"))
        if channel == "production" and (is_beta or item_channel.lower() == "beta"):
            continue
        if channel == "beta" and not (is_beta or item_channel.lower() == "beta"):
            continue
        available = get_field(item, "isAvailable", "IsAvailable")
        if available is not None and not as_bool(available, default=True):
            continue

        build_number = get_field(item, "buildNumber", "BuildNumber")
        try:
            build_number = int(build_number)
        except (TypeError, ValueError):
            raise RuntimeError(f"OTA response has an invalid buildNumber: {build_number!r}")
        version = str(get_field(item, "version", "Version") or "")
        builds.append({
            "build_number": build_number,
            "version": version,
            "min_os_version": str(get_field(item, "minOsVersion", "MinOsVersion", "minOSVersion") or ""),
            "download_url": raw_url,
            "channel": item_channel,
            "is_beta": is_beta,
            "is_available": True,
        })
    return builds


def version_key(build: dict[str, Any]) -> tuple[int, tuple[int, ...], str]:
    parts: list[int] = []
    for part in str(build.get("version", "")).split("."):
        digits = "".join(character for character in part if character.isdigit())
        parts.append(int(digits or "0"))
    return int(build["build_number"]), tuple(parts), str(build.get("version", ""))


def same_build(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    try:
        previous_build = int(previous.get("build_number", -1))
    except (TypeError, ValueError):
        previous_build = -1
    return (
        str(previous.get("channel", "production")).lower() == str(current.get("channel", "production")).lower()
        and previous_build == int(current["build_number"])
        and str(previous.get("version", "")) == str(current.get("version", ""))
    )


def write_outputs(path: Path | None, payload: dict[str, Any]) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_github_output(path: Path | None, values: dict[str, str]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover ProPresenter builds through the sample's OTA API.")
    parser.add_argument("--api-base", default=os.environ.get("PROPRES_OTA_API_BASE", DEFAULT_API_BASE))
    parser.add_argument("--route", default=os.environ.get("PROPRES_OTA_ROUTE", DEFAULT_ROUTE))
    parser.add_argument("--platform", default=os.environ.get("PROPRES_OTA_PLATFORM", "win32"))
    parser.add_argument("--os-version", default=os.environ.get("PROPRES_OTA_OS_VERSION", "10.0"))
    parser.add_argument("--app-version", default=os.environ.get("PROPRES_OTA_APP_VERSION"))
    parser.add_argument("--build-number", type=int, default=None)
    parser.add_argument("--include-notes", default="0")
    parser.add_argument("--format", default="")
    parser.add_argument("--channel", choices=("production", "beta"), default="production")
    parser.add_argument("--query", action="append", default=[], help="Additional query NAME=VALUE")
    parser.add_argument("--header", action="append", default=[], help="Additional header NAME=VALUE")
    parser.add_argument("--headers-json", default=os.environ.get("PROPRES_OTA_HEADERS_JSON", ""))
    parser.add_argument("--allowed-download-host", action="append", default=[], help="Allowed HTTPS download host")
    parser.add_argument("--state-file", type=Path, default=Path("ota-state.json"))
    parser.add_argument("--result-file", type=Path, default=Path("ota-result.json"))
    parser.add_argument("--next-state-file", type=Path, default=Path("ota-next-state.json"))
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        previous = read_json(args.state_file)
        previous_latest = previous.get("latest", {})
        if not isinstance(previous_latest, dict):
            previous_latest = {}
        headers = {**parse_header_json(args.headers_json), **parse_header(args.header)}
        allowed_hosts = {host.strip().lower() for host in args.allowed_download_host if host.strip()}
        app_version = args.app_version or str(previous_latest.get("version") or "0")
        build_number = args.build_number if args.build_number is not None else int(previous_latest.get("build_number", 0) or 0)
        query = merge_query(
            [
                f"platform={args.platform}",
                f"osVersion={args.os_version}",
                f"appVersion={app_version}",
                f"buildNumber={build_number}",
                f"includeNotes={args.include_notes}",
                f"format={args.format}",
            ],
            args.query,
        )
        url = api_url(args.api_base, args.route, query)
        status, response_headers, body = fetch(url, headers, previous, args.timeout)
        if status == 304:
            current = previous.get("latest", previous)
            result = {"status": 304, "changed": False, "api_url": url, "latest": current}
            next_state = previous
        else:
            payload = json.loads(body)
            result_payload = payload.get("result") if isinstance(payload, dict) else None
            if isinstance(result_payload, dict) and result_payload.get("success") is False:
                description = result_payload.get("description") or result_payload.get("error") or "unknown API error"
                raise RuntimeError(f"OTA API rejected the request: {description}")
            builds = parse_builds(payload, args.channel, allowed_hosts)
            latest = max(builds, key=version_key) if builds else None
            changed = bool(latest and not same_build(previous_latest, latest))
            selected = latest or (previous_latest if previous_latest else None)
            result = {
                "status": status,
                "changed": changed,
                "api_url": url,
                "channel": args.channel,
                "latest": selected,
                "response_sha256": sha256_bytes(body),
            }
            next_state = {
                "api_url": url,
                "channel": args.channel,
                "etag": response_header(response_headers, "ETag"),
                "last_modified": response_header(response_headers, "Last-Modified"),
                "latest": selected or {},
            }
        write_outputs(args.result_file, result)
        write_outputs(args.next_state_file, next_state)
        latest = result.get("latest") if result.get("changed") else {}
        latest = latest or {}
        write_github_output(args.github_output, {
            "changed": "true" if result.get("changed") else "false",
            "build_number": str(latest.get("build_number", "")),
            "version": str(latest.get("version", "")),
            "download_url": str(latest.get("download_url", "")),
        })
        print(json.dumps(result, indent=2))
        return 0
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
