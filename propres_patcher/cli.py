from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import urllib.request
import warnings
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from propres_patcher.user_agent import CHROME_USER_AGENT


DEFAULT_DNLIB_VERSION = "4.5.0"
DEFAULT_DNLIB_NUPKG_SHA256 = "63bc2f9579568204cc8b30fa9f6700a231bcf868a8032e09118887af3eafee58"
DEFAULT_DNLIB_DLL_SHA256 = "566fdab59c91a3c2eab14a22b67f01cb3c0cdb48fa9b9b6677e2b32998636efb"
DEFAULT_RUNTIME_TFM = "net10.0"
DEFAULT_RUNTIME_VERSION = "10.0.0"


@dataclass(frozen=True)
class PatchSpec:
    type_name: str
    method_name: str
    parameter_count: int
    return_type: str
    stub: str
    purpose: str


PROPRESSENTER_PATCHES = (
    PatchSpec("ProPresenter.Registration.CallbackHandlers.UserMessageHandler", "DoesRequestPopUpAlert", 1, "System.Boolean", "boolean-false", "Suppress signed-out and first-launch popup nags"),
    PatchSpec("ProPresenter.Registration.CallbackHandlers.UserAlertHandler", "ShowSimpleBooleanSelection", 5, "System.Void", "void", "Suppress premium/no-seat/device-signed-out nag dialogs"),
    PatchSpec("ProPresenter.Registration.CallbackHandlers.UserAlertHandler", "HandleExpiredSubscription", 1, "System.Void", "void", "Suppress renew/continue-with-watermarks/sign-out dialog"),
    PatchSpec("ProPresenter.Registration.CallbackHandlers.UserAlertHandler", "HandleNoSeatsAvailableLegacy", 1, "System.Void", "void", "Suppress legacy no-seat premium dialog"),
    PatchSpec("ProPresenter.Services.ForcedShutdownMessageProvider", "ShowForcedApplicationShutdownDialog", 1, "System.Void", "void", "Suppress forced shutdown dialog and countdown"),
    PatchSpec("ProPresenter.Registration.CallbackHandlers.WatermarkHandler", "HandleResponse", 2, "System.Boolean", "boolean-false", "Suppress registration-driven watermark callback handling"),
    PatchSpec("ProPresenter.DO.Registration.State.RegistrationStateHandler", "get_BannerMessage", 0, "ProPresenter.DO.Registration.State.BannerMessage", "enum-zero", "Force the activation banner state to None"),
    PatchSpec("ProPresenter.ViewModels.Resi.ResiUpdateVM", "<.ctor>b__20_2", 0, "System.Void", "void", "Suppress Resi plugin-version update check"),
    PatchSpec("ProPresenter.ViewModels.Resi.ResiUpdateVM", "<.ctor>b__20_3", 0, "System.Void", "void", "Suppress periodic Resi update poll"),
    PatchSpec("ProPresenter.ViewModels.Resi.ResiUpdateVM", "<.ctor>b__20_4", 1, "System.Void", "void", "Suppress MainWindowContentRendered Resi update check"),
)

