"""Streamable-HTTP MCP endpoint (JSON responses only, no SSE).

URL: https://<site>/api/method/mydot_mcp.mcp.handle

Auth: Frappe's own OAuth 2 provider. Unauthenticated calls get a 401 with a
WWW-Authenticate header pointing at /.well-known/oauth-protected-resource, which
is how MCP clients (claude.ai) discover the OAuth server and start sign-in.
Every tool then runs as the signed-in user, so Frappe permissions apply.
"""

from __future__ import annotations

import json

import frappe
from werkzeug.wrappers import Response

from mydot_mcp import __version__
from mydot_mcp.tools import TOOLS

SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_TEXT = 100_000

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


@frappe.whitelist(allow_guest=True, methods=["GET", "POST", "DELETE"])
def handle():
	request = frappe.request

	if frappe.session.user == "Guest":
		return _unauthorized()

	if request.method != "POST":
		# No server-initiated streams and no sessions to delete.
		return Response(status=405, headers={"Allow": "POST"})

	try:
		payload = json.loads(request.get_data(as_text=True) or "")
	except ValueError:
		return _json(_error(None, PARSE_ERROR, "Parse error"))

	if isinstance(payload, list):
		replies = [r for r in (_dispatch(m) for m in payload) if r is not None]
		return _json(replies) if replies else Response(status=202)

	reply = _dispatch(payload)
	return _json(reply) if reply is not None else Response(status=202)


def _dispatch(message):
	if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
		return _error(None, INVALID_REQUEST, "Invalid Request")

	if "id" not in message:  # notification (initialized, cancelled, ...)
		return None

	msg_id = message["id"]
	method = message.get("method")
	params = message.get("params") or {}

	if method == "initialize":
		requested = params.get("protocolVersion")
		return _result(
			msg_id,
			{
				"protocolVersion": requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0],
				"capabilities": {"tools": {"listChanged": False}},
				"serverInfo": {"name": "mydot-erpnext", "version": __version__},
				"instructions": (
					"ERPNext/Frappe for Mydot. Every call runs as the signed-in user with their "
					"permissions. Use search_doctypes and get_doctype_fields before guessing names. "
					"Writes, submits, cancels and deletes are real; confirm with the user first."
				),
			},
		)
	if method == "ping":
		return _result(msg_id, {})
	if method == "tools/list":
		return _result(
			msg_id,
			{
				"tools": [
					{
						"name": t["name"],
						"description": t["description"],
						"inputSchema": t["inputSchema"],
						"annotations": t["annotations"],
					}
					for t in TOOLS.values()
				]
			},
		)
	if method == "tools/call":
		return _call_tool(msg_id, params)

	return _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def _call_tool(msg_id, params):
	name = params.get("name")
	tool = TOOLS.get(name)
	if not tool:
		return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")

	args = params.get("arguments") or {}
	if not isinstance(args, dict):
		return _error(msg_id, INVALID_PARAMS, "arguments must be an object")
	missing = [k for k in tool["inputSchema"].get("required", []) if k not in args]
	if missing:
		return _error(msg_id, INVALID_PARAMS, f"Missing required arguments: {', '.join(missing)}")
	allowed = set(tool["inputSchema"]["properties"])
	args = {k: v for k, v in args.items() if k in allowed}

	try:
		data = tool["fn"](**args)
	except Exception as e:
		# Don't let Frappe commit half-done writes from a failed tool.
		frappe.db.rollback()
		frappe.clear_messages()
		return _result(msg_id, {"content": [{"type": "text", "text": _describe(e)}], "isError": True})

	text = data if isinstance(data, str) else frappe.as_json(data, indent=None)
	if len(text) > MAX_TEXT:
		text = text[:MAX_TEXT] + f"\n... [truncated {len(text) - MAX_TEXT} chars; narrow fields/filters or page with start]"
	return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})


def _describe(e: Exception) -> str:
	msg = str(e) or e.__class__.__name__
	return f"{e.__class__.__name__}: {frappe.utils.strip_html(msg)}"


def _unauthorized():
	metadata = frappe.utils.get_url("/.well-known/oauth-protected-resource")
	resp = _json(_error(None, INVALID_REQUEST, "Authentication required"), status=401)
	resp.headers["WWW-Authenticate"] = f'Bearer resource_metadata="{metadata}"'
	return resp


def _result(msg_id, result):
	return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
	return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _json(body, status=200):
	return Response(json.dumps(body), status=status, content_type="application/json")
