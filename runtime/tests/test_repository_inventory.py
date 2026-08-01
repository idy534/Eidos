from __future__ import annotations

from pathlib import Path
import threading

import pytest

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
