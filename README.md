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
                           stdio (subprocess)
                    ┌──────────────────────────────┐
                    │                               ▼
┌───────────────────┴───┐   JSON-RPC 2.0    ┌───────────────────┐
│  chatbot/chatbot.py    │ ── over stdio ──► │  server.py        │
│  (MCP host + LLM       │ ◄──────────────── │  pharmacy-local   │
│   client, talks to     │                   └───────────────────┘
│   Groq's API)          │
│                        │   JSON-RPC 2.0    ┌───────────────────┐
│                        │ ── over HTTP ────►│  remote_server.py │
│                        │ ◄──────────────── │  pharmacy-remote  │
│                        │   POST /mcp       │  (deployed to a   │
│                        │                   │   cloud platform) │
│                        │                   └───────────────────┘
│                        │   JSON-RPC 2.0    ┌───────────────────┐
│                        │ ── over stdio ────► official Filesystem│
│                        │ ◄──────────────── │ / Git MCP servers │
└────────────────────────┘                   └───────────────────┘
```

Both `server.py` (local) and `remote_server.py` (remote) import their tools,
data, and JSON-RPC message handling from **`pharmacy_logic.py`** — the two
servers are functionally identical; only the transport differs:

- **`server.py`** — stdio transport. The host spawns `python server.py` as a
  subprocess and communicates by writing one JSON-RPC message per line to
  the process's `stdin`, and reading one JSON-RPC message per line from its
  `stdout`.
- **`remote_server.py`** — HTTP transport (`POST /mcp`), meant to be deployed
  to a cloud platform so a remote host can reach it over the network. See
  section 9.
- **Protocol version**: `2025-06-18`.
- **State**: kept in memory for the lifetime of the process (medications,
  stock, orders). Every request/response pair is also appended to a log
  file (`server.log` / `remote_server.log`) for debugging.

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

## 8. Shared logic module (`pharmacy_logic.py`)

`pharmacy_logic.py` contains the tools, seed data, and JSON-RPC message
handling used by **both** `server.py` and `remote_server.py`. If you modify
a tool's behavior, edit it there once and both transports pick it up.

## 9. Remote MCP server (`remote_server.py`)

The same pharmacy tools, deployed so they can be reached over the network
instead of only as a local subprocess. Implemented manually with Python's
standard library `http.server` — no web framework, no MCP SDK.

### 9.1 Transport

Simplified MCP **Streamable HTTP**:

| Endpoint | Method | Purpose |
|---|---|---|
| `/mcp` | `POST` | Body = one JSON-RPC 2.0 message (request or notification). A request gets a JSON-RPC response back in the body. A notification gets an empty `202 Accepted`. |
| `/health` | `GET` | Plain liveness check for the hosting platform (not part of MCP). |

The server reads the port to listen on from the `PORT` environment variable
(defaults to `8080`), which is what Cloud Run (and most PaaS providers)
expect. On `initialize`, the server issues an `Mcp-Session-Id` response
header; if the client sends it back on later requests it is accepted, but
it isn't required (this server's state isn't partitioned per-session — same
single in-memory store as the local server).

### 9.2 Running it locally

```bash
python remote_server.py
# -> Pharmacy MCP remote server listening on http://0.0.0.0:8080/mcp

curl http://localhost:8080/health
curl -X POST http://localhost:8080/mcp -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

### 9.3 Deploying it for free (Render)

[Render](https://render.com) has a genuinely free web service tier — no
credit card required, you sign up with your GitHub account, and it builds
straight from the `Dockerfile` already in this repo.

1. Push this repo to GitHub if you haven't (`git push`, section 3).
2. Go to [dashboard.render.com](https://dashboard.render.com) → sign up /
   log in with GitHub.
3. **New** → **Web Service** → pick your `Proyecto-1_Redes` repo → grant
   Render access if prompted.
4. Render should auto-detect the `Dockerfile`. If asked:
   - **Runtime**: Docker
   - **Instance Type**: **Free**
   - Leave the build/start command blank (the `Dockerfile`'s `CMD` handles
     it) and don't set a `PORT` env var yourself — Render injects its own
     `PORT`, and `remote_server.py` already reads it from the environment.
5. Click **Create Web Service**. First build/deploy takes a couple of
   minutes; Render shows you the public URL, e.g.
   `https://pharmacy-mcp-remote.onrender.com`.
6. Verify it's up: `curl https://<your-app>.onrender.com/health`.
7. Put `https://<your-app>.onrender.com/mcp` in
   `chatbot/servers_config.json` under `pharmacy-remote.url`, set
   `"enabled": true`.

> **Free-tier quirk**: the service spins down after ~15 minutes of no
> traffic and takes 30-50s to wake up on the next request — normal for the
> free plan, just send one request and wait before your demo/Wireshark
> capture so it's already warm.

> Google Cloud Run (also has a free tier, but requires a Google Cloud
> account with billing verification) or any other platform that runs a
> Dockerfile and reads `PORT` from the environment (Fly.io, Railway, etc.)
> works the same way — just point `pharmacy-remote.url` at wherever it ends
> up.

### 9.4 Example usage

Same 6 tools as the local server (section 6) — `search_medication_by_symptom`,
`get_medication_details`, `check_stock`, `create_order`, `get_order_status`,
`list_customer_orders` — same parameters, same responses, just called over
HTTP instead of stdio.

## 10. Chatbot (MCP host) — `chatbot/`

A command-line chatbot that connects an LLM to one or more MCP servers and
lets the model call their tools. This is the **host** in MCP terminology: it
owns the connection to the LLM, discovers tools from each configured MCP
server, and routes the model's tool calls to the right server.

The LLM is [Groq](https://groq.com)'s API — **free**, no credit card
required, OpenAI-compatible tool-calling format, and fast (runs open models
like OpenAI's open-weight `gpt-oss` on their own inference hardware — check
`groq_client.models.list()` for the current catalog, it changes over time).

Implemented manually: `chatbot/mcp_client.py` is a small JSON-RPC 2.0 client
(stdio and HTTP transports) written directly against the protocol, not an
MCP SDK.

### 10.1 Setup

```bash
cd chatbot
python -m venv .venv
./.venv/Scripts/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env            # then edit .env and paste your key
```

Get a free Groq API key (no card needed) at
[console.groq.com/keys](https://console.groq.com/keys) — sign in, click
"Create API Key". Put it in `chatbot/.env`:

```
GROQ_API_KEY=gsk_...
GROQ_MODEL=openai/gpt-oss-120b
```

### 10.2 Configuring which MCP servers to use

Edit `chatbot/servers_config.json`. Each server has `enabled: true/false`:

```json
{
  "name": "pharmacy-local",
  "enabled": true,
  "transport": "stdio",
  "command": ["python", "../server.py"]
}
```

By default only `pharmacy-local` is enabled. To also use the remote server,
set `pharmacy-remote.enabled` to `true` and fill in its `url` (section 9.3).
For the official Filesystem/Git servers, see section 11.

### 10.3 Running it

```bash
python chatbot.py
```

```
Connecting to MCP server 'pharmacy-local' (stdio)...
  -> 6 tool(s): search_medication_by_symptom, get_medication_details, ...

Ready. 6 tool(s) available. Type 'exit' to quit.

You: tengo dolor de cabeza, que me recomiendas?
  [tool call] pharmacy-local__search_medication_by_symptom({"symptom": "dolor de cabeza"})

Bot: Te recomiendo Paracetamol 500mg o Ibuprofeno 400mg, ambos disponibles
sin receta. Recuerda que esto no reemplaza el consejo de un médico...
```

### 10.4 What gets logged

- `chatbot/logs/conversation.log` — every user message, assistant reply, and
  tool result, timestamped.
- `chatbot/logs/mcp_<server-name>.log` — every raw JSON-RPC message sent to
  and received from that specific MCP server (one file per server). This is
  the log referenced by functionality #3 of the project (log of all MCP
  server interactions).

## 11. Official MCP servers (Filesystem, Git)

The chatbot can also drive Anthropic's official reference MCP servers,
unmodified, through the same `chatbot/mcp_client.py`.

Both are already enabled by default in `servers_config.json`. Their
`command` uses the literal string `"{python}"` as the interpreter —
`chatbot.py` substitutes that for `sys.executable` (the interpreter
actually running the chatbot) before spawning them, so they pick up
packages installed in `chatbot/.venv` even if you never ran `activate`.
(`pharmacy-local`'s command does the same, for the same reason.)

### 11.1 Filesystem server

Requires Node.js (`npx` comes with it). No install step needed —
`npx -y @modelcontextprotocol/server-filesystem <dir>` fetches and runs it
on first use, scoped to `chatbot/workspace/` (create that folder first —
this is also the Filesystem server's sandbox: it refuses to touch anything
outside it).

> **Windows note:** `npx` is actually `npx.cmd`, which plain
> `subprocess.Popen` can't resolve. `mcp_client.py`'s `StdioMCPClient`
> already works around this with `shutil.which()` — nothing you need to do,
> just don't remove that if you touch the client.

### 11.2 Git server

Requires the official `mcp-server-git` Python package, already listed in
`chatbot/requirements.txt` (installed in section 10.1's `pip install -r
requirements.txt`).

**Important:** this reference server has no `git_init` tool — it expects
`--repository` to already point at a valid Git repo, or every call fails
with `"... is not a valid Git repository"`. Initialize it once, yourself,
before enabling the server:

```bash
git init chatbot/workspace
```

(This nested repo lives entirely inside the already-git-ignored
`chatbot/workspace/`, so it has no effect on this project's own repo.)

### 11.3 Demo: add a README and commit it

With both servers enabled, `git init chatbot/workspace` already run once
(section 11.2), and `python chatbot.py` running:

```
You: crea un archivo README.md en el workspace que diga "Pharmacy chatbot
     demo", agregalo al staging con git y haz commit con el mensaje
     "initial commit"
```

Tested output:

```
  [tool call] filesystem__write_file({"content": "Pharmacy chatbot demo", "path": "README.md"})
  [tool call] git__git_add({"files": ["README.md"], "repo_path": "workspace"})
  [tool call] git__git_commit({"message": "initial commit", "repo_path": "workspace"})

Bot: ¡Listo! He creado el archivo README.md con el contenido "Pharmacy
chatbot demo", lo he añadido al área de staging y he realizado el commit
con el mensaje "initial commit".
```

Verify independently with `cd chatbot/workspace && git log --oneline`.
The model calls the filesystem server's `write_file` tool, then the git
server's `git_add` and `git_commit` tools — you'll see each `[tool call]
...` line printed, and the full JSON-RPC exchange in
`chatbot/logs/mcp_filesystem.log` and `chatbot/logs/mcp_git.log`.

## 12. Capturing and analyzing traffic with Wireshark

This applies to the **remote** server (section 9) — the local server talks
over stdio (no network packets to capture) and the point of this analysis is
the host↔remote-server traffic over the network.

1. Deploy `remote_server.py` (section 9.3), or run it locally and have the
   chatbot connect to it via `http://localhost:8080/mcp` if you only need to
   demonstrate the protocol exchange rather than genuine internet traffic.
2. Open Wireshark, start a capture on the interface that carries the
   traffic (your Wi-Fi/Ethernet adapter for a real cloud URL; `Npcap
   Loopback Adapter` on Windows for `localhost`).
3. Filter to just this traffic, e.g. `tcp.port == 443` (cloud, HTTPS) or
   `tcp.port == 8080` (local HTTP) — add `and http` for the local case since
   plain HTTP is inspectable, whereas HTTPS to Cloud Run will be encrypted
   at the TLS layer (you'll still see the TCP/TLS handshake and segments,
   just not the plaintext JSON-RPC — mention this in the report).
4. In `chatbot/servers_config.json`, enable `pharmacy-remote` and run
   `python chatbot.py`; send a few messages that trigger tool calls
   (`check_stock`, `create_order`, etc.).
5. Stop the capture and, in the JSON-RPC exchange (or the decrypted HTTP
   stream if you tested against `localhost`), classify each message:
   - **Synchronization / handshake**: the TCP three-way handshake (`SYN`,
     `SYN, ACK`, `ACK`), and the MCP `initialize` request +
     `notifications/initialized` notification.
   - **Requests**: `tools/list`, `tools/call` (`POST /mcp` frames sent by
     the chatbot).
   - **Responses**: the JSON-RPC `result`/`error` bodies sent back by
     `remote_server.py` (`HTTP/1.1 200 OK` frames).
6. Save the capture (`.pcapng`) alongside your report — it's already
   git-ignored so it won't bloat the repo; attach it separately per your
   catedrático's instructions.

### 12.1 Captures already taken (this session)

Both captures below were taken with `tshark` and are sitting in the project
root (git-ignored, attach them to your report separately):

**`wireshark_local_capture.pcapng`** — chatbot ↔ `remote_server.py` running
on `localhost:8080` (loopback, plain HTTP), covering one full turn:
`initialize` → `notifications/initialized` → `tools/list` →
`search_medication_by_symptom` → `get_medication_details` → `create_order`.
Because plain HTTP isn't encrypted, the full JSON-RPC bodies are visible.
Findings:
- Each JSON-RPC call is its **own TCP connection** (`HttpMCPClient` doesn't
  reuse connections, and the server is HTTP/1.0-style): a full
  `SYN` → `SYN,ACK` → `ACK` handshake, one `POST /mcp` request frame with
  the JSON-RPC body, one `HTTP/1.0 200 OK` (or `202 Accepted` for the
  `notifications/initialized` notification, which correctly gets no
  JSON-RPC response body) response frame, then `FIN,ACK` from both sides.
  Six such request/response cycles appear, one per JSON-RPC message sent.
- Before almost every successful IPv4 (`127.0.0.1`) connection, Wireshark
  shows 4-5 unanswered `SYN` retransmissions to `::1` (IPv6 loopback) on
  the *same* port. This is `localhost` resolving to both an IPv4 and an
  IPv6 address; Python's `urllib` tries IPv6 first, gets no response
  (`remote_server.py` binds `0.0.0.0`, IPv4 only, so nothing answers on
  `::1`), retries a few times, then falls back to IPv4, which succeeds
  immediately. Worth a line in the network-layer part of your OSI writeup.
- Example decoded payload (frame 16, `initialize` request):
  `{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {...}}` —
  confirms the wire format matches section 5.1 exactly.

**`wireshark_render_capture.pcapng`** — chatbot ↔ the live Render URL
(`https://proyecto-1-redes-3k10.onrender.com`) over the real internet,
covering one `check_stock` call. Findings:
- Real TCP handshake to Render's public IP (seen as `216.24.57.18` in this
  capture — Render may assign a different one for you).
- `TLSv1.2 Client Hello (SNI=proyecto-1-redes-3k10.onrender.com)` — the SNI
  field is the one piece of the exchange visible in cleartext even over
  HTTPS (it's how the server's reverse proxy knows which TLS certificate
  to present); this is a good talking point for the application/transport
  layer boundary in your report.
- Server responds `TLSv1.3 Server Hello, Change Cipher Spec` — TLS 1.3 was
  negotiated even though the ClientHello framed itself as 1.2 (normal:
  1.2-framed ClientHello + a `supported_versions` extension is how TLS 1.3
  stays compatible with older middleboxes).
- Everything after the handshake is `Application Data` — the HTTP request
  line, headers, and JSON-RPC body are all inside the encrypted TLS
  record, unlike the local capture. This is the concrete evidence for your
  report that the remote deployment is confidential in transit, at the
  cost of not being able to inspect the JSON-RPC content directly from the
  capture (cross-reference `chatbot/logs/mcp_pharmacy-remote.log` instead,
  which has the plaintext of what went over that encrypted channel).
- Same one-TCP-connection-per-request pattern as the local capture (`FIN`
  then a client `RST` right after), for the same reason.

## 13. Project status

- **Local MCP server** (`server.py` + `pharmacy_logic.py`): done — first
  partial submission.
- **Remote MCP server** (`remote_server.py` + `Dockerfile`): deployed and
  live on Render at `https://proyecto-1-redes-3k10.onrender.com/mcp`
  (`/health` and `initialize` verified working). Configured and enabled in
  `chatbot/servers_config.json`.
- **Chatbot / MCP host** (`chatbot/`): implemented and tested against both
  the local and the deployed remote server; connecting it to the LLM still
  needs your own free Groq API key (section 10.1).
- **Official Filesystem/Git servers** (section 11): enabled and tested end
  to end through the chatbot — creating `README.md` via Filesystem, then
  `git add` + `git commit` via Git, verified with `git log`.
- **Wireshark capture and OSI/TCP-IP layer analysis** (section 12): two
  captures already taken (local plaintext HTTP + real HTTPS to Render),
  findings documented in section 12.1. Still needed from you: writing this
  up into the formal report (incisos 8-10) in whatever format/template your
  catedrático requires.
