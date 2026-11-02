# dsh-memos-bridge

**English** | [简体中文](README.zh.md)

[![ci](https://github.com/Swd146296/dsh-memos-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/Swd146296/dsh-memos-bridge/actions/workflows/ci.yml)

A [DeepSeek Harness](https://github.com/deepseek-harness/deepseek-harness) **bundle** that bridges the [MemOS](https://github.com/MemTensor/MemOS) memory service into the agent over **MCP**. Install the bundle, run one setup script, restart the Harness, and the agent gains persistent-memory tools named `mcp__memos__*`.

## What you get

With the bundle active, the agent can call (a subset of the MemOS MCP surface):

| Tool | Purpose |
| --- | --- |
| `add_memory` | add a memory from text, a document, or conversation messages |
| `search_memories` | semantic search across the user's memory cubes |
| `get_memory` / `update_memory` / `delete_memory` | inspect / correct / remove single memories |
| `create_cube` / `register_cube` / `share_cube` | manage memory cubes |
| `chat` | memory-enhanced chat with the MOS system |
| `control_memory_scheduler` | start/stop the async memory scheduler |
| … | 16 tools total, listed by the smoke test |

## How it works

```
DeepSeek Harness (web profile)
  └─ cordis.patch.yml  ──inserts──►  @deepseek-ai/dsh-mcp-client  (ships with the dsh CLI)
                                        │  stdio
                                        ▼
                                scripts/memos_mcp_runner.py     (preflight + endpoint resolution)
                                        │
                                        ▼
                                python -m memos.api.mcp_serve   (MemOS venv)
                                        │
                                        ▼
                        MemOS MOS core: Neo4j (graph memory), Qdrant,
                        LLM + embedding gateway (e.g. Bailian-compatible)
```

The bundle contributes only a configuration layer (`dsh.bundle` + `cordis.patch.yml`); it mounts the stock `@deepseek-ai/dsh-mcp-client` plugin with a `stdio` server row. No Harness code is modified.

## Requirements

- `dsh` CLI installed (the bundle relies on its built-in `@deepseek-ai/dsh-mcp-client`).
- A MemOS checkout with its docker stack up (the compose in `MemOS/docker` provides Neo4j + Qdrant + the MemOS API).
- Python ≥ 3.10 for the MemOS virtualenv.
- The MemOS LLM and embedding gateway reachable from the machine that runs the MCP child (see [Endpoint resolution](#endpoint-resolution)).

## Install the bundle

Pick **one** of the three paths — they install the same files.

### A. From npm (recommended)

```sh
dsh plugin --profile web add dsh-memos-bridge
```

No `git` binary, no GitHub access, no build step. If the registry answers `404`, the package has not been published yet — use path B or C.

### B. From git

```sh
dsh plugin --profile web add github:Swd146296/dsh-memos-bridge
```

This runs `git ls-remote` under pnpm, so it needs **git on `PATH`** *and* a reachable github.com. Behind a restricted network this is the step that fails first:

```
Command failed with exit code 1: git ls-remote "https://github.com/Swd146296/dsh-memos-bridge.git" HEAD "HEAD^{}"
```

Check with `git --version`. If git is missing, install it; if github.com is unreachable, use path C.

### C. From a release tarball or a local checkout (no git)

```sh
# download the tarball attached to a release, then install it as a path
curl -LO https://github.com/Swd146296/dsh-memos-bridge/releases/download/v0.2.0/dsh-memos-bridge-0.2.0.tgz
dsh plugin --profile web add ./dsh-memos-bridge-0.2.0.tgz
```

Or build it yourself from a source copy:

```sh
npm pack                 # produces dsh-memos-bridge-<version>.tgz
dsh plugin --profile web add ./dsh-memos-bridge-<version>.tgz
```

> **Windows + spaces in the path:** pnpm splits a forwarded plugin path on spaces, which is how `dsh plugin add` ends up installing packages named `Gu` or similar. Use an 8.3 short path (`C:\Users\GULING~1\...`) or `add .` from a space-free directory.

## Quick start

**0. Start the MemOS infrastructure first** — the MCP server requires Neo4j at startup and search/chat need the LLM + embedding gateway:

```sh
docker compose -f C:\path\to\MemOS\docker\docker-compose.yml up -d   # Neo4j + Qdrant + MemOS API
# and make sure the LLM/embedding gateway (e.g. :18181/:18182) is running
```

If the stack is down when `dsh` starts, the runner prints one clear line and exits; the client's reconnect policy keeps retrying, so starting the stack later recovers automatically — no GUI restart.

**1. Set up the MemOS side** (venv + dependencies + source patches + local tokenizer):

```powershell
# from the plugin checkout
.\setup.ps1 --memos C:\path\to\MemOS
```

On POSIX: `./setup.sh --memos /path/to/MemOS`. This creates `MemOS/.venv`, installs `MemoryOS[tree-mem]` plus `python-dotenv`, `tqdm`, `langchain_text_splitters`, `chonkie`, applies the required source patches (see below), and downloads a local gpt2 tokenizer.json (HuggingFace mirror first).

**2. Install the bundle** — see [Install the bundle](#install-the-bundle).

**3. Configure paths** (the patch reads these at boot; both optional):

```powershell
setx MEMOS_PYTHON "C:\path\to\MemOS\.venv\Scripts\python.exe"
setx MEMOS_HOME   "C:\path\to\MemOS"
```

```sh
export MEMOS_PYTHON=/path/to/MemOS/.venv/bin/python
export MEMOS_HOME=/path/to/MemOS
```

When `MEMOS_PYTHON` is unset the row falls back to `python` on `PATH`; when `MEMOS_HOME` is unset the child runs in the Harness process cwd (MemOS still reads its `.env`, so point `MEMOS_HOME` at the checkout unless MemOS is the launch directory).

**4. Verify and restart:**

```sh
dsh --profile web --dump-config    # expect an `id: memos-mcp` row
dsh --profile web                  # restart the GUI; tools appear as mcp__memos__*
```

## Configuration

The bundle's patch inserts two rows: the `memos-bridge` provider (exposes `memosBridge` with the venv python, this package's runner path, the child cwd, and the tool namespace) and the `memos-mcp` `@deepseek-ai/dsh-mcp-client` row that spawns the runner over stdio. The runner **preflights** Neo4j (hard dependency: clear one-line error + fast exit), warns when the LLM/embedding gateway is down, and resolves host-run endpoints.

Environment knobs read at mount time:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MEMOS_PYTHON` | `python` | python of the MemOS venv |
| `MEMOS_HOME` | Harness cwd | MemOS checkout used as the child cwd |
| `MEMOS_MCP_SERVER` | `memos` | tool namespace (`mcp__<name>__*`), `[A-Za-z0-9_-]{1,32}` |

`cwd` is never empty: the provider resolves `MEMOS_HOME` and falls back to the Harness process cwd, because an empty cwd makes the MCP SDK spawn fail with a bare `ENOENT` that looks like a missing interpreter.

Defaults the row ships with: `toolCallTimeoutMs: 120000` (memory writes and LLM-backed search routinely exceed the 60 s client default), `failOnStartupError: false`, and `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` for the child (a Windows console codepage turns a non-ASCII log line into a `UnicodeEncodeError` inside the server).

To change any other field, override the row **by id** in your profile's `cordis.patch.yml` — later layers win, but an id-targeted patch replaces the whole `config`, so restate every key:

```yaml
- id: memos-mcp
  config:
    transport: stdio
    serverName: memos
    command: 'C:/path/to/MemOS/.venv/Scripts/python.exe'
    args: ['C:/path/to/dsh-memos-bridge/scripts/memos_mcp_runner.py']
    cwd: 'C:/path/to/MemOS'
    toolCallTimeoutMs: 180000
    env:
      PYTHONUTF8: '1'
      PYTHONIOENCODING: 'utf-8'
    failOnStartupError: false
```

## Endpoint resolution

MemOS's `.env` commonly targets `host.docker.internal:18181/18182` (valid *inside* the MemOS docker network). When the MCP child runs on the **host**, the runner resolves endpoints in this order:

| Priority | Source | Behaviour |
| --- | --- | --- |
| 1 | ambient environment (row `env` block, shell) | used verbatim, never rewritten |
| 2 | `.env` in `MEMOS_HOME` / cwd | kept as-is; a **docker-internal host** is mapped to the published loopback port, but only when that port answers |
| 3 | this bundle's default | `127.0.0.1:18181` / `:18182` for keys left entirely unset |

So a `.env` that already points at a real remote gateway (DashScope, DeepSeek, a company proxy) is left untouched, and an explicit `env` block always wins. To keep docker hosts verbatim (e.g. the gateway is only reachable through a local proxy rule), set `MEMOS_MCP_ENDPOINT_REWRITE=0` and override explicitly:

```yaml
- id: memos-mcp
  config:
    transport: stdio
    serverName: memos
    command: 'C:/path/to/MemOS/.venv/Scripts/python.exe'
    args: ['C:/path/to/dsh-memos-bridge/scripts/memos_mcp_runner.py']
    cwd: 'C:/path/to/MemOS'
    env:
      OPENAI_API_BASE: 'http://127.0.0.1:18181/v1'
      MOS_EMBEDDER_API_BASE: 'http://127.0.0.1:18182/compatible-mode/v1'
      MEMRADER_API_BASE: 'http://127.0.0.1:18181/v1'
      QWEN_API_BASE: 'http://127.0.0.1:18181/v1'
    failOnStartupError: false
```

See [`examples/host-run-override.yml`](examples/host-run-override.yml).

## Diagnostics

Three commands answer "why aren't the tools there?" — run them in this order:

```sh
# 1. Is the MemOS side ready? (venv, import, patches, tokenizer; changes nothing)
python scripts/setup.py --memos C:\path\to\MemOS --check

# 2. Is the infrastructure reachable? (endpoints, Neo4j, gateways, import)
"C:\path\to\MemOS\.venv\Scripts\python.exe" scripts/memos_mcp_runner.py --check

# 3. Does the MCP handshake work? (same entrypoint dsh spawns)
"C:\path\to\MemOS\.venv\Scripts\python.exe" scripts/smoke_test.py --memos C:\path\to\MemOS --search
```

Runner environment knobs:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MEMOS_MCP_PREFLIGHT` | `1` | `0` skips the Neo4j/gateway probes entirely (proxies that drop SYN) |
| `MEMOS_MCP_PREFLIGHT_TIMEOUT` | `2` | seconds per TCP probe |
| `MEMOS_MCP_ALLOW_NO_NEO4J` | `0` | `1` starts even when Neo4j is down, with a warning |
| `MEMOS_MCP_ENDPOINT_REWRITE` | `1` | `0` keeps docker-internal hosts verbatim |
| `MEMOS_MCP_DEBUG` | `0` | `1` echoes the resolved python, version and endpoints |

## MemOS source patches

`scripts/patch_memos.py` applies the following idempotent fixes to the MemOS checkout (tested against **MemoryOS 2.0.30**):

1. `src/memos/api/config.py` — `load_dotenv(override=True)` → `load_dotenv()`, so host-run env overrides are not clobbered.
2. `src/memos/log.py` — console handler to **stderr**; stdout is the MCP protocol channel and log lines there corrupt the stdio stream.
3. `src/memos/api/mcp_serve.py` — map `EMBEDDING_DIMENSION` into the default config so the Neo4j vector index matches the embedder dimension.
4. `src/memos/mem_os/utils/default_config.py`:
   - env-aware embedder construction honoring `MOS_EMBEDDER_BACKEND` / `MOS_EMBEDDER_API_BASE` / `MOS_EMBEDDER_API_KEY` / `MOS_EMBEDDER_MODEL` / `EMBEDDING_DIMENSION` (mirrors `APIConfig.get_embedder_config`; the MCP default path otherwise ignores them and reuses the chat endpoint);
   - the sentence-chunker tokenizer points at a **local** gpt2 `tokenizer.json` — chonkie otherwise downloads `gpt2` from huggingface.co, which is unreachable in some networks.
5. `src/memos/graph_dbs/neo4j.py`, `neo4j_community.py`, `tree_text_memory/retrieve/bm25_util.py`, `internet_retriever.py` — route stray debug `print()`s (raw Cypher queries, BM25 hit lines) to **stderr**; on stdout they corrupt the MCP stdio stream during searches.

Every patched file is backed up to `<file>.dsh-bridge.bak` before its first change, writes are atomic, and line endings/BOM are preserved.

```sh
python scripts/patch_memos.py --list     # what the script changes
python scripts/patch_memos.py --check    # dry run: which patches fit this checkout
python scripts/patch_memos.py --revert   # restore the backups
```

If a patch reports "not in pre-patch state", your MemOS version differs from 2.0.30 — `--check` shows every mismatch at once, so check the diff and apply it by hand.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `git ls-remote ... exit code 1` during `dsh plugin add` | git is not installed, or github.com is unreachable from that machine. Use the npm package or a release tarball ([path A/C](#install-the-bundle)). |
| `dsh plugin add` installs `Gu` / split packages | On Windows, a plugin path containing **spaces** is split when dsh forwards it to pnpm. Use the 8.3 short path or `add .` from a space-free directory. |
| MCP row never connects; `spawn` fails with `ENOENT` | Either `cwd` was empty (fixed in 0.2.0) or `MEMOS_PYTHON` does not point at a real interpreter. Run the runner `--check`. |
| Wall of `Couldn't connect to localhost:7687` tracebacks at startup | Neo4j is down. Start the MemOS docker stack first; the runner preflights Neo4j and prints one clear line, and the row keeps retrying. |
| Row stays pending after restart | `MEMOS_PYTHON`/`MEMOS_HOME` wrong, or the MemOS venv is missing. Check `dsh --profile web --dump-config`, then `setup.py --check`. |
| Tools appear, but search/chat fails with 401/403 | dsh scrubs credential-shaped names (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`) from a child's environment. Keep the key in MemOS's `.env`, or pass it explicitly in the row's `env` block. |
| `Graph not found: memosdefaultuser` at server start | Neo4j Community Edition + `MOS_NEO4J_SHARED_DB=false` in `.env` → set it to `true` and `NEO4J_AUTO_CREATE=false` (single shared `neo4j` database). |
| `Tokenizer 'gpt2' could not be loaded ... huggingface.co` | Run `setup.py` to download the local tokenizer, or set `HF_ENDPOINT=https://hf-mirror.com` (the patch already points the tokenizer at the local file). |
| `Embeddings request ended with error: Error code: 503` | The LLM/embedding gateway (e.g. `:18181`/`:18182`) is down or not reachable from the host — see [Endpoint resolution](#endpoint-resolution). |
| `Failed to parse JSONRPC message from server` while searching | Stray `print()` in the search path — re-run `patch_memos.py` (patch #5) and restart. |
| `UnicodeEncodeError` in the child's log | Console codepage is not UTF-8. The row already sets `PYTHONUTF8=1`; if you overrode the row, restate those two env keys. |
| `pydantic` serialization warnings at startup | Cosmetic; MemOS prints them when serializing config objects. |

When opening an issue, paste the output of `scripts/memos_mcp_runner.py --check` (it covers python, version, endpoints, Neo4j and gateways) plus your `dsh --dump-config` row.

## Security

The MCP server command runs as **trusted executable code outside the agent sandbox** (this is why the Harness enables no MCP server by default). Only connect to MemOS servers you run yourself.

Two consequences worth knowing:

- `patch_memos.py` edits a third-party checkout in place. Read `--list` before running it; `--revert` restores the backups.
- The Harness forwards a scrubbed environment to MCP children: names matching `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, and every `DSH_*` variable are dropped. Secrets belong in MemOS's own `.env` or in an explicit row `env` block.

## Development

```sh
node --check index.js                  # provider syntax
node --test tests/provider.test.mjs    # provider contract (cwd, namespace, fallbacks)
python -m unittest discover -s tests   # patch engine, patch layer, runner helpers
```

Both suites run offline and need no MemOS installation. CI runs them on Linux and Windows across Python 3.10/3.12, and also verifies that `npm pack` ships every file the bundle references.

## License

MIT
