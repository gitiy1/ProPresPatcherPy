#!/usr/bin/env python3
"""Install the latest Windows innoextract fork into a local tools directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from propres_patcher.user_agent import CHROME_USER_AGENT


DEFAULT_REPOSITORY = "UserUnknownFactor/innoextract_win"
DEFAULT_API_URL = f"https://api.github.com/repos/{DEFAULT_REPOSITORY}/releases/latest"
DEFAULT_OUTPUT_DIR = Path("tools/innoextract-win")
GITHUB_API_HOST = "api.github.com"
GITHUB_DOWNLOAD_HOSTS = {"github.com", "objects.githubusercontent.com"}
EXPECTED_EXECUTABLE = "innoextract.exe"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_https_host(url: str, allowed_hosts: set[str]) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() not in allowed_hosts:
        hosts = ", ".join(sorted(allowed_hosts))
        raise ValueError(f"refusing URL outside HTTPS allowlist ({hosts}): {url}")
    return parsed


def fetch_json(url: str, timeout: float) -> dict[str, Any]:
    require_https_host(url, {GITHUB_API_HOST})
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": CHROME_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read(512).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub release API returned HTTP {error.code}: {body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"GitHub release API request failed: {error.reason}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub release API returned a non-object payload")
    return payload


def select_asset(release: dict[str, Any]) -> dict[str, Any]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError("GitHub release has no assets list")
    candidates = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        if isinstance(name, str) and "innoextract" in name.lower() and name.lower().endswith(".zip"):
            candidates.append(asset)
    if len(candidates) != 1:
        names = [item.get("name") for item in candidates]
        raise RuntimeError(f"expected exactly one innoextract ZIP asset, found {len(candidates)}: {names}")
    asset = candidates[0]
    name = asset.get("name")
    download_url = asset.get("browser_download_url")
    if not isinstance(name, str) or not isinstance(download_url, str):
        raise RuntimeError("selected GitHub asset is missing name or browser_download_url")
    require_https_host(download_url, GITHUB_DOWNLOAD_HOSTS)
    return asset


def expected_digest(asset: dict[str, Any]) -> str | None:
    value = asset.get("digest")
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise RuntimeError(f"unsupported GitHub asset digest: {value!r}")
    digest = value.removeprefix("sha256:").lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise RuntimeError(f"invalid GitHub asset SHA-256 digest: {value!r}")
    return digest


def download(url: str, destination: Path, timeout: float) -> str:
    require_https_host(url, GITHUB_DOWNLOAD_HOSTS)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": CHROME_USER_AGENT,
        },
        method="GET",
    )
    digest = hashlib.sha256()
    temporary = destination.with_name(destination.name + ".part")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temporary.open("wb") as target:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                digest.update(block)
                target.write(block)
        temporary.replace(destination)
    except urllib.error.URLError as error:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"innoextract download failed: {error.reason}") from error
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def safe_member_path(destination: Path, member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    relative = PurePosixPath(normalized)
    has_windows_drive = len(normalized) >= 2 and normalized[1] == ":"
    if normalized.startswith("/") or relative.is_absolute() or has_windows_drive or ".." in relative.parts:
        raise RuntimeError(f"refusing unsafe ZIP member: {member_name!r}")
    target = (destination / Path(*relative.parts)).resolve()
    target.relative_to(destination.resolve())
    return target


def extract_zip(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            target = safe_member_path(destination, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            unix_mode = (member.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise RuntimeError(f"refusing symlink in extractor ZIP: {member.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def find_executable(root: Path) -> Path:
    matches = [path for path in root.rglob("*") if path.is_file() and path.name.lower() == EXPECTED_EXECUTABLE]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one {EXPECTED_EXECUTABLE} in release ZIP, found {matches}")
    return matches[0]


def copy_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.rglob("*"):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def install(api_url: str, output_dir: Path, timeout: float, force: bool) -> Path:
    release = fetch_json(api_url, timeout)
    asset = select_asset(release)
    release_tag = str(release.get("tag_name") or "unknown")
    asset_name = str(asset["name"])
    asset_digest = expected_digest(asset)
    metadata_path = output_dir / "innoextract-release.json"
    if not force and metadata_path.is_file() and (output_dir / EXPECTED_EXECUTABLE).is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
        if isinstance(metadata, dict) and metadata.get("tag") == release_tag and metadata.get("asset") == asset_name:
            cached_asset_digest = metadata.get("assetSha256")
            cached_executable_digest = metadata.get("executableSha256")
            executable = output_dir / EXPECTED_EXECUTABLE
            if (
                isinstance(cached_asset_digest, str)
                and isinstance(cached_executable_digest, str)
                and (not asset_digest or cached_asset_digest == asset_digest)
                and sha256(executable) == cached_executable_digest
            ):
                print(f"Using cached {executable}")
                return executable

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="innoextract-", dir=output_dir.parent) as temporary_dir:
        temporary_root = Path(temporary_dir)
        archive_path = temporary_root / asset_name
        actual_digest = download(str(asset["browser_download_url"]), archive_path, timeout)
        if asset_digest and actual_digest != asset_digest:
            raise RuntimeError(f"GitHub asset SHA-256 mismatch: expected {asset_digest}, got {actual_digest}")
        extracted = temporary_root / "extracted"
        extract_zip(archive_path, extracted)
        executable = find_executable(extracted)
        executable_relative = executable.relative_to(extracted)
        copy_tree(extracted, output_dir)

    installed_executable = output_dir / executable_relative
    executable_digest = sha256(installed_executable)
    metadata = {
        "repository": DEFAULT_REPOSITORY,
        "releaseUrl": release.get("html_url", ""),
        "tag": release_tag,
        "asset": asset_name,
        "assetUrl": asset.get("browser_download_url", ""),
        "expectedAssetSha256": asset_digest,
        "assetSha256": actual_digest,
        "executableSha256": executable_digest,
        "executable": str(installed_executable),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"Installed {installed_executable} assetSha256={actual_digest} executableSha256={executable_digest}")
    if not asset_digest:
        print("warning: GitHub did not provide an asset digest; recorded the computed SHA-256", file=sys.stderr)
    return installed_executable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the latest Windows innoextract fork release")
    parser.add_argument("--api-url", default=os.environ.get("INNOEXTRACT_RELEASE_API", DEFAULT_API_URL))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        executable = install(args.api_url, args.output_dir, args.timeout, args.force)
        print(f"innoextract={executable}")
        return 0
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
