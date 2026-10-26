# MyDot MCP

A small Frappe app that turns an ERPNext site into a remote MCP server. Claude
(desktop, web, iPhone) connects to it as a custom connector.

- **Endpoint:** `https://<site>/api/method/mydot_mcp.mcp.handle`
- **Auth:** Frappe's built-in OAuth 2 (dynamic client registration and RFC 9728
  metadata). There's no API key to store anywhere. You sign in once with your
  ERPNext login, and every call runs **as you**, with your roles and permissions.
- **No extra dependencies.** It's just the app code.

## Tools

| Tool | What it does |
|---|---|
| `whoami` | Signed-in user, roles |
| `search_doctypes`, `get_doctype_fields` | Discover DocTypes and their fields |
| `list_documents`, `count_documents`, `get_document` | Read data (filters, fields, paging, group_by) |
| `run_report` | Run Script/Query reports (Stock Balance, General Ledger, …) |
| `create_document`, `update_document` | Write drafts |
| `submit_document`, `cancel_document`, `delete_document` | Change docstatus or delete |
| `call_method` | Any whitelisted method (e.g. `make_sales_invoice` mappers) |

All of them go through `frappe.client` / Document APIs, so they get the same
permission checks as the REST API. If a tool fails, its transaction is rolled back.

## Install

```bash
cd ~/frappe-bench
bench get-app https://github.com/fakeamirulhakim/mydot_mcp
bench --site go.mydotconsulting.com install-app mydot_mcp
bench restart
```

No migration or DocTypes are involved; installing it only makes the endpoint available.

Smoke test (it should return **401** with a `WWW-Authenticate: Bearer resource_metadata=...` header):

```bash
curl -si -X POST https://go.mydotconsulting.com/api/method/mydot_mcp.mcp.handle \
  -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' | head -5
```

## OAuth Settings (already on for go.mydotconsulting.com)

Under **OAuth Settings**, these must be enabled: *Show Auth Server Metadata*,
*Show Protected Resource Metadata*, and *Enable Dynamic Client Registration*.

## Add to Claude

claude.ai → **Settings → Connectors → Add custom connector**

- Name: `MYDOT ERPNext`
- URL: `https://go.mydotconsulting.com/api/method/mydot_mcp.mcp.handle`

Click **Connect**, log in to ERPNext and approve. The connector then shows up in
every chat on every device.

**Revoke access:** delete the matching **OAuth Client** or **OAuth Bearer Token**
in ERPNext, or disconnect the connector in Claude.

## Tests

The protocol tests run against a stubbed `frappe`. Run them from the repo root,
**not** via `bench run-tests`:

```bash
pip install pytest werkzeug && python -m pytest tests
```
