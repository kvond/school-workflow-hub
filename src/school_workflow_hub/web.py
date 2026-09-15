"""Preview-only WSGI entry point for the existing hub.

Cloud Run IAM/IAP must protect the deployment. The optional metadata check also
verifies IAP's signed identity. No cell data is served or workflows executed.
Only explicitly packaged preview assets are exposed, never arbitrary files.
"""
import json
from pathlib import Path


ASSET_ROOT = Path(__file__).resolve().parents[2] / "web"
ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    **{name: "text/javascript; charset=utf-8" for name in (
        "app.mjs", "pages.mjs", "contract.mjs", "workspace-contract.mjs",
        "workspace-preview.mjs", "draft-review.mjs", "completion-view.mjs",
        "review-view.mjs", "agenda-connection.mjs",
    )},
    **{name: "application/json; charset=utf-8" for name in (
        "operations.preview.json", "workspace.preview.json",
        "workspace.partial.preview.json", "teacher-prompts.json",
    )},
}
SECURITY_HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "default-src 'self'; script-src 'self'; "
     "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
     "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"),
]


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")

    def respond(status, body, content_type="application/json; charset=utf-8", extra=()):
        if not isinstance(body, bytes):
            body = json.dumps(body, sort_keys=True).encode("utf-8")
        start_response(status, SECURITY_HEADERS + [
            ("Content-Type", content_type), ("Content-Length", str(len(body)))
        ] + list(extra))
        return [b"" if method == "HEAD" else body]

    # Never parse bodies, query strings or caller-supplied bearer tokens.
    if method not in ("GET", "HEAD"):
        return respond("405 Method Not Allowed", {"error": "method_not_allowed"},
                       extra=[("Allow", "GET, HEAD")])
    if path == "/healthz":
        return respond("200 OK", {"status": "ok", "mode": "preview_only",
                                  "school_connected": False, "external_writes": 0})
    if path == "/api/readiness":
        return respond("200 OK", {
            "schema_version": "hub-hosting-readiness/v1",
            "mode": "preview_only", "school_connected": False,
            "authentication_verified": False, "persistent_storage_verified": False,
            "save_enabled": False, "run_enabled": False,
            "external_writes": 0,
        })
    if path == "/api/agenda-connection" and method == "GET":
        from .adapters.hosted_agenda import check_connection, ConnectionError
        try:
            return respond("200 OK", check_connection(environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION")))
        except ConnectionError as error:
            status = "403 Forbidden" if str(error) == "sign_in_required" else "503 Service Unavailable"
            return respond(status, {"error": str(error), "external_writes": 0})
        except Exception:
            # No provider response, credential or private source in error logs.
            return respond("503 Service Unavailable", {"error": "connection_check_failed", "external_writes": 0})
    if path.startswith(("/api/", "/auth/", "/oauth/")):
        return respond("503 Service Unavailable", {"error": "not_connected",
                                                    "external_writes": 0})
    name = "index.html" if path == "/" else path[1:] if path.startswith("/") else ""
    if name not in ASSETS:
        return respond("404 Not Found", {"error": "not_found"})
    try:
        asset = ASSET_ROOT / name
        if asset.is_symlink():
            raise OSError("symlink not permitted")
        body = asset.read_bytes()
    except OSError:
        return respond("503 Service Unavailable", {"error": "preview_unavailable"})
    return respond("200 OK", body, ASSETS[name])
