#!/usr/bin/env python3
"""End-to-end verification of the MemOS MCP server over stdio.

Spawns the server exactly like @deepseek-ai/dsh-mcp-client does, performs the
MCP initialize handshake, lists the exposed tools, and optionally exercises a
memory write and a semantic search.

By default the child is ``scripts/memos_mcp_runner.py`` -- the same entrypoint
the bundle configures -- so the preflight, endpoint resolution and exit codes
that dsh relies on are covered too. Pass ``--direct`` to spawn
``python -m memos.api.mcp_serve`` instead and isolate MemOS itself.

Usage:
    python smoke_test.py --python C:\\path\\to\\MemOS\\.venv\\Scripts\\python.exe \\
                         --memos C:\\path\\to\\MemOS [--search]

    # with MEMOS_PYTHON / MEMOS_HOME exported, the flags are optional:
    python smoke_test.py --search

`add_memory` (plain content) works without any LLM/embedding gateway; the
`--search` probe additionally needs the MemOS LLM and embedding endpoints
reachable from this machine (see README "Host-run endpoint override").

Exit code 0 on success; non-zero on failure, counting the failed probes
(add_memory, and search_memories when --search is given). A tool surface that
is missing a required tool also counts as one failure.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

#: Tools the bundle's documentation promises. Missing ones are reported, but
#: only the two probed below are hard requirements for a passing run.
REQUIRED_TOOLS = ("add_memory", "search_memories")

RUNNER = Path(__file__).with_name("memos_mcp_runner.py")


def _venv_python(memos: Path) -> Path:
    return memos / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")


def _guess_memos() -> Path | None:
    """Locate a MemOS checkout without being told: env, cwd, then siblings."""
    candidates = [os.environ.get("MEMOS_HOME")]
    here = Path.cwd()
    candidates += [str(here / "MemOS"), str(here.parent / "MemOS")]
    for raw in candidates:
        if not raw:
            continue
        try:
            path = Path(raw).expanduser()
        except (OSError, ValueError):
            continue
        if (path / "pyproject.toml").is_file():
            return path
    return None


def _tool_args(schema: dict, defaults: dict) -> dict:
    """Keep only the arguments the server's own schema accepts."""
    props = (schema or {}).get("properties", {})
    return {k: v for k, v in defaults.items() if k in props} if props else defaults


async def _probe(python: str, memos: str, do_search: bool, timeout: float, direct: bool) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    if direct:
        command, args = python, ["-m", "memos.api.mcp_serve"]
        label = "memos.api.mcp_serve (direct)"
    else:
        if not RUNNER.is_file():
            print(f"FAIL: runner not found at {RUNNER}", file=sys.stderr)
            return 1
        command, args = python, [str(RUNNER)]
        label = "memos_mcp_runner.py (same entrypoint dsh uses)"

    print(f"server: {label}")
    print(f"python: {python}")
    print(f"cwd   : {memos}")

    params = StdioServerParameters(command=command, args=args, cwd=memos)
    failures = 0

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await asyncio.wait_for(session.initialize(), timeout=timeout)
            tools = await asyncio.wait_for(session.list_tools(), timeout=timeout)
            names = [t.name for t in tools.tools]
            print(f"TOOL_COUNT={len(names)}")
            print("  " + ", ".join(names))

            missing = [name for name in REQUIRED_TOOLS if name not in names]
            if missing:
                print(f"FAIL: missing required tool(s): {', '.join(missing)}")
                failures += 1

            schemas = {t.name: t.inputSchema for t in tools.tools}

            add_args = _tool_args(
                schemas.get("add_memory", {}),
                {
                    "memory_content": "dsh-memos-bridge smoke test memory.",
                    "user_id": "default_user",
                },
            )
            try:
                res = await asyncio.wait_for(
                    session.call_tool("add_memory", add_args), timeout=timeout
                )
                text = res.content[0].text if res.content else "<no content>"
                print(f"CALL add_memory -> {text[:200]}")
                if "success" not in text.lower():
                    failures += 1
            except Exception as err:  # noqa: BLE001 - a probe failure is data
                print(f"CALL add_memory FAILED: {type(err).__name__}: {str(err)[:200]}")
                failures += 1

            if do_search:
                search_args = _tool_args(
                    schemas.get("search_memories", {}),
                    {"query": "dsh-memos-bridge smoke test", "user_id": "default_user"},
                )
                try:
                    res = await asyncio.wait_for(
                        session.call_tool("search_memories", search_args), timeout=timeout
                    )
                    text = res.content[0].text if res.content else "<no content>"
                    print(f"CALL search_memories -> {text[:200]}")
                    if "error" in text.lower():
                        failures += 1
                except Exception as err:  # noqa: BLE001 - a probe failure is data
                    print(f"CALL search_memories FAILED: {type(err).__name__}: {str(err)[:200]}")
                    failures += 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python", default=os.environ.get("MEMOS_PYTHON"), help="venv python that has memos installed"
    )
    parser.add_argument("--memos", default=os.environ.get("MEMOS_HOME"), help="MemOS repository root (child cwd)")
    parser.add_argument(
        "--search", action="store_true", help="also probe search_memories (needs LLM/embedding gateway)"
    )
    parser.add_argument(
        "--direct", action="store_true", help="spawn `python -m memos.api.mcp_serve` instead of the runner"
    )
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="seconds per MCP operation (default 60)"
    )
    args = parser.parse_args()

    try:
        import mcp  # noqa: F401 - the SDK is a MemOS dependency, not a plugin one
    except ImportError:
        print(
            "FAIL: the python 'mcp' SDK is not installed in this interpreter.\n"
            "  Run the smoke test with the MemOS venv python:\n"
            '    <MemOS>/.venv/Scripts/python.exe scripts/smoke_test.py --memos <MemOS>',
            file=sys.stderr,
        )
        return 1

    memos = Path(args.memos).expanduser() if args.memos else _guess_memos()
    if memos is None or not (memos / "pyproject.toml").is_file():
        print(
            "FAIL: no MemOS checkout found.\n"
            "  Pass --memos <path> or set MEMOS_HOME; the directory must contain pyproject.toml.",
            file=sys.stderr,
        )
        return 1

    python = args.python or str(_venv_python(memos))
    if not Path(python).expanduser().is_file():
        print(f"FAIL: python not found at {python}.", file=sys.stderr)
        return 1

    try:
        failures = asyncio.run(_probe(python, str(memos), args.search, args.timeout, args.direct))
    except KeyboardInterrupt:
        return 130
    except Exception as err:  # noqa: BLE001 - e.g. the child died before initialize
        print(f"FAIL: could not complete the MCP session: {type(err).__name__}: {err}", file=sys.stderr)
        print(
            "  If the child exited first, its stderr above has the reason. "
            "`python scripts/memos_mcp_runner.py --check` prints a diagnosis.",
            file=sys.stderr,
        )
        return 1

    print("RESULT:", "PASS" if failures == 0 else f"FAIL ({failures} probe(s))")
    return failures


if __name__ == "__main__":
    sys.exit(main())
