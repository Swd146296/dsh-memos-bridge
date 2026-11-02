"""Launch the MemOS MCP server with startup preflight checks.

Runs the same MCP stdio server as ``python -m memos.api.mcp_serve``, but first
checks the hard dependency (Neo4j) and the soft dependencies (LLM / embedding
gateway) so a downed infrastructure stack fails with one clear, actionable
line instead of a wall of connection tracebacks.

Hard dependency (fatal):  Neo4j      - the MemOS graph store; the MOS core
                                       constructs it eagerly at startup.
Soft dependency (warn):   LLM/embed  - only search/chat tools need them;
                                       add_memory (plain text) still works.

Usage:
    python memos_mcp_runner.py            # serve over stdio (what dsh spawns)
    python memos_mcp_runner.py --check    # print a diagnosis report and exit

Exit codes (stable; the dsh mcp-client reconnect policy retries every one):
  0  the server exited cleanly (client disconnected / shutdown)
  1  infrastructure unavailable (Neo4j down, MOS init failed)
  2  this python cannot run MemOS (memos not installed, broken dependency)
  130 interrupted

Environment knobs:
  MEMOS_HOME            MemOS checkout; also the child cwd and the directory
                        whose .env is read before the preflight
  MEMOS_MCP_PREFLIGHT   set to 0/off/false/no to skip the network preflight
                        (useful when endpoints sit behind a proxy or a
                        firewall that drops connects instead of refusing them)
  MEMOS_MCP_PREFLIGHT_TIMEOUT  seconds per probe (default 2)
  MEMOS_MCP_ALLOW_NO_NEO4J     set to 1 to start even when Neo4j is down
  MEMOS_MCP_ENDPOINT_REWRITE   set to 0 to keep docker-internal hosts verbatim
                        (see "Endpoint resolution" below)
  MEMOS_MCP_DEBUG       set to 1 to echo the resolved python / MemOS path

Endpoint resolution
-------------------
MemOS's ``.env`` usually targets ``host.docker.internal:18181/18182``, which is
valid *inside* the MemOS docker network but often unresolvable from the host.
Precedence, highest first:

  1. the ambient environment (a dsh row ``env`` block, or the shell) -- never
     rewritten, so an explicit override always wins;
  2. ``.env`` values from MEMOS_HOME / cwd -- a docker-internal host is mapped
     to the published loopback port, but only when that port actually answers;
  3. this bundle's ``127.0.0.1`` defaults, for keys left entirely unset.

A ``.env`` that already points at a real remote gateway (DashScope, DeepSeek,
a company proxy, ...) is therefore left untouched.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import traceback
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

LOG = "[dsh-memos-bridge]"

DEFAULT_OPENAI_API_BASE = "http://127.0.0.1:18181/v1"
DEFAULT_EMBEDDER_API_BASE = "http://127.0.0.1:18182/compatible-mode/v1"

#: Endpoint keys this runner knows how to reason about, with the label used in
#: diagnostics. Order is the order they are reported in.
_ENDPOINTS: tuple[tuple[str, str, str], ...] = (
    ("OPENAI_API_BASE", "LLM gateway", DEFAULT_OPENAI_API_BASE),
    ("MOS_EMBEDDER_API_BASE", "embedding gateway", DEFAULT_EMBEDDER_API_BASE),
    ("MEMRADER_API_BASE", "memory reader", DEFAULT_OPENAI_API_BASE),
    ("QWEN_API_BASE", "Qwen gateway", DEFAULT_OPENAI_API_BASE),
)

#: Hosts that only resolve inside a docker network. A host-run child that keeps
#: them fails to connect even though the service is published on the loopback.
_DOCKER_INTERNAL_HOSTS = frozenset(
    {
        "host.docker.internal",
        "gateway.docker.internal",
        "docker.for.win.localhost",
        "docker.for.mac.localhost",
    }
)

_SCHEME_PORTS = {
    "bolt": 7687,
    "neo4j": 7687,
    "bolt+s": 7687,
    "bolt+ssc": 7687,
    "neo4j+s": 7687,
    "neo4j+ssc": 7687,
    "http": 80,
    "https": 443,
}

_FALSEY = {"0", "off", "false", "no", "none", ""}


def _log(message: str) -> None:
    """Write one line to stderr (never stdout: that is the MCP channel)."""
    print(f"{LOG} {message}", file=sys.stderr, flush=True)


def _safe_stderr() -> None:
    """Make stderr tolerate characters the console codepage cannot encode.

    On a Windows cp936/cp1252 console, echoing a path that contains non-ASCII
    characters raises ``UnicodeEncodeError`` *inside* the error handler -- the
    preflight would then crash while reporting a problem instead of printing it.
    """
    for stream in (sys.stderr, sys.stdout):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):  # pragma: no cover - old/odd streams
            pass


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSEY


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        _log(f"WARNING: ignoring non-numeric {name}={raw!r}; using {default}")
        return default
    return value if value > 0 else default


def _host_port(uri: str, default_port: int | None = None) -> tuple[str, int] | None:
    """Parse ``scheme://host:port/path`` (or a bare ``host:port``).

    Returns ``(host, port)``, or ``None`` when the string has no usable
    host/port pair. Never raises: a malformed NEO4J_URI must produce a clear
    message, not a traceback from the preflight itself.
    """
    if not uri or not uri.strip():
        return None
    raw = uri.strip()

    if "://" in raw:
        try:
            parsed = urlsplit(raw)
            host = parsed.hostname
            port = parsed.port
        except ValueError:  # malformed IPv6 literal or out-of-range port
            return None
        if port is None:
            port = default_port
        if port is None:
            port = _SCHEME_PORTS.get((parsed.scheme or "").lower())
    else:
        hostport = raw.split("/", 1)[0]
        host, sep, port_text = hostport.rpartition(":")
        if not sep:
            host, port = hostport, default_port
        else:
            try:
                port = int(port_text)
            except ValueError:
                return None

    if not host or port is None:
        return None
    if not 0 < port < 65536:
        return None
    return host, port


def _reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _env_dirs() -> list[Path]:
    """Directories that may hold the MemOS ``.env``, most specific first."""
    dirs: list[Path] = []
    for raw in (
        os.environ.get("MEMOS_HOME"),  # explicit, set by dsh-memos-bridge users
        os.getcwd(),  # the mcp-client row sets cwd to MEMOS_HOME
        str(Path(__file__).resolve().parent.parent),  # plugin checkout fallback
    ):
        if not raw:
            continue
        try:
            path = Path(raw).expanduser()
        except (OSError, ValueError):
            continue
        if path not in dirs:
            dirs.append(path)
    return dirs


def _load_env_files() -> list[Path]:
    """Load MemOS ``.env`` from MEMOS_HOME / cwd without overriding ambient env.

    ``load_dotenv()`` without a path resolves relative to *this file*, i.e. the
    plugin checkout -- never the MemOS repo. That silently made the preflight
    read NEO4J_URI from nowhere and fall back to ``bolt://localhost:7687``.

    Returns the ``.env`` files that were actually read (for diagnostics).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return []

    loaded: list[Path] = []
    seen: set[str] = set()
    for directory in _env_dirs():
        candidate = directory / ".env"
        try:
            key = str(candidate.resolve())
        except OSError:  # pragma: no cover - unresolvable path
            key = str(candidate)
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        try:
            load_dotenv(candidate, override=False)
            loaded.append(candidate)
        except (OSError, ValueError) as exc:  # unreadable / malformed .env
            _log(f"WARNING: could not read {candidate}: {exc}")
    return loaded


