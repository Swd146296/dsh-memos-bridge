# dsh-memos-bridge

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
                                python -m memos.api.mcp_serve   (MemOS venv)
                                        │
                                        ▼
                        MemOS MOS core: Neo4j (graph memory), Qdrant,
                        LLM + embedding gateway (e.g. Bailian-compatible)
```

The bundle contributes only a configuration layer (`dsh.bundle` + `cordis.patch.yml`); it mounts the stock `@deepseek-ai/dsh-mcp-client` plugin with a `stdio` server row. No Harness code is modified.

## Prerequisites

- `dsh` CLI installed (the bundle relies on its built-in `@deepseek-ai/dsh-mcp-client`).
- A MemOS checkout with its docker stack up (the compose in `MemOS/docker` provides Neo4j + Qdrant + the MemOS API).
- Python ≥ 3.10 for the MemOS virtualenv.
- The MemOS LLM and embedding gateway reachable from the machine that runs the MCP child (see [Host-run endpoint override](#host-run-endpoint-override)).

## Quick start

**0. Start the MemOS infrastructure first** — the MCP server requires Neo4j at startup and search/chat need the LLM + embedding gateway:

```sh
docker compose -f C:\path\to\MemOS\docker\docker-compose.yml up -d   # Neo4j + Qdrant + MemOS API
# and make sure the LLM/embedding gateway (e.g. :18181/:18182) is running
```

If the stack is down when `dsh` starts, the runner prints one clear line and exits; the client's reconnect policy keeps retrying (up to `maxAttempts`), so starting the stack later recovers automatically — no GUI restart.

**1. Set up the MemOS side** (venv + dependencies + source patches + local tokenizer):

```powershell
# from the plugin checkout
.\setup.ps1 --memos C:\path\to\MemOS
```

On POSIX: `./setup.sh --memos /path/to/MemOS`. This creates `MemOS/.venv`, installs `MemoryOS[tree-mem]` plus `python-dotenv`, `tqdm`, `langchain_text_splitters`, `chonkie`, applies the required source patches (see below), and downloads a local gpt2 tokenizer.json (HuggingFace mirror first).

**2. Install the bundle into a profile:**

```sh
dsh plugin --profile web add ./dsh-memos-bridge
```

**3. Configure paths** (the patch reads these at boot; all optional):

```sh
# PowerShell: setx MEMOS_PYTHON "C:\path\to\MemOS\.venv\Scripts\python.exe"
#             setx MEMOS_HOME   "C:\path\to\MemOS"
export MEMOS_PYTHON=/path/to/MemOS/.venv/bin/python
export MEMOS_HOME=/path/to/MemOS
```

When `MEMOS_PYTHON` is unset the row falls back to `python` on `PATH`; when `MEMOS_HOME` is unset the child inherits the Harness cwd (MemOS still reads its `.env`, so point `MEMOS_HOME` at the checkout unless MemOS is the launch directory).

**4. Verify and restart:**

```sh
dsh --profile web --dump-config    # expect an `id: memos-mcp` row
dsh --profile web                  # restart the GUI; tools appear as mcp__memos__*
```

Run the smoke test any time:

```sh
python scripts/smoke_test.py --python C:\path\to\MemOS\.venv\Scripts\python.exe --memos C:\path\to\MemOS --search
```

## Configuration

The bundle's patch inserts two rows: the `memos-bridge` provider (exposes `memosBridge` with the venv python, this package's runner path, and the MemOS cwd) and the `memos-mcp` `@deepseek-ai/dsh-mcp-client` row (`serverName: memos`) that spawns the runner over stdio. The runner **preflights** Neo4j (hard dependency: clear one-line error + fast exit), warns when the LLM/embedding gateway is down, and defaults the endpoints to `127.0.0.1` for host-run deployments.

Environment knobs read at mount time:

| Variable | Default | Meaning |
| --- | --- | --- |
| `MEMOS_PYTHON` | `python` | python of the MemOS venv |
| `MEMOS_HOME` | `''` (inherit cwd) | MemOS checkout used as the child cwd |
| `MEMOS_MCP_SERVER` | `memos` | tool namespace (`mcp__<name>__*`) |

To change any other field (e.g. `toolCallTimeoutMs`, `failOnStartupError`), override the row **by id** in your profile's `cordis.patch.yml` — later layers win, but an id-targeted patch replaces the whole `config`, so restate every key:

```yaml
- id: memos-mcp
  config:
    transport: stdio
    serverName: memos
    command: 'C:/path/to/MemOS/.venv/Scripts/python.exe'
    args: ['C:/path/to/dsh-memos-bridge/scripts/memos_mcp_runner.py']
    cwd: 'C:/path/to/MemOS'
    toolCallTimeoutMs: 60000
    failOnStartupError: false
