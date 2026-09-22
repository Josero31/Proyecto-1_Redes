"""
Minimal manual MCP client (no MCP SDK), implemented directly with
JSON-RPC 2.0, matching the protocol used by our own pharmacy servers
(server.py / remote_server.py) and by the official reference MCP
servers (Filesystem, Git).

Two transports are implemented:
  - StdioMCPClient: spawns a server as a subprocess and speaks
    newline-delimited JSON-RPC over its stdin/stdout. Used for the
    local pharmacy server and the official Filesystem/Git servers.
  - HttpMCPClient: speaks JSON-RPC 2.0 over HTTP POST to a single
    /mcp endpoint. Used for the remote pharmacy server.

Every request sent and every response received is written to a
per-server log file so the interaction between the host and each MCP
server can be inspected (required by the project for the chatbot's
MCP interaction log).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import itertools
import urllib.request
import urllib.error
from pathlib import Path


LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

_id_counter = itertools.count(1)


class MCPError(RuntimeError):
    pass


class BaseMCPClient:
    """Common handshake + tool-call logic shared by both transports."""

    def __init__(self, name: str):
        self.name = name
        self.tools: list[dict] = []
        self._log_path = LOG_DIR / f"mcp_{name}.log"
        self._log_lock = threading.Lock()

    def _log(self, direction: str, payload: dict) -> None:
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {direction} {json.dumps(payload, ensure_ascii=False)}\n"
        with self._log_lock:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(line)

    def _next_id(self) -> int:
        return next(_id_counter)

    # -- transport-specific, implemented by subclasses -------------------
    def _send_request(self, msg: dict) -> dict:
        raise NotImplementedError

    def _send_notification(self, msg: dict) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    # -- MCP protocol, shared ---------------------------------------------
    def initialize(self) -> dict:
        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pharmacy-chatbot-host", "version": "1.0.0"},
            },
        }
        result = self._send_request(req)
        self._send_notification({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self) -> list[dict]:
        req = {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}}
        result = self._send_request(req)
        self.tools = result.get("tools", [])
        return self.tools

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        req = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        return self._send_request(req)


class StdioMCPClient(BaseMCPClient):
    """Spawns the MCP server as a subprocess; talks JSON-RPC over its
    stdin/stdout, one message per line (matches server.py's transport)."""

    def __init__(self, name: str, command: list[str], cwd: str | None = None, env: dict | None = None):
        super().__init__(name)
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        self._proc = subprocess.Popen(
            command,
            cwd=cwd,
            env=full_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._lock = threading.Lock()

    def _write_line(self, msg: dict) -> None:
        line = json.dumps(msg, ensure_ascii=False)
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _read_line(self) -> dict:
        line = self._proc.stdout.readline()
        if not line:
            stderr = self._proc.stderr.read()
            raise MCPError(f"MCP server '{self.name}' closed its stdout unexpectedly. stderr:\n{stderr}")
        return json.loads(line)

    def _send_request(self, msg: dict) -> dict:
        with self._lock:
            self._log(">>", msg)
            self._write_line(msg)
            response = self._read_line()
            self._log("<<", response)
        if "error" in response:
            raise MCPError(f"[{self.name}] {response['error'].get('message')}")
        return response.get("result", {})

    def _send_notification(self, msg: dict) -> None:
        with self._lock:
            self._log(">>", msg)
            self._write_line(msg)

    def close(self) -> None:
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        self._proc.terminate()


class HttpMCPClient(BaseMCPClient):
    """Speaks JSON-RPC 2.0 over HTTP POST to a remote MCP server's
    /mcp endpoint (matches remote_server.py's transport)."""

    def __init__(self, name: str, url: str, timeout: float = 15.0):
        super().__init__(name)
        self._url = url
        self._timeout = timeout
        self._session_id: str | None = None

    def _post(self, msg: dict) -> tuple[int, dict | None]:
        body = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = urllib.request.Request(self._url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:
                session_id = resp.headers.get("Mcp-Session-Id")
                if session_id:
                    self._session_id = session_id
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            return e.code, (json.loads(raw) if raw else None)

    def _send_request(self, msg: dict) -> dict:
        self._log(">>", msg)
        status, response = self._post(msg)
        if response is not None:
            self._log("<<", response)
        if response is None or "error" in response:
            err = response["error"].get("message") if response else f"HTTP {status}"
            raise MCPError(f"[{self.name}] {err}")
        return response.get("result", {})

    def _send_notification(self, msg: dict) -> None:
        self._log(">>", msg)
        self._post(msg)

    def close(self) -> None:
        pass
