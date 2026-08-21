#!/usr/bin/env python3
"""
Pharmacy Chatbot MCP Server
============================

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

Course: CC3067 Redes - Universidad del Valle de Guatemala
"""

import sys
import json
import uuid
import datetime

# ---------------------------------------------------------------------------
# In-memory "database" (seed data)
# ---------------------------------------------------------------------------

MEDICAL_DISCLAIMER = (
    "This information is for general guidance only and does not replace "
    "professional medical advice. If symptoms persist, worsen, or you are "
    "unsure, consult a doctor or pharmacist."
)

# symptom (lowercase, simplified) -> list of medication_ids
SYMPTOM_INDEX = {
    "dolor de cabeza": ["MED1", "MED2"],
    "headache": ["MED1", "MED2"],
    "fiebre": ["MED1", "MED3"],
    "fever": ["MED1", "MED3"],
    "gripe": ["MED3", "MED4", "MED6"],
    "flu": ["MED3", "MED4", "MED6"],
    "resfriado": ["MED4", "MED6"],
    "cold": ["MED4", "MED6"],
    "alergia": ["MED5"],
    "allergy": ["MED5"],
    "dolor muscular": ["MED2"],
    "muscle pain": ["MED2"],
    "tos": ["MED6"],
    "cough": ["MED6"],
    "acidez": ["MED7"],
    "heartburn": ["MED7"],
    "infeccion": ["MED8"],
    "infection": ["MED8"],
}

MEDICATIONS = {
    "MED1": {
        "medication_id": "MED1", "name": "Paracetamol 500mg",
        "category": "analgesico_antipiretico",
        "requires_prescription": False,
        "dosage": "1 tableta cada 6-8 horas. Maximo 4 tabletas al dia.",
        "contraindications": "Enfermedad hepatica grave. No combinar con alcohol.",
        "price": 15.00, "stock": 120,
    },
    "MED2": {
        "medication_id": "MED2", "name": "Ibuprofeno 400mg",
        "category": "antiinflamatorio",
        "requires_prescription": False,
        "dosage": "1 tableta cada 8 horas con alimentos. Maximo 3 tabletas al dia.",
        "contraindications": "Ulcera gastrica, embarazo (tercer trimestre).",
        "price": 20.00, "stock": 80,
    },
    "MED3": {
        "medication_id": "MED3", "name": "Acetaminofen + Fenilefrina (antigripal)",
        "category": "antigripal",
        "requires_prescription": False,
        "dosage": "1 sobre cada 8 horas disuelto en agua caliente.",
        "contraindications": "Hipertension no controlada.",
        "price": 25.00, "stock": 60,
    },
    "MED4": {
        "medication_id": "MED4", "name": "Loratadina 10mg",
        "category": "antihistaminico",
        "requires_prescription": False,
        "dosage": "1 tableta cada 24 horas.",
        "contraindications": "Insuficiencia hepatica grave.",
        "price": 18.00, "stock": 90,
    },
    "MED5": {
        "medication_id": "MED5", "name": "Cetirizina 10mg",
        "category": "antihistaminico",
        "requires_prescription": False,
        "dosage": "1 tableta cada 24 horas.",
        "contraindications": "Puede causar somnolencia; evitar conducir.",
        "price": 22.00, "stock": 70,
    },
    "MED6": {
        "medication_id": "MED6", "name": "Jarabe para la tos con dextrometorfano",
        "category": "antitusivo",
        "requires_prescription": False,
        "dosage": "10ml cada 6-8 horas.",
        "contraindications": "No usar en menores de 6 anios.",
        "price": 35.00, "stock": 40,
    },
    "MED7": {
        "medication_id": "MED7", "name": "Omeprazol 20mg",
        "category": "antiacido",
        "requires_prescription": False,
        "dosage": "1 capsula al dia en ayunas.",
        "contraindications": "Uso prolongado sin supervision medica no recomendado.",
        "price": 28.00, "stock": 55,
    },
    "MED8": {
        "medication_id": "MED8", "name": "Amoxicilina 500mg",
        "category": "antibiotico",
        "requires_prescription": True,
        "dosage": "Segun prescripcion medica.",
        "contraindications": "Alergia a penicilinas. Requiere receta medica.",
        "price": 45.00, "stock": 30,
    },
}

# order_id -> order dict
ORDERS = {}


# ---------------------------------------------------------------------------
# Business logic
# ---------------------------------------------------------------------------

