"""Protocol-level checks for the MCP endpoint, run against a stub `frappe`.

These cover what a real bench can't easily exercise in CI: the 401 discovery
challenge, the JSON-RPC handshake, tool dispatch, and rollback on tool errors.
Run outside a bench:  python -m pytest tests  (from the repo root, NOT via bench run-tests)
"""

import json
import sys
import types

from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

# ---------------------------------------------------------------- frappe stub
frappe = types.ModuleType("frappe")
frappe.session = types.SimpleNamespace(user="Guest")
frappe.request = None
frappe.calls = []


class PermissionError(Exception):
	pass


frappe.PermissionError = PermissionError
frappe.whitelist = lambda **kw: (lambda fn: fn)
frappe.db = types.SimpleNamespace(rollback=lambda: frappe.calls.append("rollback"))
frappe.clear_messages = lambda: None
frappe._dict = dict
frappe.local = types.SimpleNamespace(form_dict={"id": 1, "method": "tools/call"})
frappe.as_json = lambda obj, indent=None: json.dumps(obj, default=str)
frappe.get_roles = lambda user=None: ["System Manager"]
frappe.utils = types.SimpleNamespace(
	get_url=lambda path="": "https://go.example.com" + path,
	strip_html=lambda s: s,
	get_fullname=lambda u: "Test User",
)
client = types.ModuleType("frappe.client")


def _get(doctype, name):
	assert frappe.local.form_dict == {}, "JSON-RPC body leaked into form_dict"
	if name == "SECRET":
		raise PermissionError("Not permitted")
	return {"doctype": doctype, "name": name, "grand_total": 10}


client.get = _get
frappe.client = client
sys.modules["frappe"] = frappe
sys.modules["frappe.client"] = client

from mydot_mcp import mcp  # noqa: E402


def call(body, method="POST", user="amirul@example.com"):
	frappe.session.user = user
	frappe.request = Request(
		EnvironBuilder(method=method, data=json.dumps(body) if body is not None else None).get_environ()
	)
	return mcp.handle()


def rpc(method, params=None, id=1):
	return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


def test_guest_gets_401_with_resource_metadata():
	r = call(rpc("initialize"), user="Guest")
	assert r.status_code == 401
	assert 'resource_metadata="https://go.example.com/.well-known/oauth-protected-resource"' in r.headers[
		"WWW-Authenticate"
	]


def test_initialize_echoes_supported_protocol():
	r = call(rpc("initialize", {"protocolVersion": "2025-03-26"}))
	res = json.loads(r.get_data())["result"]
	assert res["protocolVersion"] == "2025-03-26"
	assert "tools" in res["capabilities"]


def test_notification_is_accepted_without_body():
	r = call({"jsonrpc": "2.0", "method": "notifications/initialized"})
	assert r.status_code == 202 and r.get_data() == b""


def test_get_is_405():
	assert call(None, method="GET").status_code == 405


def test_tools_list_has_valid_schemas():
	tools = json.loads(call(rpc("tools/list")).get_data())["result"]["tools"]
	names = {t["name"] for t in tools}
	assert {"get_document", "list_documents", "create_document", "run_report", "call_method"} <= names
	for t in tools:
		assert t["inputSchema"]["type"] == "object"
		assert set(t["inputSchema"]["required"]) <= set(t["inputSchema"]["properties"])


def test_tool_call_returns_text_content():
	r = call(rpc("tools/call", {"name": "get_document", "arguments": {"doctype": "Item", "name": "X1"}}))
	res = json.loads(r.get_data())["result"]
	assert res["isError"] is False
	assert json.loads(res["content"][0]["text"])["name"] == "X1"


def test_tool_error_rolls_back_and_reports():
	frappe.calls.clear()
	r = call(rpc("tools/call", {"name": "get_document", "arguments": {"doctype": "Item", "name": "SECRET"}}))
	res = json.loads(r.get_data())["result"]
	assert res["isError"] is True and "Not permitted" in res["content"][0]["text"]
	assert frappe.calls == ["rollback"]


def test_missing_required_argument_is_invalid_params():
	r = call(rpc("tools/call", {"name": "get_document", "arguments": {"doctype": "Item"}}))
	assert json.loads(r.get_data())["error"]["code"] == -32602


def test_call_method_cannot_reenter_mcp():
	r = call(rpc("tools/call", {"name": "call_method", "arguments": {"method": "mydot_mcp.mcp.handle"}}))
	assert json.loads(r.get_data())["result"]["isError"] is True


def test_unknown_method():
	assert json.loads(call(rpc("resources/list")).get_data())["error"]["code"] == -32601


def test_wellknown_discovery_documents():
	from mydot_mcp.wellknown import OAuthDiscovery

	assert not OAuthDiscovery("about").can_render()
	pr = OAuthDiscovery("/.well-known/oauth-protected-resource")
	assert pr.can_render()
	assert json.loads(pr.render().get_data())["authorization_servers"] == ["https://go.example.com"]
	meta = json.loads(OAuthDiscovery(".well-known/oauth-authorization-server").render().get_data())
	assert meta["code_challenge_methods_supported"] == ["S256"]
	assert "client_secret_basic" not in meta["token_endpoint_auth_methods_supported"]
	assert meta["token_endpoint"].endswith("frappe.integrations.oauth2.get_token")
