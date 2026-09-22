#!/usr/bin/env python3
"""
Pharmacy Chatbot — MCP Host
============================

The "host" in MCP terms: a command-line chatbot that talks to an LLM
(Groq's free API, OpenAI-compatible tool-calling format) and gives it
tools backed by one or more MCP servers (configured in
servers_config.json):

  - pharmacy-local   the local MCP server from server.py (stdio)
  - pharmacy-remote  the same server deployed to the cloud (HTTP)
  - filesystem       the official MCP Filesystem reference server
  - git              the official MCP Git reference server

It keeps the full conversation history in memory for the session (so
the model has context across turns) and logs every request/response it
exchanges with each MCP server (see chatbot/logs/) plus a transcript of
the conversation itself.

Run:  python chatbot.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

from mcp_client import StdioMCPClient, HttpMCPClient, MCPError

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
CONVERSATION_LOG = LOG_DIR / "conversation.log"

MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_TOKENS = 1024
SYSTEM_PROMPT = (
    "You are the customer-facing chatbot for a pharmacy chain. You have "
    "access to tools exposed by one or more MCP servers (pharmacy "
    "lookup/ordering, and possibly filesystem/git tools). Use the "
    "pharmacy tools to answer medication questions, check stock, and "
    "place orders. Never invent stock, prices, or order IDs — always "
    "call the appropriate tool. If a tool result includes a medical "
    "disclaimer, pass its meaning along to the user in your own words."
)


def log_conversation(role: str, text: str) -> None:
    with open(CONVERSATION_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {role}: {text}\n")


def load_servers() -> dict:
    """Read servers_config.json, connect to every server with
    enabled: true, and return {tool_name: (client, original_tool_name)}
    plus the OpenAI/Groq-format tool list."""
    config_path = BASE_DIR / "servers_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    clients = []
    tool_routes = {}
    groq_tools = []

    for server_cfg in config["servers"]:
        if not server_cfg.get("enabled", False):
            continue

        name = server_cfg["name"]
        transport = server_cfg["transport"]
        print(f"Connecting to MCP server '{name}' ({transport})...")

        try:
            if transport == "stdio":
                client = StdioMCPClient(name, server_cfg["command"], cwd=str(BASE_DIR))
            elif transport == "http":
                client = HttpMCPClient(name, server_cfg["url"])
            else:
                raise ValueError(f"Unknown transport '{transport}' for server '{name}'")

            client.initialize()
            tools = client.list_tools()
        except (MCPError, OSError, FileNotFoundError) as e:
            print(f"  !! Could not connect to '{name}': {e}. Skipping.")
            continue

        clients.append(client)
        print(f"  -> {len(tools)} tool(s): {', '.join(t['name'] for t in tools)}")

        for tool in tools:
            # Prefix with server name to keep tool names globally unique
            # across servers (e.g. two servers could both expose "search").
            routed_name = f"{name}__{tool['name']}"
            tool_routes[routed_name] = (client, tool["name"])
            groq_tools.append({
                "type": "function",
                "function": {
                    "name": routed_name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                },
            })

    return {"clients": clients, "tool_routes": tool_routes, "groq_tools": groq_tools}


def run_tool(tool_routes: dict, routed_name: str, arguments: dict) -> str:
    if routed_name not in tool_routes:
        return json.dumps({"error": f"Unknown tool '{routed_name}'"})

    client, real_name = tool_routes[routed_name]
    try:
        result = client.call_tool(real_name, arguments)
    except MCPError as e:
        return json.dumps({"error": str(e)})

    content = result.get("content", [])
    text_parts = [c["text"] for c in content if c.get("type") == "text"]
    return "\n".join(text_parts) if text_parts else json.dumps(result)


def main():
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("ERROR: set GROQ_API_KEY in chatbot/.env (see .env.example). "
              "Get a free key at https://console.groq.com/keys")
        sys.exit(1)

    groq_client = Groq(api_key=api_key)
    registry = load_servers()
    tool_routes = registry["tool_routes"]
    groq_tools = registry["groq_tools"]
    clients = registry["clients"]

    if not clients:
        print("No MCP servers connected. Check servers_config.json.")
        sys.exit(1)

    print(f"\nReady. {len(groq_tools)} tool(s) available. Type 'exit' to quit.\n")

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    try:
        while True:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                break

            log_conversation("user", user_input)
            messages.append({"role": "user", "content": user_input})

            # Agentic loop: keep calling the model and running any tools
            # it requests, until it responds without a tool call.
            while True:
                response = groq_client.chat.completions.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    messages=messages,
                    tools=groq_tools,
                    tool_choice="auto",
                )

                message = response.choices[0].message
                tool_calls = message.tool_calls or []

                assistant_msg = {"role": "assistant", "content": message.content}
                if tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ]
                messages.append(assistant_msg)

                if not tool_calls:
                    final_text = message.content or ""
                    print(f"\nBot: {final_text}\n")
                    log_conversation("assistant", final_text)
                    break

                for tool_call in tool_calls:
                    arguments = json.loads(tool_call.function.arguments or "{}")
                    print(f"  [tool call] {tool_call.function.name}({json.dumps(arguments, ensure_ascii=False)})")
                    output = run_tool(tool_routes, tool_call.function.name, arguments)
                    log_conversation("tool", f"{tool_call.function.name} -> {output}")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": output,
                    })

    except (KeyboardInterrupt, EOFError):
        print("\nBye.")
    finally:
        for client in clients:
            client.close()


if __name__ == "__main__":
    main()
