"""Regression tests for the pure helpers in scripts/memos_mcp_runner.py.

Importing the runner is side-effect free (MemOS is only imported inside
`main`), so these run on any Python 3.10+ without a MemOS installation.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import memos_mcp_runner as runner  # noqa: E402


class HostPortTests(unittest.TestCase):
    def test_scheme_uri(self) -> None:
        self.assertEqual(runner._host_port("bolt://localhost:7687"), ("localhost", 7687))

    def test_bare_host_port(self) -> None:
        self.assertEqual(runner._host_port("localhost:7687"), ("localhost", 7687))

    def test_default_port_when_omitted(self) -> None:
        self.assertEqual(runner._host_port("bolt://neo4j", 7687), ("neo4j", 7687))

    def test_scheme_default_when_no_default_given(self) -> None:
        self.assertEqual(runner._host_port("https://api.example.com/v1"), ("api.example.com", 443))

    def test_ipv6_literal(self) -> None:
        self.assertEqual(runner._host_port("http://[::1]:8080/pat"), ("::1", 8080))

    def test_credentials_are_stripped(self) -> None:
        self.assertEqual(
            runner._host_port("bolt://user:secret@db.internal:7687"), ("db.internal", 7687)
        )

    def test_garbage_returns_none_instead_of_raising(self) -> None:
        for value in ("", "   ", "bolt://host:99999", "http://", "://nohost", "localhost:abc"):
            with self.subTest(value=value):
                self.assertIsNone(runner._host_port(value, None))

    def test_out_of_range_port_rejected(self) -> None:
        self.assertIsNone(runner._host_port("bolt://host:0"))
        self.assertIsNone(runner._host_port("bolt://host:70000"))


class LoopbackRewriteTests(unittest.TestCase):
    def test_docker_internal_is_mapped_to_loopback(self) -> None:
        self.assertEqual(
            runner._to_loopback("http://host.docker.internal:18181/v1"),
            "http://127.0.0.1:18181/v1",
        )

    def test_gateway_alias_and_path_are_kept(self) -> None:
        self.assertEqual(
            runner._to_loopback("http://gateway.docker.internal:18182/compatible-mode/v1"),
            "http://127.0.0.1:18182/compatible-mode/v1",
        )

    def test_real_remote_endpoint_is_left_alone(self) -> None:
        for value in (
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "http://127.0.0.1:18181/v1",
            "http://192.168.1.10:8000/v1",
        ):
            with self.subTest(value=value):
                self.assertIsNone(runner._to_loopback(value))


class EnabledTests(unittest.TestCase):
    def test_falsey_values(self) -> None:
        import os

        for raw in ("0", "off", "false", "NO", " none ", ""):
            with self.subTest(raw=raw):
                os.environ["MEMOS_TEST_FLAG"] = raw
                self.assertFalse(runner._enabled("MEMOS_TEST_FLAG"))
        os.environ["MEMOS_TEST_FLAG"] = "1"
        self.assertTrue(runner._enabled("MEMOS_TEST_FLAG"))
        del os.environ["MEMOS_TEST_FLAG"]
        self.assertTrue(runner._enabled("MEMOS_TEST_FLAG", default=True))
        self.assertFalse(runner._enabled("MEMOS_TEST_FLAG", default=False))


if __name__ == "__main__":
    unittest.main()
