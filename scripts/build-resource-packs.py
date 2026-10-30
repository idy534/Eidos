"""Build fixed release archives, then remove document payloads from the App.

Run after the existing locked Node/Python/font builders. This script never
resolves packages or installs dependencies on a target machine.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import tarfile
import tempfile

from eidos_runtime.models.managed_resources import ResourceIndex, ResourcePack
from eidos_runtime.models.runtime_dependencies import (
    RuntimeDependencyExecutable, RuntimeDependencyInventoryFile,
    RuntimeDependencyManifest, RuntimeDependencyNodePackage, RuntimeDependencyPythonPackage,
)

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / "resources/runtime-dependencies/resource-release.json").read_text())
VERSION = RELEASE["version"]
NODE_VERSION = json.loads((ROOT / "resources/runtime-dependencies/node/node-release.json").read_text())["version"]
PYTHON_GROUPS = {
    "pandas": "data", "numpy": "data", "python-dateutil": "data", "six": "data",
    "reportlab": "pdf", "pdfminer-six": "pdf", "pdfplumber": "pdf", "pypdfium2": "pdf",
}
TITLES = {
    "document-python": "基础文档处理", "node": "文档 Node 解释器", "document-node": "文档 JavaScript 库",
    "office": "Office 转换与重算", "images": "图片转换", "pdf": "PDF 生成与渲染",
    "data": "表格数据分析", "ocr": "OCR 引擎", "ocr-eng": "英文 OCR", "ocr-chi-sim": "中文 OCR",
    "fonts": "文档字体",
}
REQUIRES = {
    "document-node": ("node",), "office": ("document-node", "fonts"),
    "images": ("document-node",), "pdf": ("document-python",), "data": ("document-python",),
    "ocr": ("document-node",), "ocr-eng": ("ocr",), "ocr-chi-sim": ("ocr",),
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def python_packages(bundle: Path, staging: Path) -> tuple[dict[str, list[RuntimeDependencyPythonPackage]], list[RuntimeDependencyPythonPackage]]:
    source, core = bundle / "dependencies/python", bundle / "app"
    declared = {}
    for line in (ROOT / "resources/runtime-dependencies/python/top_level.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            package, name = line.split(": ")
            declared[package.split("==")[0]] = name
    distributions = list(importlib.metadata.distributions(path=[str(source)]))
    core_versions = {dist.metadata["Name"].lower().replace("_", "-"): dist.version
                     for dist in importlib.metadata.distributions(path=[str(core)])}
    groups: dict[str, list[RuntimeDependencyPythonPackage]] = {}
    shared = []
    for dist in distributions:
        name = dist.metadata["Name"].lower().replace("_", "-").replace(".", "-")
        descriptor = RuntimeDependencyPythonPackage(name=name, import_name=declared[name], version=dist.version)
        if core_versions.get(name) == dist.version:
            shared.append(descriptor)
            continue
        group = PYTHON_GROUPS.get(name, "document-python")
        groups.setdefault(group, []).append(descriptor)
        record = dist.read_text("RECORD")
        if record is None:
            raise ValueError(f"No wheel RECORD for {name}")
        for row in csv.reader(record.splitlines()):
            relative = Path(row[0])
            if (relative.is_absolute() or ".." in relative.parts or
                    any(part in {"tests", "test", "__pycache__"} for part in relative.parts)):
                continue
            file = source / relative
            if file.is_file() and file.suffix != ".pyc":
                copy_file(file, staging / group / "lib" / relative)
    return groups, shared


def node_group(relative: Path) -> str | None:
    parts = relative.parts
    if parts[0].startswith("."):
        return None
    if any(p in {"test", "tests", "examples", ".gitkeep", "__pycache__"} for p in parts):
        return None
    if parts[0] == "@deepseek-ai":
        if parts[1] == "libreoffice-kit-wasm":
            return None
        if parts[1] == "libreoffice-kit-darwin-arm64":
            return "office"
        if "frame-forwarding" in parts:
            return None
    if parts[0] in {"react", "react-dom", "react-icons", "scheduler"}:
        return None
    if parts[0] == "@img":
        return "images"
    if parts[0] == "tesseract.js-core":
        # Tesseract.js 7.0.0 passes a boolean to Node getCore(), whose OEM
        # comparison selects the full core even for LSTM_ONLY. Keep both
        # variants until the locked upstream adapter is compatible.
        return "ocr"
    if parts[0] == "@tesseract.js-data":
        if "4.0.0_best_int" in parts:
            return None
        return "ocr-eng" if parts[1] == "eng" else "ocr-chi-sim"
    return "document-node"


def node_packages(bundle: Path, staging: Path) -> dict[str, list[RuntimeDependencyNodePackage]]:
    source = bundle / "dependencies/node/node_modules"
    for file in sorted(source.rglob("*")):
        if file.is_symlink():
            raise ValueError("Node source must already be flattened")
        if file.is_file():
            relative = file.relative_to(source)
            group = node_group(relative)
            if group:
                copy_file(file, staging / group / "node_modules" / relative)
    groups = {}
    for group in staging.iterdir():
        modules = group / "node_modules"
        if not modules.exists():
            continue
        descriptors = []
        roots = [p for p in modules.iterdir() if not p.name.startswith(".")]
        roots = [child for p in roots for child in (list(p.iterdir()) if p.name.startswith("@") else [p])]
        for package in roots:
            metadata = package / "package.json"
            if metadata.is_file():
                value = json.loads(metadata.read_text())
                descriptors.append(RuntimeDependencyNodePackage(name=value["name"], version=value["version"]))
        groups[group.name] = descriptors
    copy_file(bundle / "dependencies/node/bin/node", staging / "node/bin/node")
    copy_file(bundle / "dependencies/node/LICENSE", staging / "node/LICENSE")
    return groups


def pack_group(group: Path, output: Path, python: list[RuntimeDependencyPythonPackage], node: list[RuntimeDependencyNodePackage]) -> ResourcePack:
    pack_id = group.name
    version = NODE_VERSION if pack_id == "node" else VERSION
    files = [p for p in sorted(group.rglob("*")) if p.is_file()]
    inventory = tuple(RuntimeDependencyInventoryFile(path=p.relative_to(group).as_posix(), sha256=digest(p)) for p in files)
    executable = ()
    if pack_id == "node":
        executable = (RuntimeDependencyExecutable(name="node", version=NODE_VERSION, path="bin/node", sha256=digest(group / "bin/node")),)
    if pack_id == "office":
        relative = "node_modules/@deepseek-ai/libreoffice-kit-darwin-arm64/bin/dsoffice"
        executable = (RuntimeDependencyExecutable(name="office", version="0.2.3", path=relative, sha256=digest(group / relative)),)
    manifest = RuntimeDependencyManifest(
        schema_version=1, bundle_id=f"com.idy.eidos.resources.{pack_id}", bundle_version=version, target="darwin-arm64",
        executables=executable, python_path=("lib",) if python else (), python_packages=tuple(python),
        node_modules="node_modules" if node else None, node_packages=tuple(node), files=inventory,
    )
    filename = f"eidos-{pack_id}-{version}-darwin-arm64.tar.gz"
    archive_path = output / filename
    with archive_path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for file in files:
                entry = archive.gettarinfo(str(file), file.relative_to(group).as_posix())
                entry.uid = entry.gid = entry.mtime = 0
                entry.uname = entry.gname = ""
                entry.mode = 0o755 if file.stat().st_mode & 0o111 else 0o644
                with file.open("rb") as stream:
                    archive.addfile(entry, stream)
    if pack_id == "node":
        location = f"runtimes/node/{version}/darwin-arm64"
    elif python:
        location = f"packages/python/{pack_id}/{version}/cp312-darwin-arm64"
    elif pack_id == "office":
        location = f"native/office/{version}/darwin-arm64"
    elif pack_id.startswith("ocr-"):
        location = f"models/ocr/{pack_id.removeprefix('ocr-')}/{version}"
    elif pack_id == "fonts":
        location = f"fonts/document/{version}"
    else:
        location = f"packages/node/{pack_id}/{version}"
    pack = ResourcePack(
        id=pack_id, title=TITLES[pack_id], version=version, location=location,
        url=f"https://github.com/{RELEASE['repository']}/releases/download/{RELEASE['tag']}/{filename}",
        archive_sha256=digest(archive_path), archive_bytes=archive_path.stat().st_size,
        expanded_bytes=sum(p.stat().st_size for p in files), requires=REQUIRES.get(pack_id, ()),
        font_directory="fonts" if pack_id == "fonts" else None, manifest=manifest,
        python_abi="cp312" if python else None,
    )
    print(f"Built {pack.title}: {pack.archive_bytes / 1024**2:.1f} MiB download, {pack.expanded_bytes / 1024**2:.1f} MiB installed", flush=True)
    return pack


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "build/macos-runtime")
    parser.add_argument("--output", type=Path, default=ROOT / "build/resource-packages")
    parser.add_argument("--keep-payloads", action="store_true")
    args = parser.parse_args()
    bundle, output = args.bundle.resolve(), args.output.resolve()
    controlled = (ROOT / "build").resolve()
    if not bundle.is_relative_to(controlled) or not output.is_relative_to(controlled):
        raise ValueError("resource build paths must be inside the repository build directory")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="resource-packs-", dir=output) as folder:
        stage = Path(folder)
        python, shared = python_packages(bundle, stage)
        node = node_packages(bundle, stage)
        shutil.copytree(bundle / "dependencies/fonts", stage / "fonts/fonts")
        packs = tuple(pack_group(group, output, python.get(group.name, []), node.get(group.name, []))
                      for group in sorted(stage.iterdir()) if group.is_dir())
    index = ResourceIndex(version=VERSION, packs=packs)
    encoded = index.model_dump_json(by_alias=True).encode()
    (bundle / "resource-index.json").write_bytes(encoded)
    (output / "resource-index.json").write_bytes(encoded)
    (output / "shared-python.json").write_text(json.dumps([p.model_dump(by_alias=True) for p in shared]))
    loader = bundle / "app/eidos_runtime/resources/runtime-loader.mjs"
    copy_file(ROOT / "resources/runtime-dependencies/node/runtime-loader.mjs", loader)
    if not args.keep_payloads:
        shutil.rmtree(bundle / "dependencies")


if __name__ == "__main__":
    main()
