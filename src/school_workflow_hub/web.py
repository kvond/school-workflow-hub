"""Existing hub WSGI entry point: previews and authenticated read-only Agenda.

Cloud Run IAM/IAP must protect the deployment. The optional metadata check also
verifies IAP's signed identity, as does the dated lesson reader. No writes or workflows execute.
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
        "review-view.mjs", "agenda-connection.mjs", "teaching-results.mjs",
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


def application(environ, start_response, *, teaching_provider=None):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")

    def respond(status, body, content_type="application/json; charset=utf-8", extra=()):
        if not isinstance(body, bytes):
            body = json.dumps(body, sort_keys=True).encode("utf-8")
        start_response(status, SECURITY_HEADERS + [
            ("Content-Type", content_type), ("Content-Length", str(len(body)))
        ] + list(extra))
        return [b"" if method == "HEAD" else body]

    # Never parse bodies or caller-supplied bearer tokens.
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
    if path == "/api/teaching-results" and method == "GET":
        from urllib.parse import parse_qs
        from .adapters.teaching_results import read_teaching_results
        from .adapters.hosted_agenda import ConnectionError
        try:
            query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=3)
            if set(query) != {"date", "class_key", "agenda_checked_at"} or any(
                    len(value) != 1 for value in query.values()):
                raise ConnectionError("invalid_teaching_scope")
            return respond("200 OK", read_teaching_results(
                environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION"), day=query["date"][0],
                class_key=query["class_key"][0], agenda_checked_at=query["agenda_checked_at"][0],
                provider=teaching_provider))
        except ConnectionError as error:
            status = "403 Forbidden" if str(error) == "sign_in_required" else (
                "400 Bad Request" if str(error) == "invalid_teaching_scope" else "503 Service Unavailable")
            return respond(status, {"error": str(error), "external_writes": 0})
        except ValueError:
            return respond("400 Bad Request", {"error": "invalid_teaching_scope", "external_writes": 0})
        except Exception:
            return respond("503 Service Unavailable", {"error": "teaching_results_unavailable", "external_writes": 0})
    if path == "/api/agenda-prompts" and method == "GET":
        from .adapters.agenda_prompts import read_prompts
        from .adapters.hosted_agenda import ConnectionError
        try:
            return respond("200 OK", read_prompts(environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION")))
        except ConnectionError as error:
            return respond("403 Forbidden" if str(error) == "sign_in_required" else "503 Service Unavailable",
                           {"error": str(error), "external_writes": 0})
        except Exception:
            return respond("503 Service Unavailable", {"error": "agenda_prompts_unavailable", "external_writes": 0})
    if path == "/api/agenda-day" and method == "GET":
        from urllib.parse import parse_qs
        from .adapters.dated_agenda import read_day
        from .adapters.hosted_agenda import ConnectionError
        try:
            query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=2)
            if set(query) != {"date"} or len(query["date"]) != 1:
                raise ConnectionError("invalid_date")
            return respond("200 OK", read_day(environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION"), query["date"][0]))
        except ConnectionError as error:
            status = "403 Forbidden" if str(error) == "sign_in_required" else "400 Bad Request" if str(error) == "invalid_date" else "503 Service Unavailable"
            return respond(status, {"error": str(error), "external_writes": 0})
        except Exception:
            return respond("503 Service Unavailable", {"error": "agenda_read_failed", "external_writes": 0})
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
