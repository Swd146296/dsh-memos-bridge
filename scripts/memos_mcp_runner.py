"""Launch the MemOS MCP server with startup preflight checks.

Runs the same MCP stdio server as ``python -m memos.api.mcp_serve``, but first
checks the hard dependency (Neo4j) and the soft dependencies (LLM / embedding
gateway) so a downed infrastructure stack fails with one clear, actionable
line instead of a wall of connection tracebacks.

Hard dependency (fatal):  Neo4j      - the MemOS graph store; the MOS core
                                       constructs it eagerly at startup.
Soft dependency (warn):   LLM/embed  - only search/chat tools need them;
                                       add_memory (plain text) still works.

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
  MEMOS_MCP_DEBUG       set to 1 to echo the resolved python / MemOS path
"""

from __future__ import annotations

import os
import socket
import sys
import traceback
from pathlib import Path
from urllib.parse import urlparse

LOG = "[dsh-memos-bridge]"

DEFAULT_OPENAI_API_BASE = "http://127.0.0.1:18181/v1"
DEFAULT_EMBEDDER_API_BASE = "http://127.0.0.1:18182/compatible-mode/v1"

# Endpoint defaults must be set BEFORE dotenv loads: on a host-run deployment
# the .env targets host.docker.internal (valid inside the MemOS docker network
# but unreliable from the host), while the gateway is published on the host
# loopback. dotenv(override=False) keeps our defaults; the dsh-mcp-client `env`
# block overrides them when set.
_ENDPOINT_DEFAULTS = {
    "OPENAI_API_BASE": DEFAULT_OPENAI_API_BASE,
    "MOS_EMBEDDER_API_BASE": DEFAULT_EMBEDDER_API_BASE,
    "MEMRADER_API_BASE": DEFAULT_OPENAI_API_BASE,
    "QWEN_API_BASE": DEFAULT_OPENAI_API_BASE,
}

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
            parsed = urlparse(raw)
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


def _load_env_files() -> None:
    """Load MemOS ``.env`` from MEMOS_HOME / cwd without overriding ambient env.

    ``load_dotenv()`` without a path resolves relative to *this file*, i.e. the
    plugin checkout -- never the MemOS repo. That silently made the preflight
    read NEO4J_URI from nowhere and fall back to ``bolt://localhost:7687``.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    seen: set[str] = set()
    for directory in _env_dirs():
        candidate = directory / ".env"
        key = str(candidate)
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        try:
            load_dotenv(candidate, override=False)
        except (OSError, ValueError) as exc:  # unreadable / malformed .env
            _log(f"WARNING: could not read {candidate}: {exc}")


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


def _preflight_gateways(timeout: float) -> None:
    for label, key, default in (
        ("LLM gateway", "OPENAI_API_BASE", DEFAULT_OPENAI_API_BASE),
        ("embedding gateway", "MOS_EMBEDDER_API_BASE", DEFAULT_EMBEDDER_API_BASE),
    ):
        uri = os.getenv(key, default)
        parsed = _host_port(uri, 443)
        if parsed is None:
            _log(f"WARNING: cannot parse {key}={uri!r}; skipping its preflight.")
            continue
        host, port = parsed
        if not _reachable(host, port, timeout):
            _log(
                f"WARNING: {label} not reachable at {uri}. "
                "add_memory (plain text) works; search_memories/chat need it up."
            )


def main() -> int:
    for key, value in _ENDPOINT_DEFAULTS.items():
        os.environ.setdefault(key, value)

    _load_env_files()

    if _enabled("MEMOS_MCP_DEBUG", default=False):
        _log(f"python={sys.executable} cwd={os.getcwd()}")

    MOSMCPServer = _import_server()

    if _enabled("MEMOS_MCP_PREFLIGHT", default=True):
        timeout = _float_env("MEMOS_MCP_PREFLIGHT_TIMEOUT", 2.0)
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