def tool_search_medication_by_symptom(args):
    symptom = args.get("symptom")
    if not symptom or not isinstance(symptom, str):
        raise ValueError("'symptom' is required and must be a string.")

    key = symptom.strip().lower()
    med_ids = SYMPTOM_INDEX.get(key)

    if not med_ids:
        return {
            "symptom": symptom,
            "matches": [],
            "message": (
                f"No OTC medication mapped for symptom '{symptom}'. "
                f"Please consult a pharmacist."
            ),
            "disclaimer": MEDICAL_DISCLAIMER,
        }

    matches = []
    for mid in med_ids:
        m = MEDICATIONS[mid]
        matches.append({
            "medication_id": m["medication_id"],
            "name": m["name"],
            "category": m["category"],
            "requires_prescription": m["requires_prescription"],
            "price": m["price"],
            "in_stock": m["stock"] > 0,
        })

    return {"symptom": symptom, "matches": matches, "disclaimer": MEDICAL_DISCLAIMER}


def tool_get_medication_details(args):
    medication_id = args.get("medication_id")
    if not medication_id or medication_id not in MEDICATIONS:
        raise ValueError(f"Medication '{medication_id}' not found.")

    m = MEDICATIONS[medication_id]
    return {**m, "disclaimer": MEDICAL_DISCLAIMER}


def tool_check_stock(args):
    medication_id = args.get("medication_id")
    if not medication_id or medication_id not in MEDICATIONS:
        raise ValueError(f"Medication '{medication_id}' not found.")

    m = MEDICATIONS[medication_id]
    return {
        "medication_id": medication_id,
        "name": m["name"],
        "stock": m["stock"],
        "price": m["price"],
        "in_stock": m["stock"] > 0,
    }


