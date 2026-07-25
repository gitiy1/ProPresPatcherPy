#!/usr/bin/env python3
"""Extract the two managed competition assemblies from a Windows installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


TARGETS = ("ProPresenter.dll", "ProPresenter.DO.dll")
FORK_INNOEXTRACT_ROOT = Path(__file__).resolve().parent / "tools" / "innoextract-win"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_archiver(explicit: str | None) -> str:
    if explicit:
        return explicit
    for candidate in ("7z", "7zz", "7z.exe", "7zz.exe"):
        found = shutil.which(candidate)
        if found:
            return found
    raise RuntimeError("No 7-Zip executable found; install 7z/7zz on the Windows runner or pass --archiver")


def find_innoextract(explicit: str | None) -> str | None:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"innoextract executable not found: {path}")
        return str(path)
    for candidate in (FORK_INNOEXTRACT_ROOT / "innoextract.exe", FORK_INNOEXTRACT_ROOT / "innoextract"):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("innoextract") or shutil.which("innoextract.exe")


def run_capture(command: list[str], log_file: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8", errors="replace") as stream:
            stream.write(f"$ {shlex.join(command)}\n")
            stream.write(result.stdout)
            if result.stdout and not result.stdout.endswith("\n"):
                stream.write("\n")
    if result.returncode != 0 and result.stdout:
        print(result.stdout[-4000:], file=sys.stderr)
    return result


def run_silent_install(installer: Path, output: Path, installer_log: Path | None) -> Path:
    if os.name != "nt":
        raise RuntimeError("--allow-installer-run is only supported on Windows")
    installed = output / "installed"
    installed.mkdir(parents=True, exist_ok=True)
    install_command = [
        str(installer),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        f"/DIR={installed}",
    ]
    result = run_capture(install_command, installer_log)
    if result.returncode != 0:
        raise RuntimeError(f"silent installer exited with code {result.returncode}")
    return installed


def extract(
    installer: Path,
    output: Path,
    archiver: str | None,
    innoextract: str | None,
    allow_installer_run: bool,
    installer_log: Path | None,
) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(installer):
        with zipfile.ZipFile(installer) as archive:
            archive.extractall(output)
        return output

    inno = find_innoextract(innoextract)
    if inno:
        result = run_capture([inno, "--extract", "--output-dir", str(output), str(installer)], installer_log)
        if result.returncode == 0 and any(path.is_file() for target in TARGETS for path in output.rglob(target)):
            return output

    staging = output / "installer-tree"
    staging.mkdir(parents=True, exist_ok=True)
    try:
        archiver_path = find_archiver(archiver)
    except RuntimeError:
        if allow_installer_run:
            return run_silent_install(installer, output, installer_log)
        raise
    command = [archiver_path, "x", "-y", f"-o{staging}", str(installer)]
    run_capture(command, installer_log)
    if any(path.is_file() for target in TARGETS for path in staging.rglob(target)):
        return staging

    if allow_installer_run:
        return run_silent_install(installer, output, installer_log)

    raise RuntimeError(
        "installer payload was not directly extractable; install innoextract or "
        "pass --allow-installer-run on a Windows runner"
    )


def locate(root: Path, name: str) -> Path:
    matches = [path for path in root.rglob(name) if path.is_file()]
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one {name} after extraction, found {len(matches)}: {matches}")
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract ProPresenter managed DLLs from an installer")
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--archiver")
    parser.add_argument("--innoextract")
    parser.add_argument("--allow-installer-run", action="store_true")
    parser.add_argument("--installer-log", type=Path)
    args = parser.parse_args(argv)
    try:
        if not args.installer.is_file():
            raise FileNotFoundError(args.installer)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        root = extract(
            args.installer,
            args.output_dir,
            args.archiver,
            args.innoextract,
            args.allow_installer_run,
            args.installer_log or args.output_dir / "installer-output.log",
        )
        manifest = {"installer": str(args.installer), "assemblies": {}}
        for target in TARGETS:
            source = locate(root, target)
            destination = args.output_dir / target
            if destination.exists():
                raise FileExistsError(destination)
            shutil.copy2(source, destination)
            manifest["assemblies"][target] = {"source": str(source), "sha256": sha256(destination)}
            print(f"Extracted {target}: {destination} sha256={manifest['assemblies'][target]['sha256']}")
        (args.output_dir / "extraction-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
