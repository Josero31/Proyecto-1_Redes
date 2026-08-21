# Pharmacy Chatbot MCP Server

A local **Model Context Protocol (MCP)** server for a pharmacy chain chatbot,
implemented **manually** using raw **JSON-RPC 2.0** messages over the
**stdio transport** — no MCP SDK (e.g. FastMCP) was used, per course
requirements (CC3067 Redes, Universidad del Valle de Guatemala).

The server exposes 6 tools that let an LLM-powered chatbot (the MCP **host**)
recommend over-the-counter (OTC) medications based on a customer's symptoms,
check stock, place orders, and look up order history.

## Industry use case

A pharmacy chain offers a chatbot so customers can describe their symptoms
and get a recommendation for an over-the-counter medication, check its price
and availability, and order it for pickup — without needing to call or visit
in person first. The chatbot (host + client) talks to this MCP server, which
owns the actual business logic and data (medications, stock, orders).

**Safety boundary:** any medication that requires a prescription
(`requires_prescription: true`) can be looked up and described, but the
server refuses to create an order for it — the customer is told to visit the
pharmacy in person with their prescription. This logic lives in the server,
not in the LLM, so it cannot be bypassed by prompting.

> **Disclaimer:** All tool responses that involve a medical recommendation
> include a `disclaimer` field stating this does not replace professional
> medical advice. This is a class project simulating a pharmacy chatbot; it
> is not a real medical or pharmaceutical product.

## 1. Architecture

```
┌─────────────┐        JSON-RPC 2.0        ┌──────────────────────┐
│  MCP Host   │ ───────  over stdio  ────── │  server.py           │
│ (chatbot)   │ ◄────────────────────────── │  (this MCP server)   │
└─────────────┘         stdin/stdout        └──────────────────────┘
```

- **Transport**: stdio. The host spawns `python3 server.py` as a subprocess
  and communicates by writing one JSON-RPC message per line to the process's
  `stdin`, and reading one JSON-RPC message per line from its `stdout`.
- **Protocol version**: `2025-06-18`.
- **State**: kept in memory for the lifetime of the process (medications,
  stock, orders). Every request/response pair is also appended to
  `server.log` for debugging.

## 2. Requirements

- Python 3.10+ (standard library only — no external dependencies).

## 3. Installation

```bash
git clone <your-repo-url>
cd pharmacy-mcp-server
# No dependencies to install — uses only the Python standard library.
```

## 4. Running the server

The server is not meant to be run interactively by a human — it is meant to
be spawned as a subprocess by an MCP host. To run it directly for testing:

```bash
python3 server.py
```

It will then block, waiting for JSON-RPC messages on stdin, one per line.
Every message it receives and every response it sends is appended to
`server.log`.

### Configuring it in an MCP host (e.g. Claude Desktop)

Add to your host's MCP config (example for Claude Desktop's
`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "pharmacy-chatbot": {
      "command": "python3",
      "args": ["/absolute/path/to/pharmacy-mcp-server/server.py"]
    }
  }
}
```

## 5. Protocol specification

### 5.1 `initialize` (request)

**Request**
```json
{"jsonrpc": "2.0", "id": 1, "method": "initialize",
 "params": {"protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1.0"}}}
```

**Response**
```json
{"jsonrpc": "2.0", "id": 1,
 "result": {"protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "pharmacy-chatbot-server", "version": "1.0.0"}}}
```

### 5.2 `notifications/initialized` (notification — no response)

```json
{"jsonrpc": "2.0", "method": "notifications/initialized"}
```

### 5.3 `tools/list` (request)

Returns the list of tools with their JSON Schema `inputSchema`. See section 6
below for the full list.

### 5.4 `tools/call` (request)

**Request**
```json
{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
 "params": {"name": "<tool_name>", "arguments": {"...": "..."}}}
```

**Response (success)**
```json
{"jsonrpc": "2.0", "id": 3,
 "result": {"content": [{"type": "text", "text": "<JSON-encoded result>"}],
            "isError": false}}
```

**Response (business-logic error, e.g. prescription required, no stock)**
```json
{"jsonrpc": "2.0", "id": 8,
 "result": {"content": [{"type": "text",
   "text": "'Amoxicilina 500mg' requires a medical prescription and cannot be ordered through the chatbot. Please visit the pharmacy in person with your prescription."}],
   "isError": true}}
```

**Response (protocol-level error, e.g. unknown method)**
```json
{"jsonrpc": "2.0", "id": 3,
 "error": {"code": -32601, "message": "Method 'foo' not found."}}
```

### 5.5 `ping` (request)

Simple liveness check. Returns `{"jsonrpc": "2.0", "id": <id>, "result": {}}`.

### 5.6 Error codes used

| Code    | Meaning                                  |
|---------|-------------------------------------------|
| -32700  | Parse error (invalid JSON)                |
| -32600  | Invalid JSON-RPC request                  |
| -32601  | Method not found                          |
| -32602  | Invalid params (e.g. missing tool name)   |
| -32603  | Internal server error                     |

## 6. Tools

### `search_medication_by_symptom`
Searches OTC medications recommended for a described symptom.

| Param | Type | Required | Description |
|---|---|---|---|
| `symptom` | string | yes | e.g. `"dolor de cabeza"`, `"fiebre"`, `"alergia"` |

**Example call**
```json
{"name": "search_medication_by_symptom", "arguments": {"symptom": "dolor de cabeza"}}
```

### `get_medication_details`
Full details of a medication: dosage, contraindications, price, whether it
requires a prescription.

| Param | Type | Required | Description |
|---|---|---|---|
| `medication_id` | string | yes | e.g. `"MED1"` |

### `check_stock`
Checks available stock and price for a medication.

| Param | Type | Required | Description |
|---|---|---|---|
| `medication_id` | string | yes | e.g. `"MED1"` |

### `create_order`
Creates an order for an OTC medication. **Fails** if the medication requires
a prescription, or if there is insufficient stock.

| Param | Type | Required | Description |
|---|---|---|---|
| `customer_name` | string | yes | Customer's full name |
| `phone` | string | yes | Used as the customer's identifier |
| `medication_id` | string | yes | e.g. `"MED1"` |
| `quantity` | integer | yes | Units to order, ≥ 1 |

**Example call**
```json
{"name": "create_order",
 "arguments": {"customer_name": "Maria Lopez", "phone": "5555-9999",
               "medication_id": "MED1", "quantity": 2}}
```

### `get_order_status`

| Param | Type | Required | Description |
|---|---|---|---|
| `order_id` | string | yes | ID returned by `create_order` |

### `list_customer_orders`

| Param | Type | Required | Description |
|---|---|---|---|
| `phone` | string | yes | Customer phone number |

## 7. Manual testing

A sample batch of JSON-RPC messages is included in `test_requests.jsonl`.
Run:

```bash
python3 server.py < test_requests.jsonl
```

This exercises `initialize`, `tools/list`, all 6 tools, and the following
error cases: attempting to order a prescription-only medication
(`MED8`, Amoxicillin), an invalid order ID, and a symptom with no mapped
medication.

## 8. Project status

This is the **local MCP server** deliverable (partial submission). It is not
yet wired into a chatbot host — that integration, plus the remote version of
this same server, are part of later phases of this project.
