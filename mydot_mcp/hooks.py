app_name = "mydot_mcp"
app_title = "MyDot MCP"
app_publisher = "Mydot ERP Consulting"
app_description = "MCP endpoint for Claude, authenticated with Frappe OAuth"
app_email = "amirul.zulkefli@mydotconsulting.com"
app_license = "mit"

# Serves OAuth discovery documents on Frappe versions that lack them (v15).
page_renderer = ["mydot_mcp.wellknown.OAuthDiscovery"]
