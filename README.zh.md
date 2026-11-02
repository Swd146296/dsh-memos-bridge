# dsh-memos-bridge

[English](README.md) | **简体中文**

一个 [DeepSeek Harness](https://github.com/deepseek-harness/deepseek-harness) **组合包（bundle）**，通过 **MCP** 把 [MemOS](https://github.com/MemTensor/MemOS) 记忆服务桥接进 agent。安装组合包、跑一次设置脚本、重启 Harness，agent 就获得了名为 `mcp__memos__*` 的持久记忆工具。

## 能获得什么

组合包激活后，agent 可以调用（MemOS MCP 工具面的子集）：

| 工具 | 用途 |
| --- | --- |
| `add_memory` | 从文本、文档或对话消息写入记忆 |
| `search_memories` | 跨用户记忆 Cube 做语义检索 |
| `get_memory` / `update_memory` / `delete_memory` | 查看 / 修正 / 删除单条记忆 |
| `create_cube` / `register_cube` / `share_cube` | 管理记忆 Cube |
| `chat` | 带记忆增强的 MOS 对话 |
| `control_memory_scheduler` | 启停异步记忆调度器 |
| … | 共 16 个工具，可用冒烟测试列出 |

## 工作原理

```
DeepSeek Harness（web profile）
  └─ cordis.patch.yml  ──插入──►  @deepseek-ai/dsh-mcp-client  （dsh CLI 自带）
                                        │  stdio
                                        ▼
                                scripts/memos_mcp_runner.py     （预检 + 端点解析）
                                        │
                                        ▼
                                python -m memos.api.mcp_serve   （MemOS venv）
                                        │
                                        ▼
                        MemOS MOS 核心：Neo4j（图记忆）、Qdrant、
                        LLM + Embedding 网关（如百炼兼容代理）
```

组合包只贡献一层配置（`dsh.bundle` + `cordis.patch.yml`），挂载的是 Harness 自带的 `@deepseek-ai/dsh-mcp-client` 插件（stdio 服务行）。不改动 Harness 任何代码。

## 前置条件

- 已安装 `dsh` CLI（组合包依赖其内置的 `@deepseek-ai/dsh-mcp-client`）。
- MemOS 源码目录及其 docker 栈在运行（`MemOS/docker` 的 compose 提供 Neo4j + Qdrant + MemOS API）。
- Python ≥ 3.10（用于 MemOS venv）。
- 运行 MCP 子进程的机器能访问 MemOS 的 LLM 与 Embedding 网关（见[端点解析](#端点解析)）。

## 安装组合包

下面三条路径**任选其一**，装出来的文件完全一样。

### A. 从 npm 安装（推荐）

```sh
dsh plugin --profile web add dsh-memos-bridge
```

不需要 `git`、不访问 GitHub、没有构建步骤。如果 registry 返回 `404`，说明包还没发布，请改用 B 或 C。

### B. 从 git 安装

```sh
dsh plugin --profile web add github:Swd146296/dsh-memos-bridge
```

pnpm 内部会执行 `git ls-remote`，因此既要求 **`PATH` 上有 git**，也要求 github.com 可达。受限网络下这一环最先失败：

```
Command failed with exit code 1: git ls-remote "https://github.com/Swd146296/dsh-memos-bridge.git" HEAD "HEAD^{}"
```

先用 `git --version` 确认 git 存在；如果 git 缺失就装上，如果 github.com 不可达就用路径 C。

### C. 从 release 压缩包或本地目录安装（不需要 git）

```sh
# 下载某个 release 附带的 tgz，再按路径安装
curl -LO https://github.com/Swd146296/dsh-memos-bridge/releases/download/v0.2.0/dsh-memos-bridge-0.2.0.tgz
dsh plugin --profile web add ./dsh-memos-bridge-0.2.0.tgz
```

也可以从源码副本自行打包：

```sh
npm pack                 # 生成 dsh-memos-bridge-<version>.tgz
dsh plugin --profile web add ./dsh-memos-bridge-<version>.tgz
```

> **Windows 路径含空格：** pnpm 会把转发过去的插件路径按空格切开，于是 `dsh plugin add` 会装出名为 `Gu` 之类的拆分包。改用 8.3 短路径（如 `C:\Users\GULING~1\...`），或从无空格目录执行 `add .`。

## 快速开始

**0. 先启动 MemOS 基础设施**——MCP server 启动时必须连 Neo4j，search/chat 还需要 LLM 与 Embedding 网关：

```sh
docker compose -f C:\path\to\MemOS\docker\docker-compose.yml up -d   # Neo4j + Qdrant + MemOS API
# 并确保 LLM/Embedding 网关（如 :18181/:18182）在运行
```

如果 `dsh` 启动时栈还没起来，runner 会打印一行清晰的提示并快速退出；客户端的重连策略会持续重试，之后把栈启动起来即可自动恢复，**无需重启 GUI**。

**1. 准备 MemOS 侧**（venv + 依赖 + 源码补丁 + 本地 tokenizer）：

```powershell
# 在插件目录下
.\setup.ps1 --memos C:\path\to\MemOS
```

POSIX：`./setup.sh --memos /path/to/MemOS`。脚本会创建 `MemOS/.venv`，安装 `MemoryOS[tree-mem]` 及 `python-dotenv`、`tqdm`、`langchain_text_splitters`、`chonkie`，应用必需的源码补丁（见下文），并下载本地 gpt2 tokenizer.json（优先 HuggingFace 镜像）。

**2. 安装组合包**——见[安装组合包](#安装组合包)。

**3. 配置路径**（patch 在启动时读取，均可选）：

```powershell
setx MEMOS_PYTHON "C:\path\to\MemOS\.venv\Scripts\python.exe"
setx MEMOS_HOME   "C:\path\to\MemOS"
```

```sh
export MEMOS_PYTHON=/path/to/MemOS/.venv/bin/python
export MEMOS_HOME=/path/to/MemOS
```

不设置 `MEMOS_PYTHON` 时回退到 `PATH` 上的 `python`；不设置 `MEMOS_HOME` 时子进程在 Harness 进程的工作目录下运行（MemOS 仍会读取自己的 `.env`，建议把 `MEMOS_HOME` 指向源码目录，除非 MemOS 就在启动目录下）。

**4. 验证并重启：**

```sh
dsh --profile web --dump-config    # 应看到 `id: memos-mcp` 行
dsh --profile web                  # 重启 GUI，工具以 mcp__memos__* 出现
```

## 配置

组合包的 patch 插入两行：`memos-bridge` 提供方（通过 `memosBridge` 服务暴露 venv python、本包 runner 路径、子进程 cwd 和工具命名空间）和 `memos-mcp`（`@deepseek-ai/dsh-mcp-client` 行，经 stdio 拉起 runner）。runner 会**预检** Neo4j（硬依赖：一行清晰报错并快速退出）、在 LLM/Embedding 网关不可用时给出警告，并解析宿主机部署的端点。

挂载时读取的环境变量：

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `MEMOS_PYTHON` | `python` | MemOS venv 的 python |
| `MEMOS_HOME` | Harness 的工作目录 | 作为子进程 cwd 的 MemOS 目录 |
| `MEMOS_MCP_SERVER` | `memos` | 工具命名空间（`mcp__<name>__*`），需匹配 `[A-Za-z0-9_-]{1,32}` |

`cwd` 永不为空：提供方先解析 `MEMOS_HOME`，取不到就回退到 Harness 进程的工作目录——空 cwd 会让 MCP SDK 的 spawn 直接失败并抛出一个裸 `ENOENT`，看起来像是解释器不存在。

该行自带的默认值：`toolCallTimeoutMs: 120000`（写记忆和带 LLM 的检索经常超过客户端默认的 60 秒）、`failOnStartupError: false`，以及给子进程的 `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`（Windows 控制台代码页会把一行非 ASCII 日志变成 server 内部的 `UnicodeEncodeError`）。

想改其他字段，在你的 profile `cordis.patch.yml` 中**按 id 覆盖**该行——后层优先，但按 id 的 patch 会整体替换 `config`，需要重述所有键：

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

## 端点解析

MemOS 的 `.env` 通常指向 `host.docker.internal:18181/18182`（在 MemOS 的 docker 网络*内部*有效）。当 MCP 子进程跑在**宿主机**上时，runner 按下面的顺序解析端点：

| 优先级 | 来源 | 行为 |
| --- | --- | --- |
| 1 | 外层环境变量（行的 `env` 块、shell） | 原样使用，绝不改写 |
| 2 | `MEMOS_HOME` / cwd 下的 `.env` | 原样保留；仅当 **docker 内部主机名**对应的发布端口真能连上时，才映射到回环地址 |
| 3 | 本组合包的默认值 | 完全未设置的键才填 `127.0.0.1:18181` / `:18182` |

因此，`.env` 里已经指向真实远端网关（百炼、DeepSeek、公司代理等）的配置**不会被改动**，显式 `env` 块也永远优先。若要让 docker 主机名原样保留（例如网关只能经本地代理规则到达），设 `MEMOS_MCP_ENDPOINT_REWRITE=0` 并显式覆盖：

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

参见 [`examples/host-run-override.yml`](examples/host-run-override.yml)。

## 排障与自检

"工具为什么没出现"这个问题，按顺序跑下面三条命令即可定位：

```sh
# 1. MemOS 侧是否就绪？（venv、import、补丁、tokenizer；不改动任何东西）
python scripts/setup.py --memos C:\path\to\MemOS --check

# 2. 基础设施是否可达？（端点、Neo4j、网关、import）
"C:\path\to\MemOS\.venv\Scripts\python.exe" scripts/memos_mcp_runner.py --check

# 3. MCP 握手是否成功？（与 dsh 拉起的是同一个入口）
"C:\path\to\MemOS\.venv\Scripts\python.exe" scripts/smoke_test.py --memos C:\path\to\MemOS --search
```

runner 的环境变量：

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `MEMOS_MCP_PREFLIGHT` | `1` | `0` 完全跳过 Neo4j / 网关探测（适用于丢 SYN 的代理环境） |
| `MEMOS_MCP_PREFLIGHT_TIMEOUT` | `2` | 单次 TCP 探测的秒数 |
| `MEMOS_MCP_ALLOW_NO_NEO4J` | `0` | `1` 表示 Neo4j 不通也照常启动（带警告） |
| `MEMOS_MCP_ENDPOINT_REWRITE` | `1` | `0` 表示 docker 内部主机名原样保留 |
| `MEMOS_MCP_DEBUG` | `0` | `1` 打印解析后的 python、版本与端点 |

## MemOS 源码补丁

`scripts/patch_memos.py` 对 MemOS 目录应用如下幂等修复（**针对 MemoryOS 2.0.30 验证**）：

1. `src/memos/api/config.py` —— `load_dotenv(override=True)` → `load_dotenv()`，宿主机环境变量覆盖不再被 `.env` 冲掉。
2. `src/memos/log.py` —— 控制台日志改到 **stderr**；stdout 是 MCP 协议通道，日志行会污染 stdio 流。
3. `src/memos/api/mcp_serve.py` —— 把 `EMBEDDING_DIMENSION` 映射进默认配置，让 Neo4j 向量索引维度与 embedder 一致。
4. `src/memos/mem_os/utils/default_config.py`：
   - embedder 构造改为读取 `MOS_EMBEDDER_BACKEND` / `MOS_EMBEDDER_API_BASE` / `MOS_EMBEDDER_API_KEY` / `MOS_EMBEDDER_MODEL` / `EMBEDDING_DIMENSION`（对齐 `APIConfig.get_embedder_config`；MCP 默认路径原本会忽略这些并复用聊天端点）；
   - sentence chunker 的 tokenizer 指向**本地** gpt2 `tokenizer.json`——否则 chonkie 会从 huggingface.co 下载 `gpt2`，在部分网络不可达。
5. `src/memos/graph_dbs/neo4j.py`、`neo4j_community.py`、`tree_text_memory/retrieve/bm25_util.py`、`internet_retriever.py` —— 把搜索路径里的裸 `print()`（原始 Cypher 查询、BM25 命中行等）重定向到 **stderr**；否则 stdout 上的这些输出会在搜索时污染 MCP stdio 流。

被改动的文件首次修改前会备份为 `<file>.dsh-bridge.bak`，写入是原子的，换行符与 BOM 均保持不变。

```sh
python scripts/patch_memos.py --list     # 列出脚本会改什么
python scripts/patch_memos.py --check    # 试运行：哪些补丁适配当前目录
python scripts/patch_memos.py --revert   # 从备份恢复
```

若某条补丁报"不在补丁前状态"，说明你的 MemOS 版本与 2.0.30 不同——`--check` 会一次性列出所有不匹配项，对照差异手动应用即可。

## 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| `dsh plugin add` 报 `git ls-remote ... exit code 1` | 该机器没装 git，或 github.com 不可达。改用 npm 包或 release 压缩包（[路径 A/C](#安装组合包)）。 |
| `dsh plugin add` 装出 `Gu` 之类的拆分包 | Windows 下插件路径**含空格**时，dsh 转发给 pnpm 会被拆分。改用 8.3 短路径，或从无空格目录执行 `add .`。 |
| MCP 行一直连不上，spawn 报 `ENOENT` | 要么 `cwd` 为空（0.2.0 已修），要么 `MEMOS_PYTHON` 没指向真实解释器。跑 runner `--check`。 |
| 启动时一屏 `Couldn't connect to localhost:7687` traceback | Neo4j 没起。先启动 MemOS docker 栈；runner 会预检 Neo4j 并打印一行清晰提示，该行会持续重试。 |
| 重启后行一直 pending | `MEMOS_PYTHON`/`MEMOS_HOME` 配错，或 MemOS venv 未建。先看 `dsh --profile web --dump-config`，再跑 `setup.py --check`。 |
| 工具出现了，但 search/chat 报 401/403 | dsh 会从子进程环境里剔除"凭证形状"的变量名（`*KEY*`、`*TOKEN*`、`*SECRET*`、`*PASSWORD*`）。把密钥写进 MemOS 的 `.env`，或在行的 `env` 块里显式传入。 |
| 启动报 `Graph not found: memosdefaultuser` | Neo4j Community 版 + `.env` 里 `MOS_NEO4J_SHARED_DB=false` → 改为 `true` 并设 `NEO4J_AUTO_CREATE=false`（共享单个 `neo4j` 库）。 |
| `Tokenizer 'gpt2' could not be loaded ... huggingface.co` | 运行 `setup.py` 下载本地 tokenizer，或设 `HF_ENDPOINT=https://hf-mirror.com`（补丁已把 tokenizer 指向本地文件）。 |
| `Embeddings request ended with error: Error code: 503` | LLM/Embedding 网关（如 `:18181`/`:18182`）未启动或宿主机不可达——见[端点解析](#端点解析)。 |
| 搜索时出现 `Failed to parse JSONRPC message from server` | 搜索路径里有裸 `print()` 写到 stdout——重跑 `patch_memos.py`（补丁 #5）并重启。 |
| 子进程日志报 `UnicodeEncodeError` | 控制台代码页不是 UTF-8。该行已默认设 `PYTHONUTF8=1`；如果你覆盖过该行，记得把那两个 env 键补回去。 |
| 启动时的 `pydantic` 序列化警告 | 无害；MemOS 序列化配置对象时会打印。 |

提 issue 时，请附上 `scripts/memos_mcp_runner.py --check` 的输出（涵盖 python、版本、端点、Neo4j、网关）以及 `dsh --dump-config` 里对应的行。

## 安全说明

MCP server 命令是**沙箱之外执行的受信代码**（这正是 Harness 默认不启用任何 MCP server 的原因）。只连接你自己运行的 MemOS 服务。

两点值得注意：

- `patch_memos.py` 会就地修改第三方源码目录。运行前先看 `--list`；`--revert` 可从备份恢复。
- Harness 传给 MCP 子进程的是**清洗过的环境变量**：名字里含 `KEY`、`TOKEN`、`SECRET`、`PASSWORD` 的变量，以及所有 `DSH_*` 变量都会被丢弃。密钥请放在 MemOS 自己的 `.env` 或行的 `env` 块里。

## 开发

```sh
node --check index.js                  # 提供方插件语法
node --test tests/provider.test.mjs    # 提供方契约（cwd、命名空间、回退逻辑）
python -m unittest discover -s tests   # 补丁引擎、patch 层、runner 辅助函数
```

两套测试都可离线运行、不需要安装 MemOS，并且跨平台：Windows 与 Linux、Python 3.10+ 均通过。

## 许可证

MIT