def _to_loopback(uri: str) -> str | None:
    """Rewrite a docker-internal ``uri`` to the same port on 127.0.0.1.

    Returns ``None`` when the uri does not use a docker-internal host. The
    caller decides whether the loopback port is actually serving.
    """
    parsed = urlsplit(uri.strip())
    host = (parsed.hostname or "").lower()
    if host not in _DOCKER_INTERNAL_HOSTS:
        return None
    netloc = "127.0.0.1" if parsed.port is None else f"127.0.0.1:{parsed.port}"
    if parsed.username:  # preserve credentials (bolt://user:pass@host)
        netloc = f"{parsed.username}" + (f":{parsed.password}" if parsed.password else "") + f"@{netloc}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def resolve_endpoints(timeout: float = 2.0) -> list[tuple[str, str, str]]:
    """Load MemOS env, apply defaults, and map docker-internal hosts.

    Returns ``(key, value, source)`` triples for diagnostics, where source is
    one of ``env`` / ``.env`` / ``default``, optionally suffixed with
    ``+loopback`` when the value was rewritten for a host-run deployment.
    """
    ambient = {key for key, _, _ in _ENDPOINTS if os.getenv(key)}
    _load_env_files()

    rewrite = _enabled("MEMOS_MCP_ENDPOINT_REWRITE", default=True)
    report: list[tuple[str, str, str]] = []

    for key, _label, default in _ENDPOINTS:
        value = os.getenv(key)
        source = "env" if key in ambient else ".env"
        if not value:
            value = default
            source = "default"
            os.environ[key] = value

        if rewrite and key not in ambient:
            mapped = _to_loopback(value)
            if mapped is not None:
                host, port = _host_port(value, 443) or ("", 0)
                if port and _reachable("127.0.0.1", port, timeout):
                    _log(
                        f"{key}={value} targets a docker-internal host that the "
                        f"host cannot reach; using {mapped} (published loopback port)"
                    )
                    value = mapped
                    os.environ[key] = value
                    source += "+loopback"
                elif host:
                    _log(
                        f"{key}={value} targets a docker-internal host; "
                        f"127.0.0.1:{port} does not answer either. Set an explicit "
                        "endpoint in the memos-mcp row's `env` block, or start the "
                        "gateway."
                    )
        report.append((key, value, source))

    if rewrite:
        neo4j = os.getenv("NEO4J_URI")
        if neo4j:
            mapped = _to_loopback(neo4j)
            if mapped is not None:
                host, port = _host_port(neo4j, 7687) or ("", 0)
                if port and _reachable("127.0.0.1", port, timeout):
                    _log(f"NEO4J_URI={neo4j} -> {mapped} (published loopback port)")
                    os.environ["NEO4J_URI"] = mapped

    return report


