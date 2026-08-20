# dsh-memos-bridge

一个 [DeepSeek Harness](https://github.com/deepseek-harness/deepseek-harness) **组合包（bundle）**，通过 **MCP** 把 [MemOS](https://github.com/MemTensor/MemOS) 记忆服务桥接进 agent。安装组合包、运行一次设置脚本、重启 Harness 后，agent 就获得了名为 `mcp__memos__*` 的持久记忆工具。

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
- 运行 MCP 子进程的机器能访问 MemOS 的 LLM 与 Embedding 网关（见[宿主机端点覆盖](#宿主机端点覆盖)）。

## 快速开始

**1. 准备 MemOS 侧**（venv + 依赖 + 源码补丁 + 本地 tokenizer）：

```powershell
# 在插件目录下
.\setup.ps1 --memos C:\path\to\MemOS
```

POSIX：`./setup.sh --memos /path/to/MemOS`。脚本会创建 `MemOS/.venv`，安装 `MemoryOS[tree-mem]` 及 `python-dotenv`、`tqdm`、`langchain_text_splitters`、`chonkie`，应用必需的源码补丁（见下文），并下载本地 gpt2 tokenizer.json（优先 HuggingFace 镜像）。

**2. 把组合包安装进 profile：**

```sh
dsh plugin --profile web add ./dsh-memos-bridge
```

**3. 配置路径**（patch 在启动时读取，均可选）：

```sh
# PowerShell: setx MEMOS_PYTHON "C:\path\to\MemOS\.venv\Scripts\python.exe"
#             setx MEMOS_HOME   "C:\path\to\MemOS"
export MEMOS_PYTHON=/path/to/MemOS/.venv/bin/python
export MEMOS_HOME=/path/to/MemOS
```

不设置 `MEMOS_PYTHON` 时回退到 `PATH` 上的 `python`；不设置 `MEMOS_HOME` 时子进程继承 Harness 的工作目录（MemOS 仍会读取自己的 `.env`，建议把 `MEMOS_HOME` 指向源码目录，除非 MemOS 就在启动目录下）。

**4. 验证并重启：**

```sh
dsh --profile web --dump-config    # 应看到 `id: memos-mcp` 行
dsh --profile web                  # 重启 GUI，工具以 mcp__memos__* 出现
```

随时可跑冒烟测试：

```sh
python scripts/smoke_test.py --python C:\path\to\MemOS\.venv\Scripts\python.exe --memos C:\path\to\MemOS --search
```

## 配置

组合包的 patch 插入一行 `@deepseek-ai/dsh-mcp-client`（`id: memos-mcp`，`serverName: memos`）。挂载时由 `!!js` 读取的环境变量：

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `MEMOS_PYTHON` | `python` | MemOS venv 的 python |
| `MEMOS_HOME` | `''`（继承 cwd） | 作为子进程 cwd 的 MemOS 目录 |
| `MEMOS_MCP_SERVER` | `memos` | 工具命名空间（`mcp__<name>__*`） |

想改其他字段（如 `toolCallTimeoutMs`、`failOnStartupError`），在你的 profile `cordis.patch.yml` 中**按 id 覆盖**该行——后层优先，但按 id 的 patch 会整体替换 `config`，需要重述所有键：

```yaml
- id: memos-mcp
  config:
    transport: stdio
    serverName: memos
    command: 'C:/path/to/MemOS/.venv/Scripts/python.exe'
    args: ['-m', 'memos.api.mcp_serve']
    cwd: 'C:/path/to/MemOS'
    toolCallTimeoutMs: 60000
    failOnStartupError: false
```

## 宿主机端点覆盖

MemOS 的 `.env` 通常指向 `host.docker.internal:18181/18182`（在 MemOS 的 docker 网络*内部*有效）。当 MCP 子进程跑在**宿主机**上时，端点必须从宿主机可达。如果网关发布在宿主机回环地址（或只能经本地代理规则到达），在该行上加 `env` 块覆盖端点：

```yaml
- id: memos-mcp
  config:
    transport: stdio
    serverName: memos
    command: 'C:/path/to/MemOS/.venv/Scripts/python.exe'
    args: ['-m', 'memos.api.mcp_serve']
    cwd: 'C:/path/to/MemOS'
    env:
      OPENAI_API_BASE: 'http://127.0.0.1:18181/v1'
      MOS_EMBEDDER_API_BASE: 'http://127.0.0.1:18182/compatible-mode/v1'
      MEMRADER_API_BASE: 'http://127.0.0.1:18181/v1'
      QWEN_API_BASE: 'http://127.0.0.1:18181/v1'
    failOnStartupError: false
```

（这些值能生效的前提是补丁脚本把 MemOS 的 `load_dotenv(override=True)` 改成了 `override=False`——外部环境变量优先于 `.env`。）

## MemOS 源码补丁

`scripts/patch_memos.py` 对 MemOS 目录应用五个小而幂等的修复（**针对 MemoryOS 2.0.30 验证**）：

1. `src/memos/api/config.py` —— `load_dotenv(override=True)` → `load_dotenv()`，宿主机环境变量覆盖不再被 `.env` 冲掉。
2. `src/memos/log.py` —— 控制台日志改到 **stderr**；stdout 是 MCP 协议通道，日志行会污染 stdio 流。
3. `src/memos/api/mcp_serve.py` —— 把 `EMBEDDING_DIMENSION` 映射进默认配置，让 Neo4j 向量索引维度与 embedder 一致。
4. `src/memos/mem_os/utils/default_config.py`：
   - embedder 构造改为读取 `MOS_EMBEDDER_BACKEND` / `MOS_EMBEDDER_API_BASE` / `MOS_EMBEDDER_API_KEY` / `MOS_EMBEDDER_MODEL` / `EMBEDDING_DIMENSION`（对齐 `APIConfig.get_embedder_config`；MCP 默认路径原本会忽略这些并复用聊天端点）；
   - sentence chunker 的 tokenizer 指向**本地** gpt2 `tokenizer.json`——否则 chonkie 会从 huggingface.co 下载 `gpt2`，在部分网络不可达。

`python scripts/patch_memos.py --list` 可查看补丁清单。若某条补丁报"不在补丁前状态"，说明你的 MemOS 版本与 2.0.30 不同——对照差异手动应用。

## 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| `dsh plugin add` 装出 `Gu` 之类的拆分包 | Windows 下插件路径**含空格**时，dsh 转发给 pnpm 会被拆分。改用 8.3 短路径（如 `C:\Users\GULING~1\...`）或从无空格目录执行 `add .`。 |
| 重启后行一直 pending | `MEMOS_PYTHON`/`MEMOS_HOME` 配错，或 MemOS venv 未建。先看 `dsh --profile web --dump-config`。 |
| 启动报 `Graph not found: memosdefaultuser` | Neo4j Community 版 + `.env` 里 `MOS_NEO4J_SHARED_DB=false` → 改为 `true` 并设 `NEO4J_AUTO_CREATE=false`（共享单个 `neo4j` 库）。 |
| `Tokenizer 'gpt2' could not be loaded ... huggingface.co` | 运行 `setup.py` 下载本地 tokenizer，或设 `HF_ENDPOINT=https://hf-mirror.com`（补丁已把 tokenizer 指向本地文件）。 |
| `Embeddings request ended with error: Error code: 503` | LLM/Embedding 网关（如 `:18181`/`:18182`）未启动或宿主机不可达——见[宿主机端点覆盖](#宿主机端点覆盖)，启动网关。 |
| MCP 握手失败 / `Failed to parse JSONRPC` | 日志写到了 stdout——重跑 `patch_memos.py`（补丁 #2）。 |
| 启动时的 `pydantic` 序列化警告 | 无害；MemOS 序列化配置对象时会打印。 |

## 安全说明

MCP server 命令是**沙箱之外执行的受信代码**（这正是 Harness 默认不启用任何 MCP server 的原因）。只连接你自己运行的 MemOS 服务。

## 许可证

MIT
