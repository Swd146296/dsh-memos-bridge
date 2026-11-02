"""Regression tests for scripts/patch_memos.py.

The fixtures are synthesised from the patch table itself: every patch's
pre-state snippet is written into a throwaway "MemOS checkout", so the tests
keep working when a snippet is edited (and fail loudly when one stops being
matched). No MemOS installation is required.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import patch_memos  # noqa: E402


def _fixture(root: Path) -> None:
    """Write a fake MemOS tree containing every patch's pre-state."""
    (root / "pyproject.toml").write_text('version = "2.0.30"\n', encoding="utf-8")
    contents: dict[str, list[str]] = {}
    for patch in patch_memos.PATCHES:
        contents.setdefault(patch.rel, []).append(patch.pre)
    for rel, blocks in contents.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.endswith(".bak")
    }


class PatchApplyTests(unittest.TestCase):
    def test_apply_is_idempotent_and_revert_restores(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            original = _snapshot(root)

            first = patch_memos.main(["--memos", str(root)])
            self.assertEqual(first, 0, "every patch should apply to a fresh fixture")
            applied = _snapshot(root)
            self.assertNotEqual(applied, original, "the fixture must actually change")

            for patch in patch_memos.PATCHES:
                text = (root / patch.rel).read_text(encoding="utf-8")
                if patch.kind == patch_memos.KIND_TOKENIZER:
                    self.assertIn(patch_memos.TOKENIZER_MARKER, text, patch.rel)
                else:
                    self.assertIn(patch.marker or patch.post, text, f"{patch.rel}: {patch.desc}")

            second = patch_memos.main(["--memos", str(root)])
            self.assertEqual(second, 0, "a second run must be a no-op, not an error")
            self.assertEqual(_snapshot(root), applied, "a second run must not rewrite files")

            self.assertEqual(patch_memos.main(["--memos", str(root), "--revert"]), 0)
            self.assertEqual(_snapshot(root), original, "--revert must restore the originals")

    def test_crlf_and_bom_are_preserved(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            target = root / "src/memos/api/config.py"
            original_text = target.read_text(encoding="utf-8")
            target.write_bytes(b"\xef\xbb\xbf" + original_text.replace("\n", "\r\n").encode("utf-8"))

            self.assertEqual(patch_memos.main(["--memos", str(root)]), 0)
            patched = target.read_bytes()
            self.assertTrue(patched.startswith(b"\xef\xbb\xbf"), "BOM must survive the patch")
            self.assertIn(b"\r\n", patched, "CRLF line endings must survive the patch")
            self.assertNotIn(b"\n\n\n", patched.replace(b"\r\n", b""), "no bare LF left behind")

    def test_missing_file_reports_every_gap_and_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            (root / "src/memos/log.py").unlink()
            (root / "src/memos/api/mcp_serve.py").unlink()
            before = _snapshot(root)

            self.assertEqual(patch_memos.main(["--memos", str(root)]), 1)
            self.assertEqual(_snapshot(root), before, "a failed pre-scan must not modify any file")

    def test_check_mode_writes_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            before = _snapshot(root)
            self.assertEqual(patch_memos.main(["--memos", str(root), "--check"]), 0)
            self.assertEqual(_snapshot(root), before)

    def test_unknown_layout_is_not_touched(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _fixture(root)
            # Simulate a MemOS version whose config.py no longer matches.
            target = root / "src/memos/api/config.py"
            target.write_bytes(b"# rewritten upstream\n")
            untouched = target.read_bytes()

            self.assertEqual(patch_memos.main(["--memos", str(root)]), 1)
            self.assertEqual(target.read_bytes(), untouched)


if __name__ == "__main__":
    unittest.main()
