#!/usr/bin/env python3
"""End-to-end verification of the MemOS MCP server over stdio.

Spawns the server exactly like @deepseek-ai/dsh-mcp-client does, performs the
MCP initialize handshake, lists the exposed tools, and optionally exercises a
memory write and a semantic search.

Usage:
    python smoke_test.py --python C:\\path\\to\\MemOS\\.venv\\Scripts\\python.exe \\
                         --memos C:\\path\\to\\MemOS [--search]

`add_memory` (plain content) works without any LLM/embedding gateway; the
`--search` probe additionally needs the MemOS LLM and embedding endpoints
reachable from this machine (see README "Host-run endpoint override").

Exit code 0 on success; non-zero on failure, counting the number of failed
probes (add_memory, and search_memories when --search is given).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def _probe(python: str, memos: str, do_search: bool) -> int:
    params = StdioServerParameters(
        command=python,
        args=["-m", "memos.api.mcp_serve"],
        cwd=memos,
    )
    failures = 0
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"TOOL_COUNT={len(names)}")
            print("  " + ", ".join(names))
            schemas = {t.name: t.inputSchema for t in tools.tools}

            add_args = {"memory_content": "dsh-memos-bridge smoke test memory.", "user_id": "default_user"}
            props = schemas.get("add_memory", {}).get("properties", {})
            add_args = {k: v for k, v in add_args.items() if k in props}
            try:
                res = await session.call_tool("add_memory", add_args)
                text = res.content[0].text if res.content else "<no content>"
                print(f"CALL add_memory -> {text[:200]}")
                if "success" not in text.lower():
                    failures += 1
            except Exception as e:  # noqa: BLE001
                print(f"CALL add_memory FAILED: {type(e).__name__}: {str(e)[:200]}")
                failures += 1

            if do_search:
                search_args = {"query": "dsh-memos-bridge smoke test", "user_id": "default_user"}
                props = schemas.get("search_memories", {}).get("properties", {})
                search_args = {k: v for k, v in search_args.items() if k in props}
                try:
                    res = await session.call_tool("search_memories", search_args)
                    text = res.content[0].text if res.content else "<no content>"
                    print(f"CALL search_memories -> {text[:200]}")
                    if "error" in text.lower():
                        failures += 1
                except Exception as e:  # noqa: BLE001
                    print(f"CALL search_memories FAILED: {type(e).__name__}: {str(e)[:200]}")
                    failures += 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="venv python that has memos installed")
    parser.add_argument("--memos", required=True, help="MemOS repository root (child cwd)")
    parser.add_argument("--search", action="store_true", help="also probe search_memories (needs LLM/embedding gateway)")
    args = parser.parse_args()
    return asyncio.run(_probe(args.python, args.memos, args.search))


if __name__ == "__main__":
    sys.exit(main())
