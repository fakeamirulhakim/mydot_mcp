"""OAuth discovery documents for Frappe versions that don't serve them (v15 and older).

MCP clients find the sign-in server through:
  /.well-known/oauth-protected-resource   (RFC 9728)
  /.well-known/oauth-authorization-server (RFC 8414)

Frappe v16 serves both itself (OAuth Settings) and answers /.well-known before
the website router runs, so this renderer never fires there. On v15 those paths
fall through to the website router, where this page_renderer picks them up.

v15's token endpoint identifies the client by client_id in the request body
(it does not read HTTP Basic auth), so only "client_secret_post"/"none" are
advertised. v15 has no dynamic client registration: create an OAuth Client in
the desk and enter its client ID in Claude's connector settings.
"""

from __future__ import annotations

import json

import frappe
from werkzeug.wrappers import Response

PROTECTED_RESOURCE = ".well-known/oauth-protected-resource"
AUTH_SERVER = ".well-known/oauth-authorization-server"


class OAuthDiscovery:
	def __init__(self, path=None, http_status_code=None):
		self.path = (path or "").strip("/ ")

	def can_render(self):
		return self.path in (PROTECTED_RESOURCE, AUTH_SERVER)

	def render(self):
		data = protected_resource() if self.path == PROTECTED_RESOURCE else authorization_server()
		return Response(
			json.dumps(data),
			content_type="application/json",
			headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "max-age=3600"},
		)


def protected_resource():
	base = frappe.utils.get_url()
	return {
		"resource": base,
		"authorization_servers": [base],
		"bearer_methods_supported": ["header"],
		"resource_name": "ERPNext",
	}


def authorization_server():
	base = frappe.utils.get_url()
	oauth = f"{base}/api/method/frappe.integrations.oauth2"
	return {
		"issuer": base,
		"authorization_endpoint": f"{oauth}.authorize",
		"token_endpoint": f"{oauth}.get_token",
		"revocation_endpoint": f"{oauth}.revoke_token",
		"introspection_endpoint": f"{oauth}.introspect_token",
		"userinfo_endpoint": f"{oauth}.openid_profile",
		"response_types_supported": ["code"],
		"grant_types_supported": ["authorization_code", "refresh_token"],
		"code_challenge_methods_supported": ["S256"],
		"token_endpoint_auth_methods_supported": ["client_secret_post", "none"],
		"scopes_supported": ["all", "openid"],
	}
