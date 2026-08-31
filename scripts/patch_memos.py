#!/usr/bin/env python3
"""Apply the MemOS source fixes required by dsh-memos-bridge.

The MemOS MCP server (`memos.api.mcp_serve`) has several issues that prevent
it from working as an MCP stdio child of DeepSeek Harness. This script applies
five small, idempotent source patches to a MemOS checkout:

  1. api/config.py        - load_dotenv(override=True) -> load_dotenv()
                           (otherwise ambient env overrides never stick, so a
                           host-run deployment cannot override .env endpoints)
  2. log.py               - console logging to stderr instead of stdout
                           (stdout is the MCP protocol channel)
  3. api/mcp_serve.py     - map EMBEDDING_DIMENSION into the default config
                           (Neo4j vector index must match the embedder dims)
  4. mem_os/utils/default_config.py
     a. env-aware embedder config honoring MOS_EMBEDDER_BACKEND / _API_BASE /
        _API_KEY / _MODEL / EMBEDDING_DIMENSION (mirrors APIConfig)
     b. default chunker tokenizer points at a LOCAL gpt2 tokenizer.json
        (HuggingFace downloads are blocked/unreachable in some networks)

Idempotency: a patch is skipped when its post-state marker is already present
in the file; it is applied only when the pre-state text is found. Anything
else fails loudly, which usually means the MemOS version differs from the one
this bundle was tested against (MemoryOS 2.0.30).

Usage:
    python patch_memos.py --memos /path/to/MemOS
    python patch_memos.py --list
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# (relative path, description, pre-state, post-state, marker, replace_all)
PATCHES: list[tuple[str, str, str, str, str, bool]] = []


def _p(rel: str, desc: str, pre: str, post: str, marker: str, replace_all: bool = False) -> None:
    PATCHES.append((rel, desc, pre, post, marker, replace_all))


# 1. api/config.py: stop overriding ambient env with .env values.
_p(
    "src/memos/api/config.py",
    "load_dotenv(override=True) -> load_dotenv()",
    "# Load environment variables\nload_dotenv(override=True)",
    "# Load environment variables. Ambient environment wins; .env fills gaps.\n"
    "# (dsh-memos-bridge patch: override=False so host-run env overrides stick)\n"
    "load_dotenv()",
    "load_dotenv()",
)

# 2. log.py: console logs must not pollute the MCP stdio channel.
_p(
    "src/memos/log.py",
    "console stream stdout -> stderr (import)",
    "from sys import stdout",
    "from sys import stderr",
    "from sys import stderr",
)
_p(
    "src/memos/log.py",
    "console stream stdout -> stderr (handler)",
    '"stream": stdout,',
    '"stream": stderr,',
    '"stream": stderr,',
)

# 3. mcp_serve.py: honor EMBEDDING_DIMENSION so the Neo4j vector index
#    dimension matches the configured embedder.
_p(
    "src/memos/api/mcp_serve.py",
    "map EMBEDDING_DIMENSION in env_mapping",
    '        "EMBEDDER_MODEL": "embedder_model",\n        "MOS_EMBEDDER_MODEL": "embedder_model",',
    '        "EMBEDDER_MODEL": "embedder_model",\n        "MOS_EMBEDDER_MODEL": "embedder_model",\n'
    '        "EMBEDDING_DIMENSION": "embedding_dimension",',
    '"EMBEDDING_DIMENSION": "embedding_dimension",',
)

# 4a. default_config.py: imports for os / Any.
_p(
    "src/memos/mem_os/utils/default_config.py",
    "import os and Any",
    "import logging\n\nfrom typing import Literal",
    "import logging\nimport os\n\nfrom typing import Any, Literal",
    "from typing import Any, Literal",
)

# 4b. default_config.py: env-aware embedder helper.
_env_embedder_helper = '''def _env_embedder_config(
    openai_api_key: str, openai_api_base: str, embedder_model: str
) -> dict[str, Any] | None:
    """Build the embedder config from MOS_EMBEDDER_* env vars when set.

    Mirrors APIConfig.get_embedder_config so the MCP/CLI default path honors
    the same deployment knobs as the API service. Returns None when
    MOS_EMBEDDER_BACKEND is unset, leaving the caller's default in place.
    """
    backend = os.getenv("MOS_EMBEDDER_BACKEND")
    if backend == "universal_api":
        return {
            "backend": "universal_api",
            "config": {
                "provider": os.getenv("MOS_EMBEDDER_PROVIDER", "openai"),
                "api_key": os.getenv("MOS_EMBEDDER_API_KEY", openai_api_key),
                "model_name_or_path": os.getenv("MOS_EMBEDDER_MODEL", embedder_model),
                "embedding_dims": int(os.getenv("EMBEDDING_DIMENSION", "1024")),
                "base_url": os.getenv("MOS_EMBEDDER_API_BASE", openai_api_base),
            },
        }
    if backend == "ollama":
        return {
            "backend": "ollama",
            "config": {
                "model_name_or_path": os.getenv("MOS_EMBEDDER_MODEL", embedder_model),
                "api_base": os.getenv("OLLAMA_API_BASE", "http://localhost:11434"),
            },
        }
    return None


'''
_p(
    "src/memos/mem_os/utils/default_config.py",
    "insert _env_embedder_config helper",
    "logger = logging.getLogger(__name__)\n\n\ndef get_default_config(",
    "logger = logging.getLogger(__name__)\n\n\n" + _env_embedder_helper + "def get_default_config(",
    "def _env_embedder_config(",
)

# 4c. default_config.py: env-aware embedder construction in BOTH
#     get_default_config and get_default_cube_config (identical blocks).
_embedder_pre = """    # Universal API embedder configuration (using OpenAI)
    embedder_config = {
        "backend": "universal_api",
        "config": {
            "provider": "openai",
            "api_key": openai_api_key,
            "model_name_or_path": kwargs.get("embedder_model", "text-embedding-3-large"),
            "base_url": openai_api_base,
        },
    }"""
_embedder_post = """    # Embedder configuration: env-driven when MOS_EMBEDDER_BACKEND is set,
    # otherwise a universal OpenAI-compatible API backed by the chat key/base.
    # (dsh-memos-bridge patch: honors MOS_EMBEDDER_API_BASE / _API_KEY / _MODEL)
    embedder_config = _env_embedder_config(
        openai_api_key,
        openai_api_base,
        kwargs.get("embedder_model", "text-embedding-3-large"),
    ) or {
        "backend": "universal_api",
        "config": {
            "provider": "openai",
            "api_key": openai_api_key,
            "model_name_or_path": kwargs.get("embedder_model", "text-embedding-3-large"),
            "base_url": openai_api_base,
        },
    }"""
_p(
    "src/memos/mem_os/utils/default_config.py",
    "env-aware embedder config (both builders)",
    _embedder_pre,
    _embedder_post,
    "embedder_config = _env_embedder_config(",
    replace_all=True,
)

# 4d. default_config.py: point the sentence-chunker tokenizer at a local
#     tokenizer.json (path-dependent replacement).
_p(
    "src/memos/mem_os/utils/default_config.py",
    "chunker tokenizer -> local gpt2 tokenizer.json",
    '"tokenizer_or_token_counter": "gpt2",',
    "",  # computed per MemOS path; see _apply_patch_file
    "local-tokenizers",
)

# 5. graph_dbs / retrieve modules: route stray debug prints to stderr so they
#    cannot corrupt the MCP stdio protocol channel during searches. The tree
#    memory search path prints raw Cypher queries (and BM25 hit lines) to
#    stdout; an MCP client sees them as garbage JSON-RPC.
_PRINT_REDIRECT = (
    "# dsh-memos-bridge: keep stray debug prints off the MCP stdio channel.\n"
    "import builtins as _dsh_builtins\n"
    "\n"
    "\n"
    "def print(*args, **kwargs):\n"
    '    kwargs.setdefault("file", __import__("sys").stderr)\n'
    "    _dsh_builtins.print(*args, **kwargs)\n"
)
for _rel in (
    "src/memos/graph_dbs/neo4j.py",
    "src/memos/graph_dbs/neo4j_community.py",
    "src/memos/memories/textual/tree_text_memory/retrieve/bm25_util.py",
):
    _p(
        _rel,
        "route stray prints to stderr",
        "logger = get_logger(__name__)",
        "logger = get_logger(__name__)\n\n" + _PRINT_REDIRECT,
        "def print(*args, **kwargs):",
    )
_p(
    "src/memos/memories/textual/tree_text_memory/retrieve/internet_retriever.py",
    "route stray print to stderr (no logger in module)",
    'print(f"Google search request failed: {e}")',
    'print(f"Google search request failed: {e}", file=__import__("sys").stderr)',
    'file=__import__("sys").stderr',
)


def _tokenizer_line(tokenizer_path: Path) -> str:
    return (
        '                        # dsh-memos-bridge: local gpt2 tokenizer (HuggingFace '
        "downloads may be blocked); download via scripts/setup.py\n"
        f'                        "tokenizer_or_token_counter": "{tokenizer_path.as_posix()}",'
    )


def _apply_patch_file(
    path: Path, desc: str, pre: str, post: str, marker: str, replace_all: bool, tokenizer_path: Path | None
) -> bool:
    """Apply one textual patch. Returns True when the file changed."""
    # Read raw bytes to detect the file's line-ending style, then normalize to
    # '\n' for matching. Writing with the original style keeps the patch to the
    # intended lines only: without this, text-mode write on Windows rewrites
    # the whole file as CRLF (MemOS sources are LF, so every line would diff).
    raw = path.read_bytes()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8").replace("\r\n", "\n")

    if marker in text:
        return False  # already applied

    if pre == '"tokenizer_or_token_counter": "gpt2",':
        post = _tokenizer_line(tokenizer_path)

    if pre not in text:
        raise SystemExit(
            f"FAILED: {path} is not in pre-patch state for: {desc}\n"
            f"  (MemOS version mismatch? This bundle is tested against MemoryOS 2.0.30.)"
        )
    if replace_all:
        new_text = text.replace(pre, post)
    else:
        new_text = text.replace(pre, post, 1)
    if new_text == text:
        raise SystemExit(f"FAILED: {path} did not change for: {desc}")
    path.write_text(new_text, encoding="utf-8", newline=eol)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memos", default=None, help="MemOS repository root (default: $MEMOS_HOME or ../MemOS)")
    parser.add_argument("--list", action="store_true", help="list the patches this script would apply")
    args = parser.parse_args()

    if args.list:
        for rel, desc, _pre, _post, _marker, _ra in PATCHES:
            print(f"{rel}: {desc}")
        return 0

    memos = Path(args.memos or os.environ.get("MEMOS_HOME") or "../MemOS").resolve()
    if not (memos / "pyproject.toml").exists():
        raise SystemExit(f"MemOS checkout not found at {memos} (pass --memos or set MEMOS_HOME)")

    tokenizer_path = memos / "local-tokenizers" / "gpt2" / "tokenizer.json"
    changed: list[str] = []
    unchanged: list[str] = []
    for rel, desc, pre, post, marker, replace_all in PATCHES:
        path = memos / rel
        if not path.exists():
            raise SystemExit(f"FAILED: {path} not found")
        if _apply_patch_file(path, desc, pre, post, marker, replace_all, tokenizer_path):
            changed.append(f"  {rel}: {desc}")
        else:
            unchanged.append(f"  {rel}: {desc} (already applied or no-op)")

    print(f"MemOS checkout: {memos}")
    print("Applied:")
    print("\n".join(changed) if changed else "  (none)")
    print("Skipped (already applied):")
    print("\n".join(unchanged) if unchanged else "  (none)")
    print("Next: run scripts/setup.py to create the venv and download the tokenizer, "
          "then configure MEMOS_PYTHON / MEMOS_HOME (see README).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