def _import_server():
    """Import the MemOS MCP server, or exit 2 with an actionable message."""
    try:
        from memos.api.mcp_serve import MOSMCPServer
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing == "memos" or missing.startswith("memos."):
            _log(
                "ERROR: this python cannot import MemOS.\n"
                f"  python: {sys.executable}\n"
                "  MEMOS_PYTHON must point at the MemOS virtualenv created by\n"
                "  scripts/setup.py (e.g. <MemOS>/.venv/Scripts/python.exe on\n"
                "  Windows, <MemOS>/.venv/bin/python on POSIX)."
            )
        else:
            _log(
                f"ERROR: MemoryOS is installed but the module {missing!r} is missing.\n"
                "  Re-run scripts/setup.py --memos <MemOS> to install the "
                "remaining dependencies."
            )
        sys.exit(2)
    except Exception:  # noqa: BLE001 - any import-time failure is a config error
        _log(
            "ERROR: importing memos.api.mcp_serve failed.\n" + traceback.format_exc()
        )
        sys.exit(2)
    return MOSMCPServer


def _preflight_neo4j(timeout: float) -> bool:
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    parsed = _host_port(uri, 7687)
    if parsed is None:
        _log(
            f"WARNING: cannot parse NEO4J_URI={uri!r}; skipping the Neo4j preflight "
            "and letting MemOS report the real error."
        )
        return True
    host, port = parsed
    if _reachable(host, port, timeout):
        return True
    if _enabled("MEMOS_MCP_ALLOW_NO_NEO4J", default=False):
        _log(
            f"WARNING: Neo4j is not reachable at {uri} (MEMOS_MCP_ALLOW_NO_NEO4J is "
            "set); starting anyway, graph-backed tools will fail."
        )
        return True
    _log(
        f"ERROR: Neo4j is not reachable at {uri}.\n"
        "  Start the MemOS docker stack first:\n"
        "    docker compose -f <MemOS>/docker/docker-compose.yml up -d\n"
        "  dsh keeps retrying; once the stack is up the memory tools appear "
        "without a restart."
    )
    return False


def _preflight_gateways(timeout: float) -> list[str]:
    """Probe the LLM/embedding gateways; return the labels that are down."""
    down: list[str] = []
    for key, label, default in _ENDPOINTS[:2]:
        uri = os.getenv(key, default)
        parsed = _host_port(uri, 443)
        if parsed is None:
            _log(f"WARNING: cannot parse {key}={uri!r}; skipping its preflight.")
            continue
        host, port = parsed
        if not _reachable(host, port, timeout):
            down.append(label)
            _log(
                f"WARNING: {label} not reachable at {uri}. "
                "add_memory (plain text) works; search_memories/chat need it up."
            )
    return down


