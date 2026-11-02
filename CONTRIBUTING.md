# Contributing to dsh-memos-bridge

Thanks for helping. This project is a thin configuration layer over two moving
parts (DeepSeek Harness's MCP client and MemOS), so most bugs are **integration**
bugs — the fastest way to a fix is a precise report.

[简体中文](#简体中文)

## Before you open an issue

Run the three diagnostics and paste their output. They cover ~90% of reports and
need no guessing:

```sh
python scripts/setup.py --memos <MemOS> --check
<MemOS>/.venv/Scripts/python.exe scripts/memos_mcp_runner.py --check
<MemOS>/.venv/Scripts/python.exe scripts/smoke_test.py --memos <MemOS> --search
```

Also useful: the `id: memos-mcp` block from `dsh --profile <p> --dump-config`,
the MemOS version (`MemoryOS 2.0.30` is what the patches target), and the OS.

## Ground rules

- **The patches are text-anchored to MemOS 2.0.30.** A patch that reports
  "not in pre-patch state" is a version-mismatch signal, not a bug by itself.
  When you touch `scripts/patch_memos.py`, keep patches idempotent: applying
  twice must produce the same file, and `--revert` must restore the original
  bytes. The test suite checks both.
- **`stdout` belongs to MCP.** Anything the child process prints to stdout that
  is not a JSON-RPC frame corrupts the session. Diagnostics go to stderr.
- **`cwd` is never empty.** The MCP SDK forwards it to `spawn` verbatim, and an
  empty value fails with a bare `ENOENT`.
- **Unsupported config keys are silently dropped** by the client's schema, so a
  typo in `cordis.patch.yml` looks like "my setting does nothing".
  `tests/test_patch_layer.py` pins the allowed key set — update it when the
  client adds options.
- Keep both READMEs in sync. Chinese is not a translation afterthought here:
  most users arrive from a Chinese-language plugin market.

## Local checks

Everything runs offline; no MemOS installation is required.

```sh
node --check index.js
node --test tests/provider.test.mjs
python -m unittest discover -s tests -v
```

Python 3.10+ is the floor (the scripts use `X | None` annotations and
`Path.write_text(newline=...)`). Keep the suites green on both Windows and
Linux — the Windows path is what most users run.

## Commit style

Conventional-commit subjects (`fix:`, `feat:`, `docs:`, `chore:`, `test:`),
lowercase, no trailing period. One logical change per commit; explain the
*observable* symptom in the body when the diff is not self-evident.

---

## 简体中文

### 提问前

先跑上面三条自检命令并附上输出——它们能覆盖绝大多数报告，不需要靠猜。另外
请附 `dsh --profile <p> --dump-config` 中的 `id: memos-mcp` 段、MemOS 版本
（补丁针对 `MemoryOS 2.0.30`）和操作系统。

### 基本约定

- **补丁是按文本锚定到 MemOS 2.0.30 的。** 报"不在补丁前状态"通常意味着版本
  不匹配，未必是 bug。改动 `scripts/patch_memos.py` 时必须保持幂等：连续应用
  两次结果相同，`--revert` 能还原原始字节——测试会校验这两点。
- **stdout 属于 MCP 协议。** 子进程往 stdout 打任何非 JSON-RPC 内容都会破坏会话，
  诊断信息一律走 stderr。
- **`cwd` 不能为空。** MCP SDK 会原样传给 `spawn`，空值直接抛裸 `ENOENT`。
- **客户端会静默丢弃不认识的配置键**，所以 `cordis.patch.yml` 里写错键名表现为
  "设置不生效"。`tests/test_patch_layer.py` 锁定了允许的键集合，客户端新增选项时
  记得同步。
- 两份 README 必须同步更新——中文不是事后翻译，多数用户来自中文插件市场。

### 本地检查

无需安装 MemOS，全部可离线运行：

```sh
node --check index.js
node --test tests/provider.test.mjs
python -m unittest discover -s tests -v
```

Python 最低 3.10（脚本使用了 `X | None` 注解与 `Path.write_text(newline=...)`）。

### 提交信息

使用约定式前缀（`fix:`、`feat:`、`docs:`、`chore:`、`test:`），小写、结尾不加句号。
一次提交只做一件事；如果 diff 不足以说明问题，在正文里描述**可观察到的现象**。