PROPRESSENTER_DO_PATCHES = (
    PatchSpec("ProPresenter.DO.Settings.App", "get_NotifyUpdates", 0, "System.Boolean", "boolean-false", "Force the update-notification setting off"),
    PatchSpec("ProPresenter.DO.Settings.App", "set_NotifyUpdates", 1, "System.Void", "void", "Prevent update-notification setting from being re-enabled"),
    PatchSpec("ProPresenter.DO.Settings.General", "get_EnableCrashReporting", 0, "System.Boolean", "boolean-false", "Force crash-reporting opt-in off"),
    PatchSpec("ProPresenter.DO.Settings.General", "set_EnableCrashReporting", 1, "System.Void", "void", "Prevent crash-reporting opt-in from being re-enabled"),
    PatchSpec("ProPresenter.DO.Settings.General", "get_EnableAnalytics", 0, "System.Boolean", "boolean-false", "Force analytics opt-in off"),
    PatchSpec("ProPresenter.DO.Settings.General", "set_EnableAnalytics", 1, "System.Void", "void", "Prevent analytics opt-in from being re-enabled"),
    PatchSpec("ProPresenter.DO.Updates.AppUpdateService", "Start", 0, "System.Void", "void", "Suppress automatic app-update poll startup"),
    PatchSpec("ProPresenter.DO.Updates.AppUpdateService", "InitiateCheckForUpgrades", 0, "System.Void", "void", "Suppress direct app-update checks"),
    PatchSpec("ProPresenter.DO.Updates.AppUpdateService", "InitiateCheckForUpgradesAfterChannelChange", 0, "System.Void", "void", "Suppress update checks after channel changes"),
    PatchSpec("ProPresenter.DO.Updates.AppUpdateService", "InitiateCheckForUpgradesAfterRegistrationChange", 0, "System.Void", "void", "Suppress update checks after registration changes"),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def default_cache_dir() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "ProPresPatcher"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ProPresPatcher"


def resolve_dnlib(version: str, cache_dir: Path) -> Path:
    version_dir = cache_dir / "dnlib" / version
    dll_path = version_dir / "dnlib.dll"
    version_dir.mkdir(parents=True, exist_ok=True)
    if dll_path.exists():
        if version == DEFAULT_DNLIB_VERSION and sha256(dll_path) != DEFAULT_DNLIB_DLL_SHA256:
            raise RuntimeError(f"cached dnlib.dll hash mismatch: {dll_path}")
        return dll_path

    package_path = version_dir / f"dnlib.{version}.nupkg"
    if not package_path.exists():
        url = f"https://api.nuget.org/v3-flatcontainer/dnlib/{version}/dnlib.{version}.nupkg"
        print(f"Downloading dnlib {version} to {package_path}", file=sys.stderr)
        with tempfile.NamedTemporaryFile(dir=version_dir, delete=False) as temporary:
            temporary_path = Path(temporary.name)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": CHROME_USER_AGENT})
            with urllib.request.urlopen(request, timeout=90) as response, temporary_path.open("wb") as target:
                shutil.copyfileobj(response, target)
            temporary_path.replace(package_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    package_hash = sha256(package_path)
    if version == DEFAULT_DNLIB_VERSION and package_hash != DEFAULT_DNLIB_NUPKG_SHA256:
        raise RuntimeError(f"dnlib package hash mismatch: expected {DEFAULT_DNLIB_NUPKG_SHA256}, got {package_hash}")
    if version != DEFAULT_DNLIB_VERSION:
        warnings.warn("Non-default dnlib version is not pinned by this launcher.", RuntimeWarning)

    candidates = (
        "lib/netstandard2.0/dnlib.dll",
        "lib/net6.0/dnlib.dll",
        "lib/net45/dnlib.dll",
    )
    with zipfile.ZipFile(package_path) as archive:
        member = next((name for name in candidates if name in archive.namelist()), None)
        if member is None:
            raise RuntimeError(f"No compatible dnlib.dll found in {package_path}")
        with archive.open(member) as source, dll_path.open("wb") as target:
            shutil.copyfileobj(source, target)
    if version == DEFAULT_DNLIB_VERSION and sha256(dll_path) != DEFAULT_DNLIB_DLL_SHA256:
        raise RuntimeError(f"extracted dnlib.dll hash mismatch: {dll_path}")
    return dll_path


def runtime_config(cache_dir: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(f"Runtime config not found: {explicit}")
        return explicit
    configured = os.environ.get("PYTHONNET_CORECLR_RUNTIME_CONFIG")
    if configured:
        return runtime_config(cache_dir, Path(configured))
    path = cache_dir / "runtime" / "net10.runtimeconfig.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "runtimeOptions": {
                    "tfm": DEFAULT_RUNTIME_TFM,
                    "framework": {"name": "Microsoft.NETCore.App", "version": DEFAULT_RUNTIME_VERSION},
                }
            }, indent=2) + "\n",
            encoding="ascii",
        )
    return path


def find_dotnet_root(explicit: Path | None) -> Path | None:
    candidates = []
    if explicit is not None:
        if not (explicit / "host" / "fxr").is_dir():
            raise ValueError(f"Invalid --dotnet-root; host/fxr was not found: {explicit}")
        candidates.append(explicit)
    if os.environ.get("DOTNET_ROOT"):
        candidates.append(Path(os.environ["DOTNET_ROOT"]))
    if os.environ.get("CONDA_PREFIX"):
        prefix = Path(os.environ["CONDA_PREFIX"])
        candidates.extend((prefix, prefix / "share" / "dotnet", prefix / "dotnet"))
    for candidate in candidates:
        if (candidate / "host" / "fxr").is_dir():
            return candidate
    return None