def _memos_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        for dist in ("MemoryOS", "memos"):
            try:
                return version(dist)
            except PackageNotFoundError:
                continue
    except Exception:  # noqa: BLE001 - diagnostics must never crash
        pass
    return "unknown"


def _check() -> int:
    """Print a diagnosis report (stdout: this mode is not the MCP channel)."""
    timeout = _float_env("MEMOS_MCP_PREFLIGHT_TIMEOUT", 2.0)
    _log("diagnosis report (--check)")

    rows: list[tuple[str, str]] = [
        ("python", sys.executable),
        ("cwd", os.getcwd()),
        ("MEMOS_HOME", os.getenv("MEMOS_HOME") or "(unset)"),
    ]

    env_files = [str(p) for p in _env_dirs() if (p / ".env").is_file()]
    rows.append((".env candidates", ", ".join(env_files) if env_files else "(none found)"))

    endpoints = resolve_endpoints(timeout)
    rows.append(("memos version", _memos_version()))

    for key, value, source in endpoints:
        rows.append((key, f"{value}  [{source}]"))

    neo4j = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_ok = False
    parsed = _host_port(neo4j, 7687)
    if parsed is None:
        rows.append(("NEO4J_URI", f"{neo4j}  [unparseable]"))
    else:
        neo4j_ok = _reachable(*parsed, timeout)
        rows.append(("NEO4J_URI", f"{neo4j}  [{'reachable' if neo4j_ok else 'DOWN'}]"))

    gateway_ok = True
    for key, label, default in _ENDPOINTS[:2]:
        parsed = _host_port(os.getenv(key, default), 443)
        if parsed is None:
            continue
        ok = _reachable(*parsed, timeout)
        gateway_ok = gateway_ok and ok
        rows.append((label, "reachable" if ok else "DOWN"))

    try:
        _import_server()
        rows.append(("import memos", "ok"))
        import_ok = True
    except SystemExit:
        rows.append(("import memos", "FAILED (see above)"))
        import_ok = False

    width = max(len(k) for k, _ in rows)
    print()
    for key, value in rows:
        print(f"  {key.ljust(width)}  {value}")
    print()

    hard_ok = import_ok and (neo4j_ok or _enabled("MEMOS_MCP_ALLOW_NO_NEO4J", default=False))
    if not hard_ok:
        print("RESULT: FAIL — fix the FAILED/DOWN rows above before starting dsh.")
        return 1
    if not gateway_ok:
        print("RESULT: PARTIAL — tools will mount, but search/chat need the gateway up.")
        return 0
    print("RESULT: OK — MemOS is ready for the MCP client.")
    return 0


def main(argv: list[str] | None = None) -> int:
    _safe_stderr()

    parser = argparse.ArgumentParser(
        description="Run the MemOS MCP stdio server with preflight checks.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print a diagnosis report and exit (nothing is served)",
    )
    args = parser.parse_args(argv)

    if args.check:
        return _check()

    timeout = _float_env("MEMOS_MCP_PREFLIGHT_TIMEOUT", 2.0)
    endpoints = resolve_endpoints(timeout)

    if _enabled("MEMOS_MCP_DEBUG", default=False):
        _log(f"python={sys.executable} cwd={os.getcwd()} memos={_memos_version()}")
        for key, value, source in endpoints:
            _log(f"{key}={value} [{source}]")

    MOSMCPServer = _import_server()

    if _enabled("MEMOS_MCP_PREFLIGHT", default=True):
        if not _preflight_neo4j(timeout):
            return 1
        _preflight_gateways(timeout)

    try:
        server = MOSMCPServer()
    except Exception:  # noqa: BLE001 - surface any init failure as one block
        _log(
            "ERROR: MemOS MOS failed to initialize. "
            "Check that Neo4j/Qdrant are up and .env is configured.\n"
            + traceback.format_exc()
        )
        return 1

    try:
        server.run(transport="stdio")
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001 - a crash mid-session must be logged
        _log("ERROR: the MemOS MCP server stopped with an exception.\n" + traceback.format_exc())
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
