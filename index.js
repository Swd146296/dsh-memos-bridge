/**
 * dsh-memos-bridge provider plugin.
 *
 * Provides the `memosBridge` service so the bundle's cordis.patch.yml can
 * reference this package's own MCP runner without hardcoding absolute paths:
 * the mcp-client row injects `memosBridge` and reads
 * `ctx.memosBridge.python` / `.runner` / `.cwd` / `.serverName` in its `!!js`
 * config.
 *
 * Named exports only (no default export): a stray default export makes the
 * Loader collapse the namespace and drop `inject`/`apply`.
 *
 * @typedef {object} MemosBridgeValues
 * @property {string} python - Python of the MemOS venv (`MEMOS_PYTHON`, default `python`).
 * @property {string} runner - Absolute path of this package's MCP runner script.
 * @property {string} cwd - Working directory for the child. NEVER empty: Node's
 *   `spawn` fails with ENOENT when `cwd` is the empty string, so an unset
 *   `MEMOS_HOME` falls back to the Harness process cwd instead.
 * @property {string} home - MemOS checkout (`MEMOS_HOME`) or `''` when unset.
 * @property {string} serverName - MCP namespace / tool prefix (`MEMOS_MCP_SERVER`).
 */
import { existsSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/** Stable Cordis plugin name. */
export const name = 'dsh-memos-bridge'

/** Service provided by this plugin and injected by the mcp-client row. */
export const MEMOS_BRIDGE_SERVICE = 'memosBridge'

/** Same pattern `@deepseek-ai/dsh-mcp-client` enforces for `serverName`. */
const SERVER_NAME_PATTERN = /^[A-Za-z0-9_-]{1,32}$/

/** Namespace used when `MEMOS_MCP_SERVER` is unset or invalid. */
const DEFAULT_SERVER_NAME = 'memos'

/** Name of the script spawned as the MCP stdio child. */
const RUNNER_RELATIVE_PATH = join('scripts', 'memos_mcp_runner.py')

/**
 * Read a string env var, treating blank values as unset.
 * @param {string} key - environment variable name.
 * @returns {string} trimmed value or `''`.
 */
function envString(key) {
  const raw = process.env[key]
  return typeof raw === 'string' ? raw.trim() : ''
}

/**
 * Expand a leading `~` and make the path absolute.
 *
 * Windows users routinely write `~/MemOS` or quote a path with a stray
 * trailing slash; both would otherwise reach `spawn` verbatim.
 * @param {string} value - raw path from the environment.
 * @returns {string} absolute path, or `''` when the input is blank.
 */
function resolvePath(value) {
  if (!value) return ''
  let expanded = value
  if (expanded === '~') expanded = homedir()
  else if (expanded.startsWith('~/') || expanded.startsWith('~\\')) {
    expanded = join(homedir(), expanded.slice(2))
  }
  // Strip trailing separators (but keep a bare drive root like `C:\`).
  expanded = expanded.replace(/[\\/]+$/, '')
  if (!expanded) return ''
  if (/^[A-Za-z]:$/.test(expanded)) return `${expanded}\\`
  return isAbsolute(expanded) ? expanded : resolve(expanded)
}

/**
 * True when `path` exists and is a directory.
 * @param {string} path - candidate directory.
 * @returns {boolean} existence check that never throws.
 */
function isDirectory(path) {
  try {
    return statSync(path).isDirectory()
  } catch {
    return false
  }
}

/**
 * Resolve the child's working directory.
 *
 * `cwd` must never be `''`: `@modelcontextprotocol/sdk` forwards it to
 * `child_process.spawn` unconditionally, and an empty `cwd` fails the spawn
 * with a bare `ENOENT` that looks like a missing interpreter.
 *
 * @param {string} home - configured `MEMOS_HOME` (already resolved), or `''`.
 * @param {string} fallback - directory used when `MEMOS_HOME` is unset.
 * @returns {{cwd: string, note: string}} resolved cwd plus a diagnostic note.
 */
function resolveCwd(home, fallback) {
  if (!home) {
    return { cwd: fallback, note: 'MEMOS_HOME is unset; the child inherits the Harness cwd' }
  }
  if (!isDirectory(home)) {
    return { cwd: fallback, note: `MEMOS_HOME=${home} is not a directory; falling back to the Harness cwd` }
  }
  if (!existsSync(join(home, 'pyproject.toml'))) {
    return {
      cwd: home,
      note: `MEMOS_HOME=${home} has no pyproject.toml; is it really the MemOS checkout?`,
    }
  }
  return { cwd: home, note: '' }
}

/**
 * Validate and normalise the MCP namespace.
 * @param {string} raw - value of `MEMOS_MCP_SERVER`.
 * @returns {{serverName: string, note: string}} usable name plus a diagnostic note.
 */
function resolveServerName(raw) {
  if (!raw) return { serverName: DEFAULT_SERVER_NAME, note: '' }
  if (SERVER_NAME_PATTERN.test(raw)) return { serverName: raw, note: '' }
  return {
    serverName: DEFAULT_SERVER_NAME,
    note: `MEMOS_MCP_SERVER=${JSON.stringify(raw)} is not a valid namespace ` +
      `(${SERVER_NAME_PATTERN}); using ${JSON.stringify(DEFAULT_SERVER_NAME)}`,
  }
}

/**
 * Provide the MemOS launch values for this installation.
 * @param {object} ctx - plugin context.
 * @param {(name: string, value: MemosBridgeValues) => void} ctx.provide - Cordis service provider.
 * @param {object} [ctx.logger] - optional Cordis logger.
 */
export function apply(ctx) {
  const here = dirname(fileURLToPath(import.meta.url))
  const runner = join(here, RUNNER_RELATIVE_PATH)

  const home = resolvePath(envString('MEMOS_HOME'))
  const python = resolvePath(envString('MEMOS_PYTHON')) || 'python'
  const { cwd, note: cwdNote } = resolveCwd(home, process.cwd())
  const { serverName, note: nameNote } = resolveServerName(envString('MEMOS_MCP_SERVER'))

  const notes = [cwdNote, nameNote].filter(Boolean)

  if (!existsSync(runner)) {
    // Fail at mount time with an actionable message: otherwise the mcp-client
    // row dies with a bare ENOENT and the cause (a broken/partial install) is
    // invisible.
    throw new Error(
      `dsh-memos-bridge: the MCP runner is missing at ${runner}. ` +
        'The package install is incomplete — reinstall the bundle ' +
        '(`dsh plugin add <bundle>`) and check that scripts/ is present.',
    )
  }

  if (python === 'python') {
    notes.push(
      'MEMOS_PYTHON is unset; falling back to `python` on PATH. If that is not ' +
        'the MemOS virtualenv, start-up will report that MemOS cannot be imported.',
    )
  }

  const values = { python, runner, cwd, home, serverName }
  ctx.provide(MEMOS_BRIDGE_SERVICE, values)

  for (const note of notes) ctx.logger?.warn?.(`dsh-memos-bridge: ${note}`)
  ctx.logger?.debug?.(
    `dsh-memos-bridge: python=${python} cwd=${cwd} namespace=mcp__${serverName}__*`,
  )
}