def tool_create_order(args):
    customer_name = args.get("customer_name")
    phone = args.get("phone")
    medication_id = args.get("medication_id")
    quantity = args.get("quantity")

    if not customer_name or not isinstance(customer_name, str):
        raise ValueError("'customer_name' is required and must be a string.")
    if not phone or not isinstance(phone, str):
        raise ValueError("'phone' is required and must be a string.")
    if not medication_id or medication_id not in MEDICATIONS:
        raise ValueError(f"Medication '{medication_id}' not found.")
    if quantity is None or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("'quantity' must be a positive integer.")

    m = MEDICATIONS[medication_id]

    if m["requires_prescription"]:
        raise ValueError(
            f"'{m['name']}' requires a medical prescription and cannot be "
            f"ordered through the chatbot. Please visit the pharmacy in "
            f"person with your prescription."
        )

    if m["stock"] < quantity:
        raise ValueError(
            f"Insufficient stock for '{m['name']}'. Available: {m['stock']}, requested: {quantity}."
        )

    m["stock"] -= quantity
    order_id = str(uuid.uuid4())[:8]
    order = {
        "order_id": order_id,
        "customer_name": customer_name,
        "phone": phone,
        "medication_id": medication_id,
        "medication_name": m["name"],
        "quantity": quantity,
        "unit_price": m["price"],
        "total_price": round(m["price"] * quantity, 2),
        "status": "pending_pickup",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    ORDERS[order_id] = order
    return {**order, "disclaimer": MEDICAL_DISCLAIMER}


def tool_get_order_status(args):
    order_id = args.get("order_id")
    if not order_id or order_id not in ORDERS:
        raise ValueError(f"Order '{order_id}' not found.")
    return ORDERS[order_id]


def tool_list_customer_orders(args):
    phone = args.get("phone")
    if not phone or not isinstance(phone, str):
        raise ValueError("'phone' is required and must be a string.")

    results = [o for o in ORDERS.values() if o["phone"] == phone]
    return {"phone": phone, "orders": results}


# ---------------------------------------------------------------------------
# Tool registry: JSON Schema definitions exposed to the client via tools/list
# ---------------------------------------------------------------------------

TOOLS = {
    "search_medication_by_symptom": {
        "description": (
            "Search OTC medications recommended for a given symptom "
            "(e.g. 'dolor de cabeza', 'fiebre', 'alergia'). Always returns "
            "a medical disclaimer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "symptom": {"type": "string", "description": "Symptom described by the customer."},
            },
            "required": ["symptom"],
        },
        "handler": tool_search_medication_by_symptom,
    },
    "get_medication_details": {
        "description": "Get full details of a medication: dosage, contraindications, price, prescription requirement.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "medication_id": {"type": "string", "description": "Medication ID, e.g. 'MED1'."},
            },
            "required": ["medication_id"],
        },
        "handler": tool_get_medication_details,
    },
    "check_stock": {
        "description": "Check available stock and price for a medication.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "medication_id": {"type": "string", "description": "Medication ID, e.g. 'MED1'."},
            },
            "required": ["medication_id"],
        },
        "handler": tool_check_stock,
    },
    "create_order": {
        "description": (
            "Create an order for an OTC medication. Fails if the medication "
            "requires a prescription or there is insufficient stock."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "Full name of the customer."},
                "phone": {"type": "string", "description": "Customer phone number (used as customer identifier)."},
                "medication_id": {"type": "string", "description": "Medication ID, e.g. 'MED1'."},
                "quantity": {"type": "integer", "description": "Units to order.", "minimum": 1},
            },
            "required": ["customer_name", "phone", "medication_id", "quantity"],
        },
        "handler": tool_create_order,
    },
    "get_order_status": {
        "description": "Get the status and details of a previously created order.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "ID returned by create_order."},
            },
            "required": ["order_id"],
        },
        "handler": tool_get_order_status,
    },
    "list_customer_orders": {
        "description": "List all orders placed by a customer, by phone number.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Customer phone number."},
            },
            "required": ["phone"],
        },
        "handler": tool_list_customer_orders,
    },
}


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 plumbing
# ---------------------------------------------------------------------------

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2025-06-18"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def make_response(id_, result=None, error=None):
    msg = {"jsonrpc": JSONRPC_VERSION, "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    return msg


def make_error(code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return err


def handle_initialize(msg_id, params):
    result = {
        "protocolVersion": MCP_PROTOCOL_VERSION,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "pharmacy-chatbot-server", "version": "1.0.0"},
    }
    return make_response(msg_id, result=result)


def handle_tools_list(msg_id, params):
    tools_out = []
    for name, spec in TOOLS.items():
        tools_out.append({
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["inputSchema"],
        })
    return make_response(msg_id, result={"tools": tools_out})


def handle_tools_call(msg_id, params):
    if not params or "name" not in params:
        return make_response(
            msg_id, error=make_error(INVALID_PARAMS, "Missing required field 'name'.")
        )

    tool_name = params["name"]
    arguments = params.get("arguments", {}) or {}

    if tool_name not in TOOLS:
        return make_response(
            msg_id, error=make_error(INVALID_PARAMS, f"Unknown tool '{tool_name}'.")
        )

    handler = TOOLS[tool_name]["handler"]
    try:
        data = handler(arguments)
        content = [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}]
        return make_response(msg_id, result={"content": content, "isError": False})
    except ValueError as e:
        # Business-logic errors are returned as tool errors (not JSON-RPC
        # protocol errors), per MCP convention, so the LLM can see and
        # react to them (e.g. tell the customer to see a pharmacist).
        content = [{"type": "text", "text": str(e)}]
        return make_response(msg_id, result={"content": content, "isError": True})
    except Exception as e:
        return make_response(
            msg_id, error=make_error(INTERNAL_ERROR, f"Unexpected server error: {e}")
        )


def handle_ping(msg_id, params):
    return make_response(msg_id, result={})


METHOD_HANDLERS = {
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
    "ping": handle_ping,
}


def process_message(raw_line):
    """Parse one JSON-RPC message and return a response dict, or None
    if no response should be sent (e.g. notifications)."""
    try:
        msg = json.loads(raw_line)
    except json.JSONDecodeError:
        return make_response(None, error=make_error(PARSE_ERROR, "Invalid JSON."))

    if not isinstance(msg, dict) or msg.get("jsonrpc") != JSONRPC_VERSION:
        return make_response(
            msg.get("id") if isinstance(msg, dict) else None,
            error=make_error(INVALID_REQUEST, "Invalid JSON-RPC 2.0 request."),
        )

    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params", {})

    is_notification = "id" not in msg

    if method == "notifications/initialized":
        return None

    handler = METHOD_HANDLERS.get(method)
    if handler is None:
        if is_notification:
            return None
        return make_response(msg_id, error=make_error(METHOD_NOT_FOUND, f"Method '{method}' not found."))

    response = handler(msg_id, params)
    if is_notification:
        return None
    return response


def main():
    log = open("server.log", "a", encoding="utf-8")
    log.write(f"\n--- server started {datetime.datetime.now().isoformat()} ---\n")
    log.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        log.write(f">> {line}\n")
        log.flush()

        response = process_message(line)

        if response is not None:
            out = json.dumps(response, ensure_ascii=False)
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
            log.write(f"<< {out}\n")
            log.flush()


if __name__ == "__main__":
    main()
