"""Build the Windows x64 Runtime from locked production inputs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.12.13"
RIPGREP_VERSION = "15.2.0"
GIT_VERSION = "2.56.1"
RIPGREP_ARCHIVE = f"ripgrep-{RIPGREP_VERSION}-x86_64-pc-windows-msvc"
RIPGREP_URL = f"https://github.com/BurntSushi/ripgrep/releases/download/{RIPGREP_VERSION}/{RIPGREP_ARCHIVE}.zip"
GIT_URL = f"https://github.com/git-for-windows/git/releases/download/v{GIT_VERSION}.windows.1/MinGit-{GIT_VERSION}-64-bit.zip"
RESOURCE_INPUTS = ROOT / "resources/runtime-dependencies"
CRT_SHA256 = "4aaf54db0bfc9435f7c3660e1a00237a4b556042bfeea64bde44c2e0194e6ee5"
# Microsoft.VC.14.44.17.14.CRT.Redist.X64.base 14.44.35211 from the VS 2022 release channel.
CRT_URL = (
    "https://download.visualstudio.microsoft.com/download/pr/45d3b8dd-bced-4b37-9974-142f748d710c/"
    f"{CRT_SHA256}/Microsoft.VC.14.44.17.14.CRT.Redist.X64.base.vsix"
)


def _run(*args: str | Path, env: dict[str, str] | None = None) -> None:
    subprocess.run([str(arg) for arg in args], cwd=ROOT, env=env, check=True)


def _download(url: str, destination: Path, expected_bytes: int | None, expected_digest: str) -> None:
    with urlopen(url, timeout=60) as response, destination.open("xb") as target:
        digest = hashlib.sha256()
        limit = expected_bytes if expected_bytes is not None else 32 * 1024**2
        received = 0
        while chunk := response.read(min(65536, limit - received + 1)):
            received += len(chunk)
            if received > limit:
                raise ValueError("Windows resource download exceeded its declared size")
            target.write(chunk)
            digest.update(chunk)
        if (expected_bytes is not None and received != expected_bytes) or digest.hexdigest() != expected_digest:
            raise ValueError("Windows resource download failed its integrity check")


def _extract(archive: Path, destination: Path) -> None:
    with ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            path = PurePosixPath(entry.filename)
            if (
                path.is_absolute()
                or not path.parts
                or any(
                    part in {".", ".."} or part.endswith((".", " "))
                    or "\\" in part or ":" in part or PureWindowsPath(part).is_reserved()
                    for part in path.parts
                )
                or stat.S_ISLNK(entry.external_attr >> 16)
            ):
                raise ValueError("unsafe path in Windows tool archive")
        bundle.extractall(destination)


def _install_tools(stage: Path, scratch: Path) -> None:
    rg_zip = scratch / "ripgrep.zip"
    _download(RIPGREP_URL, rg_zip, 1789611, "71b2fef860abe467217a538ff31de02f5258807c0129f771846f87bd029aafc5")
    _extract(rg_zip, scratch / "ripgrep")
    rg_root = stage / "app/eidos_runtime/resources/bin/ripgrep"
    manifest_path = rg_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["version"] != RIPGREP_VERSION:
        raise ValueError("the two ripgrep bundles must use the same version")
    base = scratch / "ripgrep" / RIPGREP_ARCHIVE
    shutil.copytree(base, rg_root / "win32-x64")
    relative = "win32-x64/rg.exe"
    with (rg_root / relative).open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest["artifacts"]["win32-x64"] = {"path": relative, "sha256": digest}
    shutil.rmtree(rg_root / "darwin-arm64")
    del manifest["artifacts"]["darwin-arm64"]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _run(rg_root / relative, "--version")

    git_zip = scratch / "git.zip"
    _download(GIT_URL, git_zip, 39849768, "4a92850b6e917da4fd010669b614b55dccf5b0ec333a534cc4be4700dfefdafd")
    _extract(git_zip, stage / "git")
    _run(stage / "git/cmd/git.exe", "--version")


def _runtime_environment(app: Path) -> dict[str, str]:
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith(("PYTHON", "EIDOS_", "NODE_OPTIONS"))}
    environment.update({
        "PYTHONPATH": str(app),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OTEL_SDK_DISABLED": "true",
    })
    return environment


def _install_node_resources(dependencies: Path, scratch: Path, system_root: Path) -> None:
    inputs = RESOURCE_INPUTS / "node"
    release = json.loads((inputs / "node-release.json").read_text(encoding="utf-8"))
    version = release["version"]
    platform_release = release["windowsX64"]
    archive = scratch / "node.zip"
    _download(f"https://nodejs.org/download/release/v{version}/node-v{version}-win-x64.zip",
              archive, platform_release["archiveBytes"], platform_release["sha256"])
    _extract(archive, scratch / "node")
    source = scratch / "node" / f"node-v{version}-win-x64"
    project = dependencies / "node"
    binaries = project / "bin"
    binaries.mkdir(parents=True)
    shutil.copy2(source / "node.exe", binaries / "node.exe")
    shutil.copy2(source / "LICENSE", project / "LICENSE")

    for name in ("package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml"):
        shutil.copy2(inputs / name, project / name)
    # pnpm on Windows can be a .cmd shim. All command arguments here are fixed;
    # the possibly Unicode staging path is passed as the working directory.
    environment = {key: value for key, value in os.environ.items() if key.upper() != "NODE_OPTIONS"}
    subprocess.run([
        str(system_root / "System32/cmd.exe"), "/d", "/c",
        "pnpm install --frozen-lockfile --ignore-scripts --prod --node-linker=hoisted --reporter=append-only",
    ], cwd=project, env=environment, check=True)
    package = project / "node_modules/@deepseek-ai/libreoffice-kit-win32-x64"
    office = package / "bin"
    if not (office / "dsoffice.exe").is_file():
        raise ValueError("locked Node install is missing the Windows Office engine")
    # Use the app-local x64 release DLLs only. This keeps the NSIS install
    # per-user and leaves machine-wide redistributable installations alone.
    crt_archive = scratch / "msvc.vsix"
    _download(CRT_URL, crt_archive, 3224191, CRT_SHA256)
    crt = "Contents/VC/Redist/MSVC/14.44.35112/x64/Microsoft.VC143.CRT/"
    with ZipFile(crt_archive) as bundle:
        for entry in bundle.infolist():
            relative = entry.filename.removeprefix(crt)
            if "/" not in relative and relative.endswith(".dll") and entry.filename.startswith(crt):
                contents = bundle.read(entry)
                (binaries / relative).write_bytes(contents)
                (office / relative).write_bytes(contents)
    if not (office / "msvcp140.dll").is_file() or not (binaries / "vcruntime140_1.dll").is_file():
        raise ValueError("Windows app-local C++ runtime is incomplete")
    output = subprocess.check_output([str(binaries / "node.exe"), "--version"], env=environment, text=True)
    if output.strip() != f"v{version}":
        raise ValueError("bundled Node version does not match its lock")


def _install_managed_resources(stage: Path, python: Path, scratch: Path) -> None:
    dependencies = stage / "dependencies"
    system_root = Path(os.environ["SYSTEMROOT"])
    _install_node_resources(dependencies, scratch, system_root)
    helper = dependencies / "browser/bin/open-connect-page.exe"
    helper.parent.mkdir(parents=True)
    _run(system_root / "Microsoft.NET/Framework64/v4.0.30319/csc.exe", "/nologo", "/optimize+",
         "/target:winexe", "/platform:x64", f"/out:{helper}", ROOT / "scripts/windows-browser-connect.cs")

    requirements = scratch / "document-requirements.txt"
    _run("uv", "export", "--locked", "--only-group", "document-skills", "--no-emit-project",
         "--output-file", requirements, "--python", python)
    _run("uv", "pip", "install", "--python", python, "--target", dependencies / "python", "--link-mode", "copy",
         "--only-binary", ":all:", "--require-hashes", "--requirements", requirements)
    fonts = dependencies / "fonts"
    fonts.mkdir()
    for font in json.loads((RESOURCE_INPUTS / "fonts/files.json").read_text(encoding="utf-8"))["files"]:
        _download(font["url"], fonts / font["filename"], None, font["sha256"])

    archives = stage / "resource-packs"
    _run(python, ROOT / "scripts/build-resource-packs.py", "--target", "win32-x64", "--bundle", stage,
         "--output", archives, env=_runtime_environment(stage / "app"))
    generator = ROOT / "scripts/generate-runtime-manifest.mjs"
    version = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
    _run("node", generator, "--target", "win32-x64", "--core-only", "--bundle-root", stage,
         "--bundle-id", "com.idy.eidos", "--bundle-version", version, "--python-version", PYTHON_VERSION,
         "--git-version", GIT_VERSION, "--ripgrep-version", RIPGREP_VERSION,
         "--shared-python", archives / "shared-python.json")
    _run("node", generator, "--verify", "--bundle-root", stage)
    (archives / "resource-index.json").unlink()
    (archives / "shared-python.json").unlink()


def _verify_runtime(python: Path, app: Path, scratch: Path) -> None:
    environment = _runtime_environment(app)
    environment.update({
        "USERPROFILE": str(scratch / "验证家目录"),
        "EIDOS_DATA_DIR": str(scratch / "验证资料"),
        "EIDOS_PROJECTLESS_ROOT": str(scratch / "验证工作区"),
    })
    requests = [
        {"jsonrpc": "2.0", "id": "client-init", "method": "initialize", "params": {
            "client": {"name": "windows-bundle-smoke", "version": "0.1.0"}, "protocolVersion": 1,
        }},
        {"jsonrpc": "2.0", "id": "client-health", "method": "runtime/health", "params": {}},
        {"jsonrpc": "2.0", "id": "client-resources", "method": "resource/status", "params": {}},
        {"jsonrpc": "2.0", "id": "client-shutdown", "method": "runtime/shutdown", "params": {}},
    ]
    completed = subprocess.run(
        [str(python), "-u", "-m", "eidos_runtime"],
        cwd=app, env=environment,
        input="".join(json.dumps(request) + "\n" for request in requests).encode("utf-8"),
        stdout=subprocess.PIPE, check=True, timeout=60,
    )
    messages = [json.loads(line) for line in completed.stdout.decode("utf-8").splitlines()]
    responses = {message["id"]: message.get("result") for message in messages if "id" in message}
    if not isinstance(responses.get("client-init"), dict) or not isinstance(responses.get("client-shutdown"), dict):
        raise RuntimeError("the bundled Runtime did not initialize and shut down")
    health = responses.get("client-health")
    if not isinstance(health, dict) or health.get("state") != "ready":
        raise RuntimeError("the bundled Runtime is not ready")
    resources = responses.get("client-resources")
    if not isinstance(resources, dict) or not resources.get("available"):
        raise RuntimeError("the bundled managed resources are not available")


def main() -> None:
    if sys.platform != "win32" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("Windows x64 is required to build the Windows Runtime bundle")
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    if getattr(build.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError("build directory must be a local directory")
    target = build / "windows-runtime"
    with tempfile.TemporaryDirectory(prefix=".windows-runtime-", dir=build) as staging:
        scratch = Path(staging)
        stage = scratch / "bundle"
        stage.mkdir(mode=0o700)
        python_root = scratch / "python"
        _run("uv", "python", "install", PYTHON_VERSION, "--install-dir", python_root, "--no-bin")
        installations = [
            path for path in python_root.glob(f"cpython-{PYTHON_VERSION}-*")
            if (path / "python.exe").is_file()
        ]
        if len(installations) != 1:
            raise RuntimeError("uv did not install the expected Windows CPython layout")
        shutil.move(installations[0], stage / "python")
        python = stage / "python/python.exe"
        app = stage / "app"
        app.mkdir()
        site_packages = stage / "python/Lib/site-packages"
        requirements = scratch / "requirements.txt"
        _run("uv", "export", "--locked", "--no-dev", "--no-emit-project", "--format", "requirements.txt",
             "--output-file", requirements, "--python", python)
        # Keep wheel .pth files in an actual site directory (pywin32 uses one).
        _run("uv", "pip", "install", "--python", python, "--target", site_packages, "--link-mode", "copy",
             "--only-binary", ":all:", "--require-hashes", "--requirements", requirements)
        (site_packages / ".lock").unlink(missing_ok=True)
        shutil.copytree(
            ROOT / "runtime/eidos_runtime", app / "eidos_runtime",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
        )
        _install_tools(stage, scratch)
        _install_managed_resources(stage, python, scratch)
        _verify_runtime(python, app, scratch)
        if target.exists() or target.is_symlink():
            if getattr(target.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("Windows bundle target must not be a reparse point")
            shutil.rmtree(target)
        stage.rename(target)


if __name__ == "__main__":
    main()
