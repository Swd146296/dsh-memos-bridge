#!/usr/bin/env python3
"""One-shot setup for the MemOS side of dsh-memos-bridge.

Creates a virtualenv, installs MemOS (with the extras the MCP server needs),
applies the required source patches (scripts/patch_memos.py), and downloads a
local gpt2 tokenizer.json (HuggingFace is unreachable in some networks).

Usage:
    python setup.py --memos C:\\path\\to\\MemOS [--venv C:\\path\\to\\.venv]
      [--skip-deps] [--skip-patch] [--skip-tokenizer] [--force-tokenizer]
      [--emit-env]

The venv python ends up at <venv>/Scripts/python.exe (Windows) or
<venv>/bin/python (POSIX); point MEMOS_PYTHON at it and MEMOS_HOME at the
MemOS checkout, then install the bundle:

    dsh plugin --profile web add ./dsh-memos-bridge

Exit codes: 0 = ready, 1 = a step failed (the failing command is echoed).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

PIP_EXTRAS = ["tree-mem"]
PIP_EXTRA_PACKAGES = ["python-dotenv", "tqdm", "langchain_text_splitters", "chonkie"]
TOKENIZER_URLS = [
    "https://hf-mirror.com/openai-community/gpt2/resolve/main/tokenizer.json",
    "https://huggingface.co/openai-community/gpt2/resolve/main/tokenizer.json",
]
TOKENIZER_MIN_BYTES = 1_000_000
MIN_PYTHON = (3, 10)


def _run(cmd: list[str], cwd: Path | None = None) -> bool:
    """Run a command, echoing it; return False (never raise) on failure."""
    printable = " ".join(str(part) for part in cmd)
    print(f"$ {printable}", flush=True)
    try:
        subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)
        return True
    except FileNotFoundError:
        print(f"ERROR: command not found: {cmd[0]}", file=sys.stderr)
        return False
    except subprocess.CalledProcessError as exc:
        print(
            f"ERROR: command failed with exit code {exc.returncode}: {printable}",
            file=sys.stderr,
        )
        return False
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return False


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def verify_tokenizer(path: Path, min_bytes: int = TOKENIZER_MIN_BYTES) -> bool:
    """True when the file looks like a real gpt2 tokenizer.json."""
    try:
        return path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def download(url: str, dest: Path, timeout: float = 60.0) -> None:
    """Download to a temp file, then move into place (no partial files)."""
    request = urllib.request.Request(url, headers={"User-Agent": "dsh-memos-bridge-setup"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        expected = response.headers.get("Content-Length")
        fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), suffix=".part")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = response.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
            size = tmp.stat().st_size
            if expected is not None and expected.isdigit() and size != int(expected):
                raise OSError(f"truncated download: {size} of {expected} bytes")
            if size == 0:
                raise OSError("downloaded file is empty")
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                tmp.unlink()


def _fetch_tokenizer(memos: Path, force: bool) -> bool:
    dest = memos / "local-tokenizers" / "gpt2" / "tokenizer.json"
    if not force and verify_tokenizer(dest):
        print(f"Tokenizer already present: {dest}")
        return True
    last_error: Exception | None = None
    for url in TOKENIZER_URLS:
        try:
            print(f"Downloading {url}")
            download(url, dest)
            if verify_tokenizer(dest):
                print(f"  saved {dest.stat().st_size} bytes to {dest}")
                return True
            print("  downloaded file looks too small; trying next mirror")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
            print(f"  failed ({exc}); trying next mirror")
    print(
        "WARNING: tokenizer download failed "
        f"({last_error}); the sentence chunker will need network access to "
        "HuggingFace at runtime. Set HF_ENDPOINT=https://hf-mirror.com as a "
        "fallback, or drop a gpt2 tokenizer.json at "
        f"{dest} and re-run with --skip-tokenizer.",
        file=sys.stderr,
    )
    return False


def _emit_env(py: Path, memos: Path) -> None:
    print()
    print("Environment for the Harness process:")
    if os.name == "nt":
        print(f'  setx MEMOS_PYTHON "{py}"')
        print(f'  setx MEMOS_HOME   "{memos}"')
        print("  (open a new terminal / restart the Harness afterwards)")
        print("  PowerShell, current session only:")
        print(f"    $env:MEMOS_PYTHON = '{py}'")
        print(f"    $env:MEMOS_HOME   = '{memos}'")
    else:
        print(f"  export MEMOS_PYTHON='{py}'")
        print(f"  export MEMOS_HOME='{memos}'")


def _check(py: Path, memos: Path) -> int:
    """Report whether the MemOS side is ready, changing nothing."""
    ok = True
    print(f"MemOS checkout : {memos}")
    print(f"venv python    : {py}")

    if not py.exists():
        print("  MISSING — run the setup without --skip-deps to create it.")
        return 1
    print("  present")

    probe = "from memos.api.mcp_serve import MOSMCPServer; print('mcp_serve ok')"
    if _run([str(py), "-c", probe], cwd=memos):
        print("import         : ok")
    else:
        print("import         : FAILED — re-run the setup without --skip-deps.")
        ok = False

    patch_script = Path(__file__).with_name("patch_memos.py")
    if not _run([str(py), str(patch_script), "--memos", str(memos), "--check"]):
        print("source patches : not all applicable (see above).")
        ok = False

    tokenizer = memos / "local-tokenizers" / "gpt2" / "tokenizer.json"
    if verify_tokenizer(tokenizer):
        print(f"tokenizer      : {tokenizer.stat().st_size} bytes")
    else:
        print(f"tokenizer      : MISSING — run the setup without --skip-tokenizer.")
        ok = False

    print()
    print("RESULT:", "ready" if ok else "not ready")
    if ok:
        print("Next: set MEMOS_PYTHON / MEMOS_HOME, then restart the Harness.")
        print("Deep check (Neo4j + gateways):")
        print(f'  "{py}" "{Path(__file__).with_name("memos_mcp_runner.py")}" --check')
    return 0 if ok else 1



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--memos", default=None, help="MemOS repository root (default: $MEMOS_HOME or ../MemOS)"
    )
    parser.add_argument("--venv", default=None, help="virtualenv directory (default: <memos>/.venv)")
    parser.add_argument("--skip-deps", action="store_true", help="skip venv creation and pip install")
    parser.add_argument("--skip-patch", action="store_true", help="skip the MemOS source patches")
    parser.add_argument("--skip-tokenizer", action="store_true", help="skip the tokenizer download")
    parser.add_argument(
        "--force-tokenizer", action="store_true", help="re-download the tokenizer even if present"
    )
    parser.add_argument(
        "--emit-env", action="store_true", help="print copy-pasteable MEMOS_* export lines at the end"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the MemOS side is ready; installs nothing",
    )
    args = parser.parse_args(argv)

    if sys.version_info < MIN_PYTHON:
        print(
            f"ERROR: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required to set up MemOS "
            f"(this is {sys.version.split()[0]}).",
            file=sys.stderr,
        )
        return 1

    try:
        memos = Path(args.memos or os.environ.get("MEMOS_HOME") or "../MemOS").expanduser().resolve()
    except OSError as exc:
        print(f"ERROR: cannot resolve the MemOS path: {exc}", file=sys.stderr)
        return 1
    if not (memos / "pyproject.toml").exists():
        print(
            f"ERROR: MemOS checkout not found at {memos} (pass --memos or set MEMOS_HOME). "
            "It must be the repository root that contains pyproject.toml.",
            file=sys.stderr,
        )
        return 1

    venv = Path(args.venv or memos / ".venv").expanduser().resolve()
    py = _venv_python(venv)
    failures: list[str] = []

    if args.check:
        return _check(py, memos)

    if not args.skip_deps:
        if not py.exists():
            print(f"Creating virtualenv at {venv}")
            if not _run([sys.executable, "-m", "venv", str(venv)]):
                print("ERROR: could not create the virtualenv.", file=sys.stderr)
                return 1
            # Fresh venvs carry an older pip; upgrade once at creation. On
            # re-runs the venv already exists, so we skip the upgrade: it would
            # otherwise hit the package index and fail on offline machines even
            # when the environment is already fully set up.
            _run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
        spec = ".[" + ",".join(PIP_EXTRAS) + "]"
        if not _run([str(py), "-m", "pip", "install", "-e", spec, *PIP_EXTRA_PACKAGES], cwd=memos):
            # An extra that moved/vanished upstream should not block the whole
            # install: the base package is what the MCP server needs.
            print("Retrying without the tree-mem extra...", file=sys.stderr)
            if not _run([str(py), "-m", "pip", "install", "-e", ".", *PIP_EXTRA_PACKAGES], cwd=memos):
                failures.append("pip install")
        if not _run([str(py), "-c", "import memos; print('memos ok')"], cwd=memos):
            failures.append("import memos")
        else:
            # `import memos` alone passes on a partially installed tree: the MCP
            # entrypoint pulls in the tree-memory extras, so probe it directly.
            if not _run(
                [str(py), "-c", "from memos.api.mcp_serve import MOSMCPServer; print('mcp_serve ok')"],
                cwd=memos,
            ):
                failures.append("import memos.api.mcp_serve")
    elif not py.exists():
        print(
            f"ERROR: --skip-deps was given but no virtualenv python exists at {py}.\n"
            "  Create it first (run without --skip-deps) or pass --venv <dir>.",
            file=sys.stderr,
        )
        return 1

    if not args.skip_patch:
        patch_script = Path(__file__).with_name("patch_memos.py")
        if not _run([str(py), str(patch_script), "--memos", str(memos)]):
            failures.append("MemOS source patches")

    if not args.skip_tokenizer:
        if not _fetch_tokenizer(memos, args.force_tokenizer):
            failures.append("tokenizer download")

    print()
    if failures:
        print(f"Setup finished with problems: {', '.join(failures)}", file=sys.stderr)
        print("Fix the step(s) above and re-run; already-done steps are skipped.", file=sys.stderr)
        return 1

    print("Setup complete.")
    print(f"  MEMOS_PYTHON={py}")
    print(f"  MEMOS_HOME={memos}")
    print("Add the bundle to a profile and boot (adjust the profile name):")
    print("  dsh plugin --profile web add ./dsh-memos-bridge")
    print("  dsh --profile web --dump-config   # expect an `id: memos-mcp` row")
    print("Verify the MCP surface any time:")
    print(f'  "{py}" scripts/smoke_test.py --memos "{memos}"')
    if args.emit_env:
        _emit_env(py, memos)
    return 0


if __name__ == "__main__":
    sys.exit(main())
