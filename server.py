#!/usr/bin/env python3
"""
Pharmacy Chatbot MCP Server (local / stdio transport)
======================================================

A local Model Context Protocol (MCP) server implemented manually using
JSON-RPC 2.0 over stdio, WITHOUT any MCP SDK (per CC3067 project requirements).

Use case: a pharmacy chain chatbot that, based on a customer's described
symptoms, recommends over-the-counter (OTC) medications, checks stock,
and lets the customer place an order. Medications that require a
prescription can NEVER be auto-ordered by this server -- they are flagged
so the chatbot can tell the customer to visit the pharmacy in person.

Transport: stdio (newline-delimited JSON-RPC messages)
Protocol version implemented: 2025-06-18 (subset needed for tools)

Supported JSON-RPC methods:
  - initialize                 (request)
  - notifications/initialized  (notification, no response)
  - tools/list                 (request)
  - tools/call                 (request)
  - ping                       (request)

The business logic, tool registry, and JSON-RPC message handling are
shared with the remote (HTTP) transport in pharmacy_logic.py /
remote_server.py, so both servers behave identically -- only the
transport differs.
"""

import sys
import datetime

from pharmacy_logic import process_message_raw


def main():
    # Force UTF-8 on stdin/stdout regardless of the OS console codepage
    # (important on Windows, where the default may not be UTF-8).
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

    log = open("server.log", "a", encoding="utf-8")
    log.write(f"\n--- server started {datetime.datetime.now().isoformat()} ---\n")
    log.flush()

    for line in sys.stdin:
        # Strip a UTF-8 BOM if present (Windows/PowerShell often prepends
        # one to the first line of a text stream) plus surrounding whitespace.
        line = line.lstrip("﻿").strip()
        if not line:
            continue

        log.write(f">> {line}\n")
        log.flush()

        response = process_message_raw(line)

        if response is not None:
            import json
            out = json.dumps(response, ensure_ascii=False)
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
            log.write(f"<< {out}\n")
            log.flush()


if __name__ == "__main__":
    main()
