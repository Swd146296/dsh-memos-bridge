#!/usr/bin/env python3
"""Apply the MemOS source fixes required by dsh-memos-bridge.

The MemOS MCP server (``memos.api.mcp_serve``) has several issues that prevent
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
  5. graph_dbs + tree-text retrieve modules - stray debug prints to stderr
     (raw Cypher / BM25 lines on stdout corrupt the MCP stdio stream)

Idempotency: a patch is skipped when its post-state marker is already present
in the file; it is applied only when the pre-state text is found. Anything else
is reported as "blocked", which usually means the MemOS version differs from
the one this bundle was tested against (MemoryOS 2.0.30).

Every file is backed up once, to ``<file>.dsh-bridge.bak``, before its first
change; ``--revert`` restores those backups.

Usage:
    python patch_memos.py --memos /path/to/MemOS
    python patch_memos.py --memos /path/to/MemOS --check    # dry run
    python patch_memos.py --memos /path/to/MemOS --revert   # restore backups
    python patch_memos.py --list

Exit codes: 0 = success (all patches applied / all in post-state), 1 = a patch
is blocked or a file is missing.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

BACKUP_SUFFIX = ".dsh-bridge.bak"
TOKENIZER_MARKER = "dsh-memos-bridge: local gpt2 tokenizer"

#: A patch is either plain text (``pre`` -> ``post``) or the special
#: ``tokenizer`` kind, which rewrites every ``"tokenizer_or_token_counter":
#: "gpt2"`` line in place, keeping the original indentation.
KIND_TEXT = "text"
KIND_TOKENIZER = "tokenizer"


@dataclass(frozen=True)
class Patch:
    rel: str
    desc: str
    pre: str = ""
    post: str = ""
    marker: str = ""
    replace_all: bool = False
    kind: str = KIND_TEXT

    def computed_post(self, tokenizer_path: Path | None) -> str:
        if self.kind != KIND_TOKENIZER:
            return self.post
        if tokenizer_path is None:  # pragma: no cover - guarded by callers
            raise ValueError("tokenizer path is required for the tokenizer patch")
        return f'"tokenizer_or_token_counter": "{tokenizer_path.as_posix()}",'


PATCHES: list[Patch] = []


def _p(
    rel: str,
    desc: str,
    pre: str = "",
    post: str = "",
    marker: str = "",
    replace_all: bool = False,
    kind: str = KIND_TEXT,
) -> None:
    PATCHES.append(Patch(rel, desc, pre, post, marker, replace_all, kind))


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
_ENV_EMBEDDER_HELPER = '''def _env_embedder_config(
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
    "logger = logging.getLogger(__name__)\n\n\n" + _ENV_EMBEDDER_HELPER + "def get_default_config(",
    "def _env_embedder_config(",
)

# 4c. default_config.py: env-aware embedder construction in BOTH
#     get_default_config and get_default_cube_config (identical blocks).
_EMBEDDER_PRE = """    # Universal API embedder configuration (using OpenAI)
    embedder_config = {
        "backend": "universal_api",
        "config": {
            "provider": "openai",
            "api_key": openai_api_key,
            "model_name_or_path": kwargs.get("embedder_model", "text-embedding-3-large"),
            "base_url": openai_api_base,
        },
    }"""
_EMBEDDER_POST = """    # Embedder configuration: env-driven when MOS_EMBEDDER_BACKEND is set,
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
    _EMBEDDER_PRE,
    _EMBEDDER_POST,
    "embedder_config = _env_embedder_config(",
    replace_all=True,
)

