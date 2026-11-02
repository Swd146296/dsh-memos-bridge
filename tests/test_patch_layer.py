"""Structural checks for cordis.patch.yml.

The bundle's whole contract with @deepseek-ai/dsh-mcp-client is this file: a
typo in a key name is silently dropped by schemastery, and an empty `cwd`
fails the child spawn. These assertions pin both.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATCH_FILE = ROOT / "cordis.patch.yml"

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs PyYAML
    yaml = None

#: Keys @deepseek-ai/dsh-mcp-client accepts for the stdio transport.
STDIO_KEYS = {
    "transport",
    "serverName",
    "command",
    "args",
    "env",
    "cwd",
    "toolCallTimeoutMs",
    "failOnStartupError",
    "reconnect",
}

#: Keys accepted inside the reconnect policy.
RECONNECT_KEYS = {"enabled", "initialDelayMs", "maxDelayMs", "maxAttempts"}


def _load() -> dict:
    class Loader(yaml.SafeLoader):  # type: ignore[misc]
        pass

    Loader.add_constructor("tag:yaml.org,2002:js", lambda loader, node: node.value)
    doc = yaml.load(PATCH_FILE.read_text(encoding="utf-8"), Loader=Loader)
    # The patch layer is a list of operations; this bundle contributes exactly
    # one `insert` op.
    if isinstance(doc, list):
        inserts = [op for op in doc if isinstance(op, dict) and "insert" in op]
        assert len(inserts) == 1, f"expected one insert op, found {len(inserts)}"
        return inserts[0]
    return doc


@unittest.skipIf(yaml is None, "PyYAML is not installed")
class PatchLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.doc = _load()

    def _rows(self) -> dict[str, dict]:
        self.assertIn("insert", self.doc, "the bundle must insert rows")
        return {row["id"]: row for row in self.doc["insert"]}

    def test_inserts_both_rows(self) -> None:
        rows = self._rows()
        self.assertEqual(set(rows), {"memos-bridge", "memos-mcp"})
        self.assertEqual(rows["memos-bridge"]["name"], "dsh-memos-bridge")
        self.assertEqual(rows["memos-mcp"]["name"], "@deepseek-ai/dsh-mcp-client")

    def test_mcp_row_only_uses_supported_keys(self) -> None:
        config = self._rows()["memos-mcp"]["config"]
        unknown = set(config) - STDIO_KEYS
        self.assertEqual(unknown, set(), f"unsupported mcp-client config key(s): {unknown}")
        self.assertEqual(set(config["reconnect"]), RECONNECT_KEYS)

    def test_cwd_comes_from_the_provider_not_a_literal(self) -> None:
        # An empty literal cwd makes spawn fail with a bare ENOENT; the provider
        # always yields a real directory instead.
        config = self._rows()["memos-mcp"]["config"]
        self.assertIn("memosBridge.cwd", str(config["cwd"]))
        self.assertNotEqual(str(config["cwd"]).strip(), "")

    def test_namespace_and_command_come_from_the_provider(self) -> None:
        config = self._rows()["memos-mcp"]["config"]
        self.assertIn("memosBridge.serverName", str(config["serverName"]))
        self.assertIn("memosBridge.python", str(config["command"]))
        self.assertIn("memosBridge.runner", " ".join(map(str, config["args"])))

    def test_injects_the_provider_service(self) -> None:
        self.assertEqual(self._rows()["memos-mcp"]["inject"], ["memosBridge"])

    def test_child_gets_a_utf8_environment(self) -> None:
        env = self._rows()["memos-mcp"]["config"]["env"]
        self.assertEqual(str(env["PYTHONUTF8"]), "1")
        self.assertEqual(str(env["PYTHONIOENCODING"]), "utf-8")


if __name__ == "__main__":
    unittest.main()
