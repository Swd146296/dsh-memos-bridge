/**
 * dsh-memos-bridge provider plugin.
 *
 * Provides the `memosBridge` service so the bundle's cordis.patch.yml can
 * reference this package's own MCP runner without hardcoding absolute paths:
 * the mcp-client row injects `memosBridge` and reads
 * `ctx.memosBridge.python` / `.runner` / `.cwd` in its `!!js` config.
 *
 * Named exports only (no default export): a stray default export makes the
 * Loader collapse the namespace and drop `inject`/`apply`.
 *
 * @typedef {object} MemosBridgeValues
 * @property {string} python - Python of the MemOS venv (`MEMOS_PYTHON`, default `python`).
 * @property {string} runner - Absolute path of this package's MCP runner script.
 * @property {string} cwd - Working directory for the child (`MEMOS_HOME`, default '' = inherit).
 */
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/** Stable Cordis plugin name. */
export const name = 'dsh-memos-bridge'

/** Service provided by this plugin and injected by the mcp-client row. */
export const MEMOS_BRIDGE_SERVICE = 'memosBridge'

/**
 * Provide the MemOS launch values for this installation.
 * @param {object} ctx - plugin context.
 * @param {(name: string, value: MemosBridgeValues) => void} ctx.provide - Cordis service provider.
 */
export function apply(ctx) {
  const here = dirname(fileURLToPath(import.meta.url))
  ctx.provide(MEMOS_BRIDGE_SERVICE, {
    python: process.env.MEMOS_PYTHON ?? 'python',
    runner: join(here, 'scripts', 'memos_mcp_runner.py'),
    cwd: process.env.MEMOS_HOME ?? '',
  })
}