# 4d. default_config.py: point the sentence-chunker tokenizer at a local
#     tokenizer.json. Rewritten in place so indentation is preserved and a
#     moved/renamed MemOS checkout can be re-pointed by re-running the script.
_p(
    "src/memos/mem_os/utils/default_config.py",
    "chunker tokenizer -> local gpt2 tokenizer.json",
    '"tokenizer_or_token_counter": "gpt2",',
    "",
    TOKENIZER_MARKER,
    replace_all=True,
    kind=KIND_TOKENIZER,
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

# Matches the chunker tokenizer line as MemOS ships it, and as this script
# rewrites it (so a relocated checkout can be re-pointed).
_GPT2_LINE_RE = re.compile(
    r'^(?P<indent>[ \t]*)"tokenizer_or_token_counter":\s*"gpt2",[ \t]*$', re.MULTILINE
)
_OURS_LINE_RE = re.compile(
    r'^(?P<indent>[ \t]*)"tokenizer_or_token_counter":\s*"(?P<path>[^"]*)",[ \t]*$',
    re.MULTILINE,
)

# Status values.
APPLIED = "applied"
REFRESHED = "refreshed"
ALREADY = "already"
BLOCKED = "blocked"


def read_source(path: Path) -> tuple[str, str, bool]:
    """Read a source file as (text with \\n endings, eol, had_bom).

    Line endings are normalized for matching and restored on write, so a patch
    touches only the intended lines instead of re-encoding the whole file.
    """
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig" if bom else "utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit(
            f"FAILED: {path} is not valid UTF-8 ({exc.reason} at byte {exc.start}). "
            "Refusing to rewrite a source file this script cannot read back "
            "losslessly; check the file encoding or restore it from git."
        ) from exc
    eol = "\r\n" if b"\r\n" in raw else "\n"
    return text.replace("\r\n", "\n"), eol, bom


def write_source(path: Path, text: str, eol: str, bom: bool) -> None:
    """Replace ``path`` atomically, preserving its line endings and mode.

    A plain ``write_text`` truncates first: an interrupt, a full disk, or a
    locked file mid-write leaves a half-written module that MemOS can no longer
    import. Writing a sibling temp file and renaming it keeps the on-disk file
    either fully old or fully new.
    """
    tmp = path.with_name(path.name + ".dsh-bridge.tmp")
    try:
        with tmp.open("w", encoding="utf-8-sig" if bom else "utf-8", newline=eol) as handle:
            handle.write(text)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()



def _install_tokenizer(text: str, tokenizer_path: Path) -> tuple[str, int]:
    """Rewrite ``"tokenizer_or_token_counter": "gpt2"`` lines to the local file."""
    target = tokenizer_path.as_posix()

    def _repl(match: re.Match[str]) -> str:
        indent = match.group("indent")
        return (
            f"{indent}# {TOKENIZER_MARKER} (HuggingFace downloads may be blocked); "
            "written by scripts/setup.py\n"
            f'{indent}"tokenizer_or_token_counter": "{target}",'
        )

    return _GPT2_LINE_RE.subn(_repl, text)


def _refresh_tokenizer(text: str, tokenizer_path: Path) -> tuple[str, int]:
    """Re-point an already-patched tokenizer line at the current checkout.

    Only lines this script owns (i.e. still pointing into a
    ``local-tokenizers/gpt2/`` directory) are rewritten, so a deliberately
    customized tokenizer path is left alone.
    """
    if TOKENIZER_MARKER not in text:
        return text, 0
    target = tokenizer_path.as_posix()
    count = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal count
        current = match.group("path")
        if current == target or "local-tokenizers/gpt2/" not in current:
            return match.group(0)
        count += 1
        return f'{match.group("indent")}"tokenizer_or_token_counter": "{target}",'

    return _OURS_LINE_RE.sub(_repl, text), count


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + BACKUP_SUFFIX)


def apply_patch(
    path: Path,
    patch: Patch,
    tokenizer_path: Path | None,
    *,
    check_only: bool = False,
    make_backup: bool = True,
) -> tuple[str, str]:
    """Apply one patch to one file.

    Returns ``(status, detail)`` where status is one of APPLIED / REFRESHED /
    ALREADY / BLOCKED. With ``check_only`` nothing is written and APPLIED means
    "would apply".
    """
    text, eol, bom = read_source(path)
    original = text

    if patch.kind == KIND_TOKENIZER:
        if tokenizer_path is None:  # pragma: no cover - callers always pass one
            raise ValueError("tokenizer patch requires a tokenizer path")
        if TOKENIZER_MARKER in text:
            text, refreshed = _refresh_tokenizer(text, tokenizer_path)  # type: ignore[arg-type]
            status = REFRESHED if refreshed else ALREADY
            detail = f"{refreshed} tokenizer line(s) re-pointed" if refreshed else ""
        else:
            text, installed = _install_tokenizer(text, tokenizer_path)  # type: ignore[arg-type]
            if not installed:
                return BLOCKED, 'no `"tokenizer_or_token_counter": "gpt2"` line found'
            status, detail = APPLIED, f"{installed} tokenizer line(s) rewritten"
    else:
        if patch.marker and patch.marker in text:
            return ALREADY, ""
        if patch.pre not in text:
            return BLOCKED, "pre-patch state not found"
        new_text = (
            text.replace(patch.pre, patch.computed_post(tokenizer_path))
            if patch.replace_all
            else text.replace(patch.pre, patch.computed_post(tokenizer_path), 1)
        )
        if new_text == text:  # pragma: no cover - defensive
            return BLOCKED, "replacement produced no change"
        text, status, detail = new_text, APPLIED, ""

    if text == original:
        return ALREADY, detail
    if not check_only:
        if make_backup:
            dest = backup_path(path)
            if not dest.exists():
                shutil.copy2(path, dest)
        write_source(path, text, eol, bom)
    return status, detail


def resolve_memos(value: str | None) -> Path:
    memos = Path(value or os.environ.get("MEMOS_HOME") or "../MemOS").expanduser().resolve()
    if not (memos / "pyproject.toml").exists():
        raise SystemExit(
            f"MemOS checkout not found at {memos} (pass --memos or set MEMOS_HOME)"
        )
    return memos


def _files() -> list[str]:
    seen: list[str] = []
    for patch in PATCHES:
        if patch.rel not in seen:
            seen.append(patch.rel)
    return seen


