#!/usr/bin/env python3
"""One-shot setup for the MemOS side of dsh-memos-bridge.

Creates a virtualenv, installs MemOS (with the extras the MCP server needs),
applies the required source patches (scripts/patch_memos.py), and downloads a
local gpt2 tokenizer.json (HuggingFace is unreachable in some networks).

Usage:
    python setup.py --memos C:\\path\\to\\MemOS [--venv C:\\path\\to\\.venv]
      [--skip-deps] [--skip-patch] [--skip-tokenizer]

The venv python ends up at <venv>/Scripts/python.exe (Windows) or
<venv>/bin/python (POSIX); point MEMOS_PYTHON at it and MEMOS_HOME at the
MemOS checkout, then install the bundle:

    dsh plugin --profile web add ./dsh-memos-bridge
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

PIP_EXTRAS = ["tree-mem"]
PIP_EXTRA_PACKAGES = ["python-dotenv", "tqdm", "langchain_text_splitters", "chonkie"]
TOKENIZER_URLS = [
    "https://hf-mirror.com/openai-community/gpt2/resolve/main/tokenizer.json",
    "https://huggingface.co/openai-community/gpt2/resolve/main/tokenizer.json",
]


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    print(f"$ {' '.join(str(c) for c in cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True)


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memos", default=None, help="MemOS repository root (default: $MEMOS_HOME or ../MemOS)")
    parser.add_argument("--venv", default=None, help="virtualenv directory (default: <memos>/.venv)")
    parser.add_argument("--skip-deps", action="store_true", help="skip venv creation and pip install")
    parser.add_argument("--skip-patch", action="store_true", help="skip the MemOS source patches")
    parser.add_argument("--skip-tokenizer", action="store_true", help="skip the tokenizer download")
    args = parser.parse_args()

    memos = Path(args.memos or os.environ.get("MEMOS_HOME") or "../MemOS").resolve()
    if not (memos / "pyproject.toml").exists():
        raise SystemExit(f"MemOS checkout not found at {memos} (pass --memos or set MEMOS_HOME)")
    venv = Path(args.venv or memos / ".venv").resolve()
    py = _venv_python(venv)

    if not args.skip_deps:
        if not py.exists():
            print(f"Creating virtualenv at {venv}")
            _run([sys.executable, "-m", "venv", str(venv)])
        _run([str(py), "-m", "pip", "install", "--upgrade", "pip"])
        spec = ".[" + ",".join(PIP_EXTRAS) + "]"
        _run([str(py), "-m", "pip", "install", "-e", spec, *PIP_EXTRA_PACKAGES], cwd=memos)

    if not args.skip_patch:
        _run([str(py), str(Path(__file__).with_name("patch_memos.py")), "--memos", str(memos)])

    if not args.skip_tokenizer:
        dest_dir = memos / "local-tokenizers" / "gpt2"
        dest = dest_dir / "tokenizer.json"
        if dest.exists() and dest.stat().st_size > 1_000_000:
            print(f"Tokenizer already present: {dest}")
        else:
            dest_dir.mkdir(parents=True, exist_ok=True)
            downloaded = False
            for url in TOKENIZER_URLS:
                try:
                    print(f"Downloading {url}")
                    urllib.request.urlretrieve(url, dest)
                    downloaded = True
                    break
                except Exception as e:  # noqa: BLE001
                    print(f"  failed ({e}); trying next mirror")
            if not downloaded:
                print("WARNING: tokenizer download failed; the sentence chunker will need "
                      "network access to HuggingFace at runtime.")
            elif dest.stat().st_size < 1_000_000:
                print("WARNING: downloaded tokenizer looks too small; verify it manually.")

    print()
    print("Setup complete.")
    print(f"  MEMOS_PYTHON={py}")
    print(f"  MEMOS_HOME={memos}")
    print("Add the bundle to a profile and boot (adjust the profile name):")
    print("  dsh plugin --profile web add ./dsh-memos-bridge")
    print("  dsh --profile web --dump-config   # expect an `id: memos-mcp` row")
    return 0


if __name__ == "__main__":
    sys.exit(main())