```

## Host-run endpoint override

MemOS's `.env` commonly targets `host.docker.internal:18181/18182` (valid *inside* the MemOS docker network). When the MCP child runs on the **host**, those endpoints must be reachable from the host. The shipped runner defaults the endpoints to `127.0.0.1` (the usual host-loopback publishing); if your gateway lives elsewhere (or is reachable only through a local proxy rule), override the endpoints with an `env` block on the row:

```yaml
- id: memos-mcp
  config:
    transport: stdio
    serverName: memos
    command: 'C:/path/to/MemOS/.venv/Scripts/python.exe'
    args: ['-m', 'memos.api.mcp_serve']
    cwd: 'C:/path/to/MemOS'
    args: ['C:/path/to/dsh-memos-bridge/scripts/memos_mcp_runner.py']
    env:
      OPENAI_API_BASE: 'http://127.0.0.1:18181/v1'
      MOS_EMBEDDER_API_BASE: 'http://127.0.0.1:18182/compatible-mode/v1'
      MEMRADER_API_BASE: 'http://127.0.0.1:18181/v1'
      QWEN_API_BASE: 'http://127.0.0.1:18181/v1'
    failOnStartupError: false
```

(These values only stick because the patch script changes MemOS's `load_dotenv(override=True)` to `override=False` — ambient env then wins over `.env`.)

## MemOS source patches

`scripts/patch_memos.py` applies the following idempotent fixes to the MemOS checkout (tested against **MemoryOS 2.0.30**):

1. `src/memos/api/config.py` — `load_dotenv(override=True)` → `load_dotenv()`, so host-run env overrides are not clobbered.
2. `src/memos/log.py` — console handler to **stderr**; stdout is the MCP protocol channel and log lines there corrupt the stdio stream.
3. `src/memos/api/mcp_serve.py` — map `EMBEDDING_DIMENSION` into the default config so the Neo4j vector index matches the embedder dimension.
4. `src/memos/mem_os/utils/default_config.py`:
   - env-aware embedder construction honoring `MOS_EMBEDDER_BACKEND` / `MOS_EMBEDDER_API_BASE` / `MOS_EMBEDDER_API_KEY` / `MOS_EMBEDDER_MODEL` / `EMBEDDING_DIMENSION` (mirrors `APIConfig.get_embedder_config`; the MCP default path otherwise ignores them and reuses the chat endpoint);
   - the sentence-chunker tokenizer points at a **local** gpt2 `tokenizer.json` — chonkie otherwise downloads `gpt2` from huggingface.co, which is unreachable in some networks.
5. `src/memos/graph_dbs/neo4j.py`, `neo4j_community.py`, `tree_text_memory/retrieve/bm25_util.py`, `internet_retriever.py` — route stray debug `print()`s (raw Cypher queries, BM25 hit lines) to **stderr**; on stdout they corrupt the MCP stdio stream during searches.

Run `python scripts/patch_memos.py --list` to see the patch list. If a patch fails with "not in pre-patch state", your MemOS version differs from 2.0.30 — check the diff and re-apply by hand.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `dsh plugin add` installs `Gu` / split packages | On Windows, a plugin path containing **spaces** is split when dsh forwards it to pnpm. Use the 8.3 short path (e.g. `C:\Users\GULING~1\...`) or `add .` from a space-free directory. |
| Wall of `Couldn't connect to localhost:7687` tracebacks at startup | Neo4j is down. Start the MemOS docker stack first (`docker compose up -d`); the runner preflights Neo4j and prints one clear line, and the row keeps retrying — once the stack is up the tools appear without a GUI restart. |
| Row stays pending after restart | `MEMOS_PYTHON`/`MEMOS_HOME` wrong, or MemOS venv missing. Check `dsh --profile web --dump-config`. |
| `Graph not found: memosdefaultuser` at server start | Neo4j Community Edition + `MOS_NEO4J_SHARED_DB=false` in `.env` → set it to `true` and `NEO4J_AUTO_CREATE=false` (single shared `neo4j` database). |
| `Tokenizer 'gpt2' could not be loaded ... huggingface.co` | Run `setup.py` to download the local tokenizer, or set `HF_ENDPOINT=https://hf-mirror.com` (the patch script's tokenizer line already points at the local file). |
| `Embeddings request ended with error: Error code: 503` | The LLM/embedding gateway (e.g. `:18181`/`:18182`) is down or not reachable from the host — see [Host-run endpoint override](#host-run-endpoint-override) and start the gateway. |
| `Failed to parse JSONRPC message from server` while searching | Stray `print()` in the search path — re-run `patch_memos.py` (patch #5) and restart. |
| `pydantic` serialization warnings at startup | Cosmetic; MemOS prints them when serializing config objects. |

## Security

The MCP server command runs as **trusted executable code outside the agent sandbox** (this is why the Harness enables no MCP server by default). Only connect to MemOS servers you run yourself.

## License

MIT
