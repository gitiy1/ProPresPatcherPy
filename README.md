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
