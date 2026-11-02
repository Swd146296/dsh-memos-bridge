/**
 * Provider contract tests for index.js.
 *
 * Run with `node --test tests/` (no dependencies: node:test ships with Node 20+).
 *
 * The important case is `cwd`: @modelcontextprotocol/sdk forwards it to
 * child_process.spawn unconditionally, and an empty string fails the spawn
 * with a bare ENOENT that is indistinguishable from a missing interpreter.
 */

import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'

import { apply, MEMOS_BRIDGE_SERVICE, name } from '../index.js'

const ENV_KEYS = ['MEMOS_PYTHON', 'MEMOS_HOME', 'MEMOS_MCP_SERVER']

/** Call apply() with a clean env, returning the provided service values. */
function provide(env = {}) {
  const saved = Object.fromEntries(ENV_KEYS.map((key) => [key, process.env[key]]))
  for (const key of ENV_KEYS) delete process.env[key]
  Object.assign(process.env, env)

  const warnings = []
  const ctx = {
    provide: (service, value) => {
      assert.equal(service, MEMOS_BRIDGE_SERVICE)
      captured = value
    },
    logger: { warn: (message) => warnings.push(message), debug: () => {} },
  }
  let captured
  try {
    apply(ctx)
    return { values: captured, warnings }
  } finally {
    for (const key of ENV_KEYS) {
      if (saved[key] === undefined) delete process.env[key]
      else process.env[key] = saved[key]
    }
  }
}

function fakeCheckout() {
  const dir = mkdtempSync(join(tmpdir(), 'memos-bridge-'))
  writeFileSync(join(dir, 'pyproject.toml'), '[project]\nname = "MemoryOS"\n')
  return dir
}

test('exports a named plugin and service only', () => {
  assert.equal(name, 'dsh-memos-bridge')
  assert.equal(MEMOS_BRIDGE_SERVICE, 'memosBridge')
})

test('defaults never leave cwd empty', () => {
  const { values } = provide()
  assert.equal(values.cwd, process.cwd())
  assert.notEqual(values.cwd, '')
  assert.equal(values.serverName, 'memos')
  assert.equal(values.python, 'python')
  assert.equal(values.home, '')
  assert.ok(values.runner.endsWith(join('scripts', 'memos_mcp_runner.py')))
})

test('MEMOS_HOME wins as the child cwd', () => {
  const home = fakeCheckout()
  const { values } = provide({ MEMOS_HOME: home })
  assert.equal(values.cwd, home)
  assert.equal(values.home, home)
})

test('a bogus MEMOS_HOME falls back instead of spawning with an invalid cwd', () => {
  const missing = join(tmpdir(), 'definitely-not-a-memos-checkout-xyz')
  const { values, warnings } = provide({ MEMOS_HOME: missing })
  assert.equal(values.cwd, process.cwd())
  assert.ok(warnings.some((line) => line.includes('not a directory')))
})

test('a directory without pyproject.toml still becomes cwd, with a warning', () => {
  const dir = mkdtempSync(join(tmpdir(), 'not-memos-'))
  mkdirSync(join(dir, 'src'), { recursive: true })
  const { values, warnings } = provide({ MEMOS_HOME: dir })
  assert.equal(values.cwd, dir)
  assert.ok(warnings.some((line) => line.includes('pyproject.toml')))
})

test('values are trimmed and blank means unset', () => {
  const home = fakeCheckout()
  const { values } = provide({ MEMOS_HOME: `  ${home}  `, MEMOS_PYTHON: '   ' })
  assert.equal(values.cwd, home)
  assert.equal(values.python, 'python')
})

test('an invalid namespace falls back to memos', () => {
  const { values, warnings } = provide({ MEMOS_MCP_SERVER: 'bad name!' })
  assert.equal(values.serverName, 'memos')
  assert.ok(warnings.some((line) => line.includes('not a valid namespace')))
})

test('a valid custom namespace is honoured', () => {
  const { values } = provide({ MEMOS_MCP_SERVER: 'team_mem-2' })
  assert.equal(values.serverName, 'team_mem-2')
})

test('a bare python on PATH is reported as a fallback', () => {
  const { warnings } = provide()
  assert.ok(warnings.some((line) => line.includes('MEMOS_PYTHON is unset')))
})