def load_dnlib(dll_path: Path, runtime_config_path: Path, dotnet_root: Path | None) -> dict[str, Any]:
    try:
        from pythonnet import get_runtime_info, load
    except ImportError as exc:
        raise RuntimeError("pythonnet is missing; run with `uv run` or `pixi run patch`.") from exc

    try:
        existing = get_runtime_info()
    except Exception:
        existing = None
    if existing is None:
        runtime_args = {"runtime_config": str(runtime_config_path)}
        if dotnet_root is not None:
            runtime_args["dotnet_root"] = str(dotnet_root)
        load("coreclr", **runtime_args)
    elif "coreclr" not in str(existing).lower():
        raise RuntimeError(f"pythonnet is already using an incompatible runtime: {existing}")

    import clr

    # pythonnet's CoreCLR loader resolves assembly names from sys.path more
    # reliably than absolute paths on current pythonnet releases.
    sys.path.insert(0, str(dll_path.parent))
    clr.AddReference(dll_path.stem)
    from dnlib.DotNet import DummyLogger, ModuleDefMD
    from dnlib.DotNet.Emit import CilBody, Code, Instruction, OpCodes
    from dnlib.DotNet.Writer import ModuleWriterOptions

    return {
        "ModuleDefMD": ModuleDefMD,
        "CilBody": CilBody,
        "Code": Code,
        "Instruction": Instruction,
        "OpCodes": OpCodes,
        "DummyLogger": DummyLogger,
        "ModuleWriterOptions": ModuleWriterOptions,
    }


def resolve_method(module: Any, spec: PatchSpec) -> Any:
    types = [item for item in module.GetTypes() if str(item.FullName) == spec.type_name]
    if len(types) != 1:
        raise RuntimeError(f"Expected one type {spec.type_name}, found {len(types)}")
    matches = []
    for method in types[0].Methods:
        explicit_parameters = int(method.Parameters.Count) - (0 if bool(method.IsStatic) else 1)
        if str(method.Name) == spec.method_name and explicit_parameters == spec.parameter_count:
            matches.append(method)
    if len(matches) != 1:
        raise RuntimeError(f"Expected one method {spec.type_name}::{spec.method_name}, found {len(matches)}")
    method = matches[0]
    actual_return_type = str(method.ReturnType.FullName)
    if actual_return_type != spec.return_type:
        raise RuntimeError(f"Return type mismatch for {spec.type_name}::{spec.method_name}: {actual_return_type}")
    return method


def build_stub(spec: PatchSpec, bridge: dict[str, Any]) -> Any:
    body = bridge["CilBody"]()
    body.MaxStack = 1
    if spec.stub == "void":
        body.Instructions.Add(bridge["Instruction"].Create(bridge["OpCodes"].Ret))
    elif spec.stub in {"boolean-false", "enum-zero"}:
        body.Instructions.Add(bridge["Instruction"].Create(bridge["OpCodes"].Ldc_I4_0))
        body.Instructions.Add(bridge["Instruction"].Create(bridge["OpCodes"].Ret))
    else:
        raise RuntimeError(f"Unsupported stub kind: {spec.stub}")
    return body


def verify_method(method: Any, spec: PatchSpec, bridge: dict[str, Any]) -> None:
    if not bool(method.HasBody):
        raise RuntimeError(f"Patched method has no body: {spec.type_name}::{spec.method_name}")
    codes = [str(item.OpCode.Code).lower() for item in method.Body.Instructions]
    expected = ["ret"] if spec.stub == "void" else ["ldc_i4_0", "ret"]
    if codes != expected:
        raise RuntimeError(f"Patched method body mismatch for {spec.type_name}::{spec.method_name}: {codes}")


def patch_assembly(input_path: Path, output_path: Path, specs: tuple[PatchSpec, ...], bridge: dict[str, Any]) -> list[dict[str, Any]]:
    if output_path.exists():
        raise FileExistsError(f"Refusing to overwrite existing output: {output_path}")
    module = bridge["ModuleDefMD"].Load(str(input_path))
    assembly = module.Assembly
    assembly_name = str(assembly.Name.String) if assembly is not None else input_path.stem
    assembly_version = str(assembly.Version) if assembly is not None else "unknown"
    results = []
    for spec in specs:
        method = resolve_method(module, spec)
        original_count = int(method.Body.Instructions.Count) if bool(method.HasBody) else 0
        method.Body = build_stub(spec, bridge)
        token = str(method.MDToken)
        print(f"Patched {assembly_name}!{spec.type_name}::{spec.method_name} {token} -> {spec.stub}")
        results.append({
            "Assembly": assembly_name,
            "InputPath": str(input_path),
            "OutputPath": str(output_path),
            "AssemblyVersion": assembly_version,
            "TypeName": spec.type_name,
            "MethodName": spec.method_name,
            "MethodToken": token,
            "ParameterCount": spec.parameter_count,
            "ReturnType": spec.return_type,
            "OriginalInstructionCount": original_count,
            "PatchedInstructionCount": int(method.Body.Instructions.Count),
            "Stub": spec.stub,
            "Purpose": spec.purpose,
        })

    options = bridge["ModuleWriterOptions"](module)
    options.Logger = bridge["DummyLogger"].NoThrowInstance
    module.Write(str(output_path), options)

    verification_module = bridge["ModuleDefMD"].Load(str(output_path))
    output_hash = sha256(output_path)
    for result, spec in zip(results, specs):
        verify_method(resolve_method(verification_module, spec), spec, bridge)
        result["OutputSha256"] = output_hash
    print(f"Saved and verified: {output_path} sha256={output_hash}")
    return results