def checkout_version(memos: Path) -> str:
    """Best-effort MemOS version of the checkout (pyproject, then installed dist)."""
    pyproject = memos / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    match = re.search(r'^\s*version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if match:
        return match.group(1)
    try:
        from importlib.metadata import PackageNotFoundError, version

        for dist in ("MemoryOS", "memos"):
            try:
                return version(dist)
            except PackageNotFoundError:
                continue
    except Exception:  # noqa: BLE001 - version lookup is advisory only
        pass
    return "unknown"



def revert(memos: Path) -> int:
    restored, skipped = [], []
    for rel in _files():
        dest = backup_path(memos / rel)
        if dest.exists():
            shutil.copy2(dest, memos / rel)
            dest.unlink()
            restored.append(f"  {rel}")
        else:
            skipped.append(f"  {rel} (no backup)")
    print(f"MemOS checkout: {memos}")
    print("Restored from backup:")
    print("\n".join(restored) if restored else "  (none)")
    if skipped:
        print("Left untouched:")
        print("\n".join(skipped))
    if restored:
        print("Patches reverted. Re-run without --revert to apply them again.")
    else:
        print("Nothing to revert: no .dsh-bridge.bak files were found.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--memos", default=None, help="MemOS repository root (default: $MEMOS_HOME or ../MemOS)"
    )
    parser.add_argument("--list", action="store_true", help="list the patches this script applies")
    parser.add_argument(
        "--check",
        action="store_true",
        help="dry run: report which patches would apply, write nothing",
    )
    parser.add_argument(
        "--revert", action="store_true", help="restore the .dsh-bridge.bak backups"
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="do not write .dsh-bridge.bak files (disables --revert)",
    )
    args = parser.parse_args(argv)

    if args.list:
        for patch in PATCHES:
            suffix = "  [dry-run aware]" if patch.kind == KIND_TOKENIZER else ""
            print(f"{patch.rel}: {patch.desc}{suffix}")
        return 0

    memos = resolve_memos(args.memos)
    if args.revert:
        return revert(memos)

    # Pre-scan before touching anything: failing on the first absent file hides
    # the other renames, and a halfway-applied patch set is the worst state to
    # debug. Report every problem at once, then stop.
    missing = [rel for rel in _files() if not (memos / rel).is_file()]
    if missing:
        print(f"MemOS checkout: {memos}", file=sys.stderr)
        print("FAILED: these expected source files are missing:", file=sys.stderr)
        for rel in missing:
            print(f"  {rel}", file=sys.stderr)
        print(
            "  Your MemOS tree differs from MemoryOS 2.0.30 (the version this "
            "bundle is tested against). Nothing was modified.",
            file=sys.stderr,
        )
        return 1

    version = checkout_version(memos)
    if not version.startswith("2.0."):
        print(
            f"WARNING: MemOS checkout reports version {version}; this bundle is "
            "tested against MemoryOS 2.0.30. Patches are text-anchored, so a "
            "mismatch usually surfaces as 'blocked' rather than as silent breakage.",
            file=sys.stderr,
        )

    tokenizer_path = memos / "local-tokenizers" / "gpt2" / "tokenizer.json"
    changed: list[str] = []
    unchanged: list[str] = []
    blocked: list[str] = []

    for patch in PATCHES:
        path = memos / patch.rel
        status, detail = apply_patch(
            path,
            patch,
            tokenizer_path,
            check_only=args.check,
            make_backup=not args.no_backup,
        )
        line = f"  {patch.rel}: {patch.desc}" + (f" ({detail})" if detail else "")
        if status == BLOCKED:
            blocked.append(line)
            if not args.check:
                # Fail fast: later patches may depend on earlier ones (the
                # embedder helper is defined by 4b and used by 4c), so a
                # partial apply that continues could leave broken source.
                print(f"MemOS checkout: {memos}", file=sys.stderr)
                if changed:
                    print(
                        "Applied before the failure (re-run --revert to undo):",
                        file=sys.stderr,
                    )
                    print("\n".join(changed), file=sys.stderr)
                print(
                    f"FAILED: {path} is not in pre-patch state for: {patch.desc}\n"
                    f"  {detail}\n"
                    "  MemOS version mismatch? This bundle is tested against MemoryOS 2.0.30.\n"
                    "  Run with --check to see every patch that does not fit, then apply the "
                    "diff by hand (see README 'MemOS source patches').",
                    file=sys.stderr,
                )
                return 1
        elif status in (APPLIED, REFRESHED):
            changed.append(line)
        else:
            unchanged.append(line)

    print(f"MemOS checkout: {memos}")
    print("Patches that would apply (dry run, nothing written):" if args.check else "Applied:")
    print("\n".join(changed) if changed else "  (none)")
    print("Skipped (already applied):")
    print("\n".join(unchanged) if unchanged else "  (none)")
    if args.check:
        if blocked:
            print("Blocked (MemOS version mismatch):")
            print("\n".join(blocked))
            return 1
        print("All patches fit this checkout.")
        return 0
    if changed:
        print("Backups: *.dsh-bridge.bak next to each patched file (undo with --revert).")
    print(
        "Next: run scripts/setup.py to create the venv and download the tokenizer, "
        "then configure MEMOS_PYTHON / MEMOS_HOME (see README)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
