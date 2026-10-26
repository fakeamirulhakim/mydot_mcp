"""MCP tools. Each runs as the OAuth-signed-in user.

Reads and writes go through frappe.client / Document APIs, i.e. the same
permission checks as the REST API and the desk UI. Nothing here uses
ignore_permissions.
"""

from __future__ import annotations

import frappe
import frappe.client

TOOLS: dict[str, dict] = {}

_LAYOUT_TYPES = {"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Heading", "Fold"}

_FILTERS = {
	"description": (
		'Frappe filters: either an object {"status": "Open", "posting_date": [">=", "2026-01-01"]} '
		'or a list [["Sales Invoice", "status", "=", "Paid"], ...].'
	),
	"anyOf": [{"type": "object"}, {"type": "array"}],
}
_DOCTYPE = {"type": "string", "description": 'DocType name, e.g. "Sales Invoice", "Item", "Customer".'}
_NAME = {"type": "string", "description": "Document name (ID), e.g. ACC-SINV-2026-00012."}


def tool(name, description, properties=None, required=(), *, read_only=False, destructive=False):
	def decorator(fn):
		TOOLS[name] = {
			"name": name,
			"description": description,
			"inputSchema": {
				"type": "object",
				"properties": properties or {},
				"required": list(required),
			},
			"annotations": {
				"readOnlyHint": read_only,
				"destructiveHint": destructive,
				"openWorldHint": False,
			},
			"fn": fn,
		}
		return fn

	return decorator


# ---------------------------------------------------------------- discovery


@tool("whoami", "Who the connector is signed in as, and their roles.", read_only=True)
def whoami():
	user = frappe.session.user
	return {
		"user": user,
		"full_name": frappe.utils.get_fullname(user),
		"roles": frappe.get_roles(user),
		"site": frappe.utils.get_url(),
	}


@tool(
	"search_doctypes",
	"Find DocTypes whose name contains the text (case-insensitive). Only returns ones the user can read.",
	{"query": {"type": "string"}, "include_child_tables": {"type": "boolean", "default": False}},
	["query"],
	read_only=True,
)
def search_doctypes(query, include_child_tables=False):
	filters = {"name": ["like", f"%{query}%"]}
	if not include_child_tables:
		filters["istable"] = 0
	rows = frappe.get_all(
		"DocType",
		filters=filters,
		fields=["name", "module", "istable", "issingle", "is_submittable"],
		order_by="name asc",
		limit_page_length=100,
	)
	return [r for r in rows if r.istable or frappe.has_permission(r.name, "read")][:40]


@tool(
	"get_doctype_fields",
	"Field list of a DocType (fieldname, label, type, options, required). Call before filtering or writing.",
	{"doctype": _DOCTYPE},
	["doctype"],
	read_only=True,
)
def get_doctype_fields(doctype):
	meta = frappe.get_meta(doctype)
	if not meta.istable and not frappe.has_permission(doctype, "read"):
		raise frappe.PermissionError(f"No read permission on {doctype}")
	return {
		"doctype": doctype,
		"is_submittable": bool(meta.is_submittable),
		"is_child_table": bool(meta.istable),
		"title_field": meta.title_field,
		"fields": [
			{
				"fieldname": f.fieldname,
				"label": f.label,
				"fieldtype": f.fieldtype,
				"options": f.options,
				"reqd": f.reqd,
				"read_only": f.read_only,
			}
			for f in meta.fields
			if f.fieldtype not in _LAYOUT_TYPES
		],
	}


# -------------------------------------------------------------------- reads


@tool(
	"list_documents",
	"List documents of a DocType. Supports filters, chosen fields, ordering, paging and group_by. "
	'Aggregates use dict fields, e.g. fields ["status", {"COUNT": "*", "as": "n"}] with group_by "status".',
	{
		"doctype": _DOCTYPE,
		"filters": _FILTERS,
		"or_filters": _FILTERS,
		"fields": {
			"type": "array",
			"items": {"anyOf": [{"type": "string"}, {"type": "object"}]},
			"description": 'Field names or aggregate dicts. Default ["name"].',
		},
		"order_by": {"type": "string", "description": 'e.g. "modified desc"'},
		"group_by": {"type": "string"},
		"limit": {"type": "integer", "default": 20, "maximum": 500},
		"start": {"type": "integer", "default": 0},
	},
	["doctype"],
	read_only=True,
)
def list_documents(doctype, filters=None, or_filters=None, fields=None, order_by=None, group_by=None, limit=20, start=0):
	return frappe.client.get_list(
		doctype,
		fields=fields or ["name"],
		filters=filters,
		or_filters=or_filters,
		order_by=order_by,
		group_by=group_by,
		limit_start=start,
		limit_page_length=min(int(limit or 20), 500),
	)


