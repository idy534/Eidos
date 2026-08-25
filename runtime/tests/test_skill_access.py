from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.extensions.skill_access import (  # noqa: E402
    SkillAccess,
    SkillActivationKind,
)
from eidos_runtime.extensions.skills import (  # noqa: E402
    SkillCatalogEntry,
    SkillCatalogSnapshot,
)


class SkillAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-skill-access-")
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _snapshot(self, *names: str) -> SkillCatalogSnapshot:
        entries: list[SkillCatalogEntry] = []
        for name in names:
            skill = self.root / name
            (skill / "scripts").mkdir(parents=True)
            document = (
                f"---\nname: {name}\ndescription: {name} skill.\n---\n"
                f"Instructions for {name}.\n"
            ).encode("utf-8")
            (skill / "SKILL.md").write_bytes(document)
            (skill / "scripts" / "run.py").write_text(
                "print('skill')\n", encoding="utf-8"
            )
            entries.append(SkillCatalogEntry(
                qualified_id=f"user:{name}",
                name=name,
                description=f"{name} skill.",
                source_identity="eidos-user",
                source_version="local",
                source_hash="source-hash",
                content_hash=hashlib.sha256(document).hexdigest(),
                main_resource_locator=(skill / "SKILL.md").resolve().as_uri(),
            ))
        entries.sort(key=lambda entry: entry.qualified_id)
        snapshot = SkillCatalogSnapshot(
            catalog_hash="0" * 64,
            entries=tuple(entries),
        )
        return snapshot.model_copy(update={"catalog_hash": snapshot.canonical_hash()})

    def test_explicit_activation_uses_only_snapshot_locator_and_records_provenance(self) -> None:
        snapshot = self._snapshot("review")
        access = SkillAccess.from_snapshot(snapshot)

        record = access.activate_explicit("user:review")

        self.assertEqual(record.qualified_id, "user:review")
        self.assertEqual(record.canonical_root, (self.root / "review").resolve())
        self.assertEqual(record.activation_kind, SkillActivationKind.EXPLICIT)
        self.assertEqual(record.source, "eidos-user")
        self.assertEqual(record.provenance["version"], "local")
        self.assertEqual(access.active_roots(), (record.canonical_root,))

    def test_model_read_activation_has_a_separate_kind_and_unknown_id_is_rejected(self) -> None:
        access = SkillAccess.from_snapshot(self._snapshot("review"))

        record = access.activate_model_read("user:review")

        self.assertEqual(record.activation_kind, SkillActivationKind.MODEL_READ)
        with self.assertRaisesRegex(ValueError, "skill is not in the catalog"):
            access.activate_model_read("/tmp/arbitrary-root")

    def test_implicit_activation_matches_only_scripts_under_a_known_skill_root(self) -> None:
        snapshot = self._snapshot("review")
        access = SkillAccess.from_snapshot(snapshot)
        skill_script = self.root / "review" / "scripts" / "run.py"
        workspace_script = self.root / "workspace-run.py"
        workspace_script.write_text("print('workspace')\n", encoding="utf-8")

        record = access.activate_implicit("python3 scripts/run.py", self.root / "review")

        assert record is not None
        self.assertEqual(record.qualified_id, "user:review")
        self.assertEqual(record.activation_kind, SkillActivationKind.IMPLICIT)
        self.assertEqual(record.script_path, skill_script.resolve())
        self.assertIsNone(
            access.activate_implicit("python3 workspace-run.py", self.root)
        )

    def test_activation_snapshot_is_deterministic_and_thread_safe(self) -> None:
        access = SkillAccess.from_snapshot(self._snapshot("zeta", "alpha"))
        barrier = threading.Barrier(8)

        def activate() -> None:
            barrier.wait()
            access.activate_explicit("user:zeta")
            access.activate_model_read("user:alpha")

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _value: activate(), range(8)))

        records = access.records()
        self.assertEqual(
            tuple(record.qualified_id for record in records),
            ("user:alpha", "user:zeta"),
        )
        self.assertEqual(
            tuple(record.activation_kind for record in records),
            (SkillActivationKind.MODEL_READ, SkillActivationKind.EXPLICIT),
        )

    def test_invalid_snapshot_locator_cannot_create_an_active_root(self) -> None:
        snapshot = self._snapshot("review")
        entry = snapshot.entries[0].model_copy(
            update={"main_resource_locator": "skill://user:review/SKILL.md"}
        )
        invalid = snapshot.model_copy(
            update={"entries": (entry,)}
        )
        invalid = invalid.model_copy(update={"catalog_hash": invalid.canonical_hash()})
        access = SkillAccess.from_snapshot(invalid)

        with self.assertRaisesRegex(ValueError, "has no trusted filesystem root"):
            access.activate_explicit("user:review")


if __name__ == "__main__":
    unittest.main()
