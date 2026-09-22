#!/usr/bin/env python3
"""
Pharmacy Chatbot MCP Server (remote / Streamable HTTP transport)
==================================================================

The exact same MCP server as server.py (same tools, same in-memory data,
same JSON-RPC 2.0 message handling — imported from pharmacy_logic.py),
but exposed over HTTP instead of stdio, so it can be deployed to a cloud
platform (Google Cloud Run, Render, Fly.io, etc.) and reached over the
network by a remote MCP host/client.

Implemented manually with Python's standard library only (http.server) —
no MCP SDK, no web framework — per CC3067 project requirements.

Transport (simplified MCP "Streamable HTTP"):
  POST /mcp    -> body is a single JSON-RPC 2.0 message (request or
                  notification). A request gets back a JSON-RPC response
                  in the HTTP body (Content-Type: application/json).
                  A notification gets back an empty 202 Accepted.
  GET  /health -> plain-text liveness check (used by the cloud platform's
                  health checks; not part of MCP).

The server listens on the port given by the PORT environment variable
(Cloud Run injects this), defaulting to 8080 for local testing.
"""

import os
import json
import datetime
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pharmacy_logic import process_message

LOG_PATH = os.environ.get("MCP_LOG_PATH", "remote_server.log")
_log_lock_file = open(LOG_PATH, "a", encoding="utf-8")


def log(line: str) -> None:
    _log_lock_file.write(line + "\n")
    _log_lock_file.flush()


class MCPRequestHandler(BaseHTTPRequestHandler):
    server_version = "PharmacyMCP/1.0"

    def log_message(self, fmt, *args):
        # Redirect the default stderr access log into our own log file
        # instead of printing to stderr (keeps container logs consistent).
        log(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def _send_json(self, status: int, payload: dict | None, session_id: str | None = None):
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if session_id:
            self.send_header("Mcp-Session-Id", session_id)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/mcp":
            self._send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length > 0 else b""

        try:
            msg = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(
                400,
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "Invalid JSON."}},
            )
            return

        log(f">> {json.dumps(msg, ensure_ascii=False)}")

        session_id = self.headers.get("Mcp-Session-Id")
        is_initialize = isinstance(msg, dict) and msg.get("method") == "initialize"
        if is_initialize and not session_id:
            session_id = str(uuid.uuid4())

        response = process_message(msg)

        if response is None:
            # Notification: no JSON-RPC response body, per spec.
            self._send_json(202, None, session_id=session_id)
            return

        log(f"<< {json.dumps(response, ensure_ascii=False)}")
        self._send_json(200, response, session_id=session_id)


def main():
    port = int(os.environ.get("PORT", 8080))
    host = "0.0.0.0"
    log(f"\n--- remote server started {datetime.datetime.now().isoformat()} on {host}:{port} ---")
    httpd = ThreadingHTTPServer((host, port), MCPRequestHandler)
    print(f"Pharmacy MCP remote server listening on http://{host}:{port}/mcp")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
