from __future__ import annotations

from pathlib import Path
import threading
import os

import pytest

import eidos_runtime.repo_intelligence.inventory as inventory_module
from eidos_runtime.repo_intelligence.inventory import (
    InventoryCanceled,
    RepositoryInventoryBuilder,
)


def test_inventory_is_bounded_deterministic_and_excludes_ignored_sensitive_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".gitignore").write_text("ignored/\n*.generated.py\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "src" / "generated.generated.py").write_text("generated\n", encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored" / "secret.py").write_text("secret\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=do-not-index\n", encoding="utf-8")
    (root / "README.md").write_bytes(b"# Eidos\n")

    first = RepositoryInventoryBuilder(root).build()
    second = RepositoryInventoryBuilder(root).build()

    assert first.complete is True
    assert [record.path for record in first.files] == [
        ".gitignore",
        "README.md",
        "src/main.py",
    ]
    assert first.snapshot_hash == second.snapshot_hash
    assert first.generation == second.generation == 1
    assert first.files[1].encoding == "ascii"
    assert first.files[2].language == "python"
    assert first.files[2].content_hash is not None
    assert all(not record.path.startswith("ignored/") for record in first.files)
    assert all(".env" not in record.path for record in first.files)


def test_inventory_cancellation_never_returns_a_complete_generation(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(20):
        (root / f"file-{index}.txt").write_text(str(index), encoding="utf-8")
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(InventoryCanceled):
        RepositoryInventoryBuilder(root).build(cancel=cancel)


def test_inventory_replaced_file_race_does_not_publish_stale_verified_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "main.py"
    target.write_bytes(b"a" * 200_000)
    replacement = root / "replacement.py"
    replacement.write_bytes(b"b" * 200_000)
    original_read_verified = inventory_module.read_verified_file
    replaced = False

    def replacing_read_verified(
        root_fd: int,
        relative: str,
        expected: os.stat_result,
        max_bytes: int,
    ) -> bytes:
        nonlocal replaced
        if not replaced:
            replaced = True
            replacement.replace(target)
        return original_read_verified(root_fd, relative, expected, max_bytes)

    monkeypatch.setattr(
        inventory_module, "read_verified_file", replacing_read_verified
    )
    inventory = RepositoryInventoryBuilder(root).build()
    record = next(item for item in inventory.files if item.path == "main.py")

    assert record.verification_state.value == "metadata_only"
    assert record.content_hash is None


def test_regular_ci_inventory_fixture_is_bounded_and_complete(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for index in range(1_000):
        (root / f"module-{index:04d}.py").write_text(
            f"VALUE = {index}\n", encoding="utf-8"
        )

    inventory = RepositoryInventoryBuilder(
        root, max_entries=1_100, max_scan_seconds=15.0
    ).build()

    assert inventory.complete is True
    assert len(inventory.files) == 1_000


def test_inventory_uses_nested_gitignore_and_eidos_hard_policy(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (root / ".eidosignore").write_text("*.snapshot\n", encoding="utf-8")
    (root / "src" / ".gitignore").write_text("generated.py\n", encoding="utf-8")
    (root / "src" / "main.py").write_text("main\n", encoding="utf-8")
    (root / "src" / "generated.py").write_text("generated\n", encoding="utf-8")
    (root / "ignored").mkdir()
    (root / "ignored" / "ignored.py").write_text("ignored\n", encoding="utf-8")
    (root / "result.snapshot").write_text("snapshot\n", encoding="utf-8")

    inventory = RepositoryInventoryBuilder(root).build()

    assert [record.path for record in inventory.files] == [
        ".eidosignore",
        ".gitignore",
        "src/.gitignore",
        "src/main.py",
    ]
