# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] — 2026-11-03

Reliability and distribution release: several real start-up failures are fixed,
the bundle is installable without `git`, and both READMEs now describe what the
code actually does.

### Fixed

- **Empty `cwd` broke every default install.** `MEMOS_HOME` is optional, and the
  provider used to emit `cwd: ''`. `@modelcontextprotocol/sdk` forwards that to
  `child_process.spawn` unconditionally, which fails with a bare `ENOENT` that
  looks like a missing interpreter. The provider now resolves a real directory
  and falls back to the Harness cwd.
- **Endpoint defaults silently overrode `MEMOS_HOME/.env`.** The runner used to
  force `127.0.0.1:18181/18182` before dotenv loaded, so a `.env` pointing at a
  real remote gateway was ignored. Resolution is now explicit: ambient env wins,
  a docker-internal host in `.env` is mapped to the published loopback port only
  when that port answers, and defaults apply only to keys left unset.
- `patch_memos.py` raised on the first missing source file, hiding the rest and
  leaving a partially applied tree. Missing files are now reported together
  before anything is written.
- `patch_memos.py` truncated files on write; writes are now atomic (temp file +
  replace), preserve the file mode, and fail with a clear message on non-UTF-8
  sources instead of a `UnicodeDecodeError`.
- `setup.py --skip-deps` no longer proceeds with a nonexistent venv python.
- The smoke test spawned `python -m memos.api.mcp_serve` directly, bypassing the
  runner that dsh actually starts; it now exercises the real entrypoint
  (`--direct` keeps the old behaviour).

### Added

- `memos_mcp_runner.py --check`: a diagnosis report covering python, MemOS
  version, `.env` candidates, resolved endpoints, Neo4j and both gateways.
- `setup.py --check`: verifies venv, `memos.api.mcp_serve` import, patch
  applicability and the tokenizer without changing anything.
- `MEMOS_MCP_ENDPOINT_REWRITE` to keep docker-internal hostnames verbatim.
- Environment knobs `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` for the MCP child,
  so a non-UTF-8 Windows console cannot turn a log line into a `UnicodeEncodeError`.
- `toolCallTimeoutMs: 120000` in the shipped row (the client default is 60 s,
  which LLM-backed search and memory writes routinely exceed).
- Offline regression suites: provider contract tests (`node --test`), the patch
  engine against synthesised MemOS trees, the patch layer's key allow-list, and
  the runner's URI helpers.
- GitHub Actions CI (Linux + Windows, Python 3.10/3.12, plus a packaging check)
  and a tag-driven release workflow that publishes to npm and attaches the
  installable tarball.
- `CONTRIBUTING.md`, issue templates that ask for the diagnosis output, and a
  full Chinese README at parity with the English one.

### Changed

- `MEMOS_MCP_SERVER` is validated against the namespace pattern instead of being
  passed through; an invalid value warns and falls back to `memos`.
- `MEMOS_PYTHON` / `MEMOS_HOME` are trimmed, expand `~`, and blank values count
  as unset.
- The provider fails at mount time with an actionable message when the runner
  script is missing, instead of an opaque spawn error later.
- `package.json` gained the distribution metadata npm needs (`homepage`, `bugs`,
  `author`, `publishConfig`, `files`, `scripts`) and a version bump to 0.2.0.

### Documented

- Three install paths (npm / git / release tarball), including the `git ls-remote`
  failure that path B hits behind a restricted network.
- The harness scrubs credential-shaped variables (`*KEY*`, `*TOKEN*`, `*SECRET*`,
  `*PASSWORD*`) from MCP child environments — a common cause of 401/403 from
  `search_memories`.
- `reconnect` requires `@deepseek-ai/dsh-mcp-client` 0.2.x or newer; older
  versions ignore the block.

## [0.1.0] — 2026-08-31

Initial release: bundle patch layer, MemOS setup script, source patch engine,
MCP runner with Neo4j/gateway preflight, smoke test, and English/Chinese READMEs.

[Unreleased]: https://github.com/Swd146296/dsh-memos-bridge/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Swd146296/dsh-memos-bridge/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Swd146296/dsh-memos-bridge/releases/tag/v0.1.0