@tool(
	"count_documents",
	"Count documents of a DocType matching filters.",
	{"doctype": _DOCTYPE, "filters": _FILTERS},
	["doctype"],
	read_only=True,
)
def count_documents(doctype, filters=None):
	return {"doctype": doctype, "count": frappe.client.get_count(doctype, filters=filters)}


@tool(
	"get_document",
	"Full document including child tables.",
	{"doctype": _DOCTYPE, "name": _NAME},
	["doctype", "name"],
	read_only=True,
)
def get_document(doctype, name):
	return frappe.client.get(doctype, name)


@tool(
	"run_report",
	"Run a Script/Query report (e.g. General Ledger, Stock Balance, Accounts Receivable) and return columns and rows.",
	{
		"report_name": {"type": "string"},
		"filters": {"type": "object", "description": "Report filters, e.g. {\"company\": \"...\", \"from_date\": \"2026-01-01\"}"},
		"limit": {"type": "integer", "default": 200, "description": "Max rows returned."},
	},
	["report_name"],
	read_only=True,
)
def run_report(report_name, filters=None, limit=200):
	from frappe.desk.query_report import run

	out = run(report_name, filters=filters or {}, ignore_prepared_report=True)
	rows = out.get("result") or []
	return {
		"columns": [
			{k: c.get(k) for k in ("fieldname", "label", "fieldtype", "options")} if isinstance(c, dict) else c
			for c in out.get("columns") or []
		],
		"rows": rows[: int(limit or 200)],
		"total_rows": len(rows),
	}


# ------------------------------------------------------------------- writes


@tool(
	"create_document",
	"Create a document (saved as draft). Set submit=true to also submit a submittable DocType.",
	{
		"doctype": _DOCTYPE,
		"data": {"type": "object", "description": "Field values; child tables as lists of objects."},
		"submit": {"type": "boolean", "default": False},
	},
	["doctype", "data"],
)
def create_document(doctype, data, submit=False):
	doc = frappe.get_doc({**data, "doctype": doctype})
	doc.insert()
	if submit:
		doc.submit()
	return doc.as_dict()


@tool(
	"update_document",
	"Update fields on an existing document. Child-table values replace the whole table.",
	{"doctype": _DOCTYPE, "name": _NAME, "data": {"type": "object"}},
	["doctype", "name", "data"],
)
def update_document(doctype, name, data):
	return frappe.client.set_value(doctype, name, data)


@tool(
	"submit_document",
	"Submit a draft document (docstatus 0 -> 1). Posts ledger entries for accounting/stock DocTypes.",
	{"doctype": _DOCTYPE, "name": _NAME},
	["doctype", "name"],
	destructive=True,
)
def submit_document(doctype, name):
	doc = frappe.get_doc(doctype, name)
	doc.submit()
	return {"doctype": doctype, "name": doc.name, "docstatus": doc.docstatus}


@tool(
	"cancel_document",
	"Cancel a submitted document (docstatus 1 -> 2).",
	{"doctype": _DOCTYPE, "name": _NAME},
	["doctype", "name"],
	destructive=True,
)
def cancel_document(doctype, name):
	frappe.client.cancel(doctype, name)
	return {"doctype": doctype, "name": name, "docstatus": 2}


@tool(
	"delete_document",
	"Delete a document permanently (draft or cancelled only).",
	{"doctype": _DOCTYPE, "name": _NAME},
	["doctype", "name"],
	destructive=True,
)
def delete_document(doctype, name):
	frappe.client.delete(doctype, name)
	return {"deleted": True, "doctype": doctype, "name": name}


@tool(
	"call_method",
	"Call any whitelisted Frappe/ERPNext Python method, e.g. "
	'"erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice" with {"source_name": "SAL-ORD-..."}. '
	"Same as POST /api/method/<method>.",
	{"method": {"type": "string"}, "args": {"type": "object"}},
	["method"],
	destructive=True,
)
def call_method(method, args=None):
	if method.startswith("mydot_mcp."):
		raise frappe.PermissionError("Calling the MCP app itself is not allowed")
	get_attr = getattr(frappe, "get_attr", None) or frappe.utils.get_attr  # moved in v16
	fn = get_attr(method)
	frappe.is_whitelisted(fn)
	return frappe.call(fn, **(args or {}))
