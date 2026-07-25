# ProPresPatcherPy

Portable Python entry point for the managed ProPresenter competition patch.
It uses `pythonnet` to load dnlib and automatically downloads and caches the
pinned `dnlib` 4.5.0 NuGet package. Users do not need to manually locate or
copy `dnlib.dll`.

## Requirements

- Windows 10/11 for running ProPresenter
- .NET 10 runtime or SDK when using uv; pixi installs `dotnet-runtime 10.0.0` into its environment
- One of `uv` or `pixi`

The pixi path is self-contained with respect to .NET: `dotnet-runtime 10.0.0`
is installed into the pixi environment and selected automatically through
`CONDA_PREFIX`. The uv path falls back to a host .NET runtime unless
`--dotnet-root` points at a private runtime. The .NET SDK is not required.

## Run With uv

From this directory:

```powershell
uv run python -m propres_patcher `
  --propresenter "C:\Program Files\Renewed Vision\ProPresenter\ProPresenter.dll" `
  --do "C:\Program Files\Renewed Vision\ProPresenter\ProPresenter.DO.dll" `
  --output-dir "$env:TEMP\codex-propres-patches\python"
```

`uv` creates the environment and installs `pythonnet==3.0.5`. The first run
downloads dnlib from NuGet into the user cache; later runs reuse it.

## Run With pixi

```powershell
pixi install
pixi run patch -- `
  --propresenter "C:\Program Files\Renewed Vision\ProPresenter\ProPresenter.dll" `
  --do "C:\Program Files\Renewed Vision\ProPresenter\ProPresenter.DO.dll" `
  --output-dir "$env:TEMP\codex-propres-patches\python"
```

The `pixi.toml` environment pins Python 3.12, pythonnet 3.0.5, and the .NET
10 runtime. It supports `win-64` and `linux-64` through conda-forge's runtime
package.
On a locked-down machine, set `PIXI_CACHE_DIR` to an absolute writable path,
for example:

```powershell
$env:PIXI_CACHE_DIR = "$PWD\.pixi-cache"
pixi install
```

## Offline or Controlled Use

If dnlib is already available, bypass the downloader:

```powershell
uv run python -m propres_patcher `
  --dnlib-path "C:\path\to\dnlib.dll" `
  --runtime-config "C:\path\to\runtimeconfig.json" `
  --dotnet-root "C:\path\to\dotnet" `
  --propresenter "C:\path\to\ProPresenter.dll" `
  --do "C:\path\to\ProPresenter.DO.dll" `
  --output-dir "C:\path\to\output"
```

The default NuGet package and extracted `dnlib.dll` are hash-checked. The
default package SHA-256 is
`63bc2f9579568204cc8b30fa9f6700a231bcf868a8032e09118887af3eafee58`, and the
default `netstandard2.0/dnlib.dll` SHA-256 is
`566fdab59c91a3c2eab14a22b67f01cb3c0cdb48fa9b9b6677e2b32998636efb`.
Non-default dnlib versions are allowed for research but are explicitly
reported as unpinned.

The input DLLs are never overwritten. A new backup directory is created and
hash-checked, output files are refused if they already exist, and every output
method is re-opened and verified as either `ret` or `ldc.i4.0; ret`.

## Output Names

The default output names are derived from the inputs:

- `ProPresenter.dll` -> `ProPresenter.patched.dll`
- `ProPresenter.DO.dll` -> `ProPresenter.DO.patched.dll`

Override them without changing the patch logic:

```powershell
pixi run patch -- `
  --propresenter-name "main.patched.dll" `
  --do-name "data.patched.dll" `
  --propresenter "C:\path\to\ProPresenter.dll" `
  --do "C:\path\to\ProPresenter.DO.dll" `
  --output-dir "C:\path\to\output"
```

## MCP-Derived OTA Probe

The sample does not use the public download page as its update source. MCP
analysis of `ProCore.dll` found the registration API base
`https://api.renewedvision.com/v1.1` (with a staging equivalent) and the
`/pro/upgrade` route. The request uses `platform=win32`, `osVersion`,
`appVersion`, `buildNumber`, `includeNotes=0`, and `format`; the response
contains `buildNumber`, `version`, `downloadUrl`, `channel`, `isBeta`, and
`isAvailable`. The managed update path consumes that URL through
`BuildInformation.DownloadUrl`.

Run the lightweight, standard-library-only probe from the repository root:

```bash
python ota_probe.py \
  --state-file state/ota-state.json \
  --result-file ota-result.json \
  --next-state-file ota-next-state.json
```

It uses conditional HTTP validators when the state file contains them, filters
to production builds by default, refuses non-HTTPS download URLs, and reports
`changed`, `build_number`, `version`, and `download_url`. It does not update
the state file itself; the Windows release step advances state only after publishing
the patched artifacts. Use `--api-base` to select the sample's staging API or
an internal competition endpoint, and `--allowed-download-host` to enforce a
download host allowlist.

The repository workflow at `.github/workflows/ota-patch-release.yml` separates
detection from the Windows extraction, deterministic patching, packaging, and
release work. The Windows job runs only when the MCP-derived API reports a new
build and performs patching and publishing on the same runner, avoiding a
second artifact transfer for the patched payload.
Before extraction, `setup_innoextract.py` queries the latest release API for
`UserUnknownFactor/innoextract_win`, selects its single ZIP asset, verifies the
GitHub-provided SHA-256 digest, and caches the Windows executable under
`tools/innoextract-win/` (ignored by git). The explicit fork is the first
extractor, followed by a compatible system `innoextract`, 7z/7zz, and finally
an explicit silent install into a temporary directory. Extraction diagnostics
include the tool release metadata, `innoextract-version.txt`,
`extraction-summary.json`, and `installer-output.log`; the full extracted
installer tree is intentionally not uploaded. API probing,
installer downloads, dnlib resolution, and the GitHub tool bootstrap all use
the shared Chrome User-Agent in `propres_patcher/user_agent.py`.
The Windows release step packages the patched DLLs, metadata, and `SHA256SUMS.json`
into one versioned `ProPresPatcher-Result-<version>-<build>.zip` and publishes
only that ZIP; GitHub Release provides the archive-level digest.

## Runner Choice

The managed patch itself is portable: `dnlib`, Pythonnet, and the pixi-managed
.NET 10 runtime can run on Linux or Windows. The workflow uses the standard
`ubuntu-latest` runner only for lightweight detection and `windows-latest` for
extraction, patching, packaging, and publishing. The current sample uses a newer
Inno Setup loader than the installed
`innoextract 1.9` parser understands; the Windows-only fork supports the
sample's loader and is therefore preferred on `windows-latest`. Linux remains
suitable when a compatible native Inno extractor is available, or for the
managed patch after assemblies have already been extracted; no WPF execution
is required during patch generation.