def validate_input(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Input assembly not found: {path}")
    if path.suffix.lower() != ".dll":
        raise ValueError(f"Expected a .dll input: {path}")


def backup_inputs(inputs: tuple[Path, ...], backup_dir: Path) -> dict[str, str]:
    if backup_dir.exists():
        raise FileExistsError(f"Refusing to reuse existing backup directory: {backup_dir}")
    backup_dir.mkdir(parents=True)
    hashes = {}
    for source in inputs:
        destination = backup_dir / source.name
        shutil.copyfile(source, destination)
        source_hash = sha256(source)
        if sha256(destination) != source_hash:
            raise IOError(f"Backup hash mismatch for {source}")
        hashes[source.name] = source_hash
        print(f"Backup: {destination} sha256={source_hash}")
    return hashes


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Patch ProPresenter managed DLLs without dnSpy state.")
    parser.add_argument("--propresenter", type=Path, required=True)
    parser.add_argument("--do", dest="do_assembly", type=Path, required=True)
    parser.add_argument("--propresenter-name", help="Output filename for ProPresenter.dll (default: <input stem>.patched.dll)")
    parser.add_argument("--do-name", help="Output filename for ProPresenter.DO.dll (default: <input stem>.patched.dll)")
    parser.add_argument("--output-dir", type=Path, default=Path("patch-output"))
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--dnlib-path", type=Path)
    parser.add_argument("--dnlib-version", default=DEFAULT_DNLIB_VERSION)
    parser.add_argument("--dotnet-root", type=Path, help="Override the bundled or host .NET root used by pythonnet CoreCLR")
    parser.add_argument("--runtime-config", type=Path)
    return parser.parse_args(argv)


def output_filename(input_path: Path, override: str | None) -> str:
    filename = override or f"{input_path.stem}.patched.dll"
    candidate = Path(filename)
    if candidate.name != filename or candidate.suffix.lower() != ".dll":
        raise ValueError(f"Output name must be a filename ending in .dll: {filename}")
    return filename


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        inputs = (args.propresenter.resolve(), args.do_assembly.resolve())
        for path in inputs:
            validate_input(path)
        output_dir = args.output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        backup_dir = (args.backup_dir or output_dir / "baseline").resolve()
        output_paths = (
            output_dir / output_filename(inputs[0], args.propresenter_name),
            output_dir / output_filename(inputs[1], args.do_name),
        )
        if any(path.exists() for path in output_paths):
            raise FileExistsError("One or more output DLLs already exist; choose a new output directory.")
        manifest_path = output_dir / "ProPresPatcher.manifest.json"
        if manifest_path.exists():
            raise FileExistsError(f"Refusing to overwrite existing manifest: {manifest_path}")

        cache_dir = (args.cache_dir or default_cache_dir()).resolve()
        dnlib_path = args.dnlib_path.resolve() if args.dnlib_path else resolve_dnlib(args.dnlib_version, cache_dir)
        if not dnlib_path.is_file():
            raise FileNotFoundError(f"dnlib.dll not found: {dnlib_path}")
        dotnet_root = find_dotnet_root(args.dotnet_root.resolve() if args.dotnet_root else None)
        bridge = load_dnlib(dnlib_path, runtime_config(cache_dir, args.runtime_config), dotnet_root)
        backups = backup_inputs(inputs, backup_dir)
        results = []
        results.extend(patch_assembly(inputs[0], output_paths[0], PROPRESSENTER_PATCHES, bridge))
        results.extend(patch_assembly(inputs[1], output_paths[1], PROPRESSENTER_DO_PATCHES, bridge))
        manifest = {
            "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
            "patcher": "ProPresPatcherPy",
            "dnlibVersion": args.dnlib_version if args.dnlib_path is None else "external",
            "dnlibPath": str(dnlib_path),
            "dotnetRoot": str(dotnet_root) if dotnet_root else "host-default",
            "backupDirectory": str(backup_dir),
            "backups": backups,
            "outputs": results,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print(f"Manifest: {manifest_path}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
