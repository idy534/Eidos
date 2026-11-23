from __future__ import annotations

import hashlib
from pathlib import Path, PureWindowsPath
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
from eidos_runtime.infrastructure import local_files  # noqa: E402


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

    def test_implicit_activation_honors_skill_policy_but_explicit_activation_still_works(self) -> None:
        snapshot = self._snapshot("review")
        entry = snapshot.entries[0].model_copy(
            update={"allow_implicit_invocation": False}
        )
        snapshot = snapshot.model_copy(update={"entries": (entry,)})
        snapshot = snapshot.model_copy(update={"catalog_hash": snapshot.canonical_hash()})
        access = SkillAccess.from_snapshot(snapshot)

        self.assertIsNone(
            access.activate_implicit(
                "python3 scripts/run.py", self.root / "review"
            )
        )
        self.assertEqual(
            access.activate_explicit("user:review").activation_kind,
            SkillActivationKind.EXPLICIT,
        )
        invocation = access.activate_implicit(
            "python3 scripts/run.py", self.root / "review"
        )
        assert invocation is not None
        self.assertEqual(invocation.activation_kind, SkillActivationKind.EXPLICIT)
        self.assertEqual(invocation.script_path, (self.root / "review/scripts/run.py").resolve())

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
        document = (self.root / "review" / "SKILL.md").resolve()
        canonical = document.as_uri()
        for locator in (
            "skill://user:review/SKILL.md",
            str(document),
            canonical.replace("file:///", "file:/", 1),
            canonical.replace("/review/", "/review/../review/", 1),
        ):
            with self.subTest(locator=locator):
                entry = snapshot.entries[0].model_copy(
                    update={"main_resource_locator": locator}
                )
                invalid = snapshot.model_copy(
                    update={"entries": (entry,)}
                )
                invalid = invalid.model_copy(update={"catalog_hash": invalid.canonical_hash()})
                access = SkillAccess.from_snapshot(invalid)

                with self.assertRaisesRegex(ValueError, "has no trusted filesystem root"):
                    access.activate_explicit("user:review")

    def test_file_uri_conversion_handles_windows_and_posix_paths_strictly(self) -> None:
        win_path = PureWindowsPath(r"C:\Users\runner admin\Eidos 数据\skills\.system\docx\SKILL.md")
        self.assertEqual(
            PureWindowsPath(local_files._file_uri_path_text(win_path.as_uri(), windows=True)),
            win_path,
        )
        self.assertEqual(
            local_files._file_uri_path_text("file:///tmp/a%20b/%E4%B8%AD/SKILL.md", windows=False),
            "/tmp/a b/中/SKILL.md",
        )
        for bad_windows in (
            "file://server/share/SKILL.md",
            "file:////server/share/SKILL.md",
            "file:///relative/SKILL.md",
            "file:///C:relative/SKILL.md",
            "file:///C:/skills/SKILL.md?x=1",
            "file:///C:/skills/SKILL.md#frag",
            "file:///C:/skills%5C..%5CSKILL.md",
            "file:///C:/skills/%00/SKILL.md",
            "file:///C:/skills/%FF/SKILL.md",
            "file:/C:/skills/SKILL.md",
            "file:///C:/skills/../SKILL.md",
            "file:///C:/skills/./SKILL.md",
            "file:///C:/skills/SKILL.md:stream",
            "file:///C:/skills/x%3Astream/SKILL.md",
            "file:///C:/skills/a\tb/SKILL.md",
            "file:///C:/skills/a%2fb/SKILL.md",
            "skill://user:review/SKILL.md",
        ):
            with self.subTest(bad_windows=bad_windows):
                with self.assertRaises(ValueError):
                    local_files._file_uri_path_text(bad_windows, windows=True)
        for bad_posix in (
            "file://localhost/tmp/SKILL.md",
            "file:////tmp/SKILL.md",
            "file:///tmp/SKILL.md?x=1",
            "file:///tmp/SKILL.md#frag",
            "file:///tmp/%00/SKILL.md",
            "file:///tmp/%FF/SKILL.md",
            "file:/tmp/SKILL.md",
            "file:///tmp/../etc/SKILL.md",
            "file:///tmp/./SKILL.md",
            "file:///tmp/a\tb/SKILL.md",
            " file:///tmp/SKILL.md",
            "file:///tmp/a%2fb/SKILL.md",
            "https://example.com/SKILL.md",
        ):
            with self.subTest(bad_posix=bad_posix):
                with self.assertRaises(ValueError):
                    local_files._file_uri_path_text(bad_posix, windows=False)


if __name__ == "__main__":
    unittest.main()
