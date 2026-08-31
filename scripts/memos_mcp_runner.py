"""Launch the MemOS MCP server with startup preflight checks.

Runs the same MCP stdio server as `python -m memos.api.mcp_serve`, but first
checks the hard dependency (Neo4j) and the soft dependencies (LLM / embedding
gateway) so a downed infrastructure stack fails with one clear, actionable
line instead of a wall of connection tracebacks.

Hard dependency (fatal):  Neo4j      - the MemOS graph store; the MOS core
                                       constructs it eagerly at startup.
Soft dependency (warn):   LLM/embed  - only search/chat tools need them;
                                       add_memory (plain text) still works.

Exit code 1 (fast) when Neo4j is down; the dsh mcp-client reconnect policy
retries the child, so starting the docker stack later recovers automatically.
"""

import os
import socket
import sys


def _reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _host_port(uri: str, default_port: int) -> tuple[str, int]:
    rest = uri.split("://", 1)[-1].split("/", 1)[0]
    if ":" in rest:
        host, port = rest.rsplit(":", 1)
        return host, int(port)
    return rest, default_port


def main() -> int:
    # Endpoint defaults must be set BEFORE dotenv loads: on a host-run
    # deployment the .env targets host.docker.internal (valid inside the
    # MemOS docker network but unreliable from the host), while the gateway
    # is published on the host loopback. dotenv(override=False) keeps our
    # defaults; the dsh-mcp-client `env` block overrides them when set.
    os.environ.setdefault("OPENAI_API_BASE", "http://127.0.0.1:18181/v1")
    os.environ.setdefault("MOS_EMBEDDER_API_BASE", "http://127.0.0.1:18182/compatible-mode/v1")
    os.environ.setdefault("MEMRADER_API_BASE", "http://127.0.0.1:18181/v1")
    os.environ.setdefault("QWEN_API_BASE", "http://127.0.0.1:18181/v1")

    # Ambient env (from the dsh-mcp-client env block) wins; .env fills gaps.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    # --- Hard dependency: Neo4j -------------------------------------------------
    neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    host, port = _host_port(neo4j_uri, 7687)
    if not _reachable(host, port):
        print(
            f"[dsh-memos-bridge] ERROR: Neo4j is not reachable at {neo4j_uri}. "
            "Start the MemOS docker stack first:\n"
            "  docker compose -f <MemOS>/docker/docker-compose.yml up -d\n"
            "dsh will keep retrying; once the stack is up the memory tools "
            "appear without a restart.",
            file=sys.stderr,
        )
        return 1

    # --- Soft dependencies: LLM / embedding gateway ----------------------------
    for label, key, default in [
        ("LLM gateway", "OPENAI_API_BASE", "http://127.0.0.1:18181/v1"),
        ("embedding gateway", "MOS_EMBEDDER_API_BASE", "http://127.0.0.1:18182/compatible-mode/v1"),
    ]:
        uri = os.getenv(key, default)
        ghost, gport = _host_port(uri, 443)
        if not _reachable(ghost, gport):
            print(
                f"[dsh-memos-bridge] WARNING: {label} not reachable at {uri}. "
                "add_memory (plain text) works; search_memories/chat need it up.",
                file=sys.stderr,
            )

    # --- Start the server --------------------------------------------------------
    from memos.api.mcp_serve import MOSMCPServer

    try:
        server = MOSMCPServer()
    except Exception:
        import traceback

        print(
            "[dsh-memos-bridge] ERROR: MemOS MOS failed to initialize. "
            "Check that Neo4j/Qdrant are up and .env is configured.\n"
            + traceback.format_exc(),
            file=sys.stderr,
        )
        return 1

    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
