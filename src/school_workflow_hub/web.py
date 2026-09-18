"""Existing hub WSGI entry point: previews and authenticated read-only Agenda.

Cloud Run IAM/IAP must protect the deployment. The optional metadata check also
verifies IAP's signed identity, as does the dated lesson reader. No writes or workflows execute.
Only explicitly packaged preview assets are exposed, never arbitrary files.
"""
import json
import os
from pathlib import Path


ASSET_ROOT = Path(__file__).resolve().parents[2] / "web"
ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    **{name: "text/javascript; charset=utf-8" for name in (
        "app.mjs", "pages.mjs", "contract.mjs", "workspace-contract.mjs",
        "workspace-preview.mjs", "draft-review.mjs", "completion-view.mjs",
        "review-view.mjs", "agenda-connection.mjs", "agenda-editor.mjs", "teaching-results.mjs", "learning-progress.mjs",
        "browser-runs.mjs", "grading-records.mjs", "grade-posting.mjs", "browser-companion.mjs",
        "control-panel.mjs", "submission-monitor.mjs", "assessment-review.mjs",
    )},
    **{name: "application/json; charset=utf-8" for name in (
        "operations.preview.json", "workspace.preview.json",
        "workspace.partial.preview.json", "teacher-prompts.json",
        "run-directions.json",
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

    if path == "/api/browser-runs":
        from .adapters.browser_runs import handle_request, WorkerForbidden
        from .adapters.hosted_agenda import ConnectionError
        from .core.cloud_state import Busy, Conflict, LeaseLost, StoreUnavailable
        if method not in ("GET", "POST"):
            return respond("405 Method Not Allowed", {"error": "method_not_allowed"})
        try:
            body, headers = handle_request(environ, os.environ)
            return respond("200 OK", body, extra=headers)
        except ConnectionError:
            return respond("403 Forbidden", {"error": "sign_in_required"})
        except WorkerForbidden as error:
            return respond("403 Forbidden", {"error": str(error)})
        except Busy:
            return respond("409 Conflict", {"error": "browser_worker_busy"})
        except LeaseLost:
            return respond("409 Conflict", {"error": "browser_claim_expired_or_conflict"})
        except Conflict:
            return respond("409 Conflict", {"error": "browser_run_conflict"})
        except (ValueError, KeyError, TypeError, OverflowError):
            return respond("400 Bad Request", {"error": "invalid_browser_request"})
        except StoreUnavailable:
            return respond("503 Service Unavailable", {"error": "browser_state_unavailable"})
        except Exception:
            return respond("503 Service Unavailable", {"error": "browser_state_unavailable"})

    if path == "/api/storage-check":
        from .adapters.hosted_agenda import _identity_configuration, authorize, ConnectionError
        from .adapters.firestore_state import configured_store
        from .adapters.storage_probe import read_probe, run_probe
        from .core.cloud_state import Conflict, StoreUnavailable
        if method not in ("GET", "POST"):
            return respond("405 Method Not Allowed", {"error": "method_not_allowed"})
        try:
            audience, allowed = _identity_configuration(os.environ)
            authorize(environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION"), audience, allowed)
            revision = os.environ.get("K_REVISION", "")
            if method == "POST":
                # Only a same-origin explicit probe may write fixed fictional data.
                origins = {v for v in os.environ.get("HUB_STATE_PROBE_ORIGINS", "").split(",")
                           if v.startswith("https://") and "/" not in v[8:]}
                if (environ.get("HTTP_ORIGIN") not in origins
                        or environ.get("CONTENT_TYPE") != "application/json"
                        or environ.get("QUERY_STRING", "")):
                    return respond("403 Forbidden", {"error": "storage_probe_origin_required"})
                length = int(environ.get("CONTENT_LENGTH", "0"))
                if not 1 <= length <= 256:
                    raise ValueError()
                data = json.loads(environ["wsgi.input"].read(length))
                if not isinstance(data, dict) or set(data) != {"action", "probe_id"} or data["action"] != "verify":
                    raise ValueError()
                return respond("200 OK", run_probe(configured_store, data["probe_id"], revision))
            from urllib.parse import parse_qs
            query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=1)
            if query and (set(query) != {"probe_id"} or len(query["probe_id"]) != 1):
                raise ValueError()
            probe_id = query.get("probe_id", [os.environ.get("HUB_STATE_ACCEPTANCE_PROBE", "")])[0]
            return respond("200 OK", read_probe(configured_store, probe_id, revision))
        except ConnectionError:
            return respond("403 Forbidden", {"error": "sign_in_required"})
        except Conflict:
            return respond("409 Conflict", {"error": "storage_probe_already_working"})
        except StoreUnavailable:
            return respond("503 Service Unavailable", {"error": "storage_not_verified"})
        except (ValueError, KeyError, TypeError):
            return respond("400 Bad Request", {"error": "invalid_storage_probe"})
        except Exception:
            return respond("503 Service Unavailable", {"error": "storage_not_verified"})

    if path == '/api/agenda-save':
        from .adapters import agenda_save
        from .adapters.hosted_agenda import _identity_configuration, authorize, ConnectionError
        from .core.cloud_state import Conflict
        try:
            audience, allowed = _identity_configuration(os.environ)
            assertion = environ.get('HTTP_X_GOOG_IAP_JWT_ASSERTION')
            authorize(assertion, audience, allowed)
            if method == 'GET':
                from urllib.parse import parse_qs
                query = parse_qs(environ.get('QUERY_STRING', ''), max_num_fields=2)
                if set(query) != {'date', 'class_key'} or any(len(v) != 1 for v in query.values()):
                    raise ValueError()
                return respond('200 OK', agenda_save.capability(assertion, query['date'][0], query['class_key'][0], os.environ))
            if method != 'POST':
                return respond('405 Method Not Allowed', {'error': 'method_not_allowed'})
            origins = set(os.environ.get('HUB_STATE_PROBE_ORIGINS', '').split(','))
            if (not environ.get('HTTP_ORIGIN', '').startswith('https://')
                    or environ.get('HTTP_ORIGIN') not in origins
                    or environ.get('CONTENT_TYPE') != 'application/json' or environ.get('QUERY_STRING', '')):
                return respond('403 Forbidden', {'error': 'save_origin_required'})
            length = int(environ.get('CONTENT_LENGTH', '0'))
            if not 1 <= length <= 250000:
                raise ValueError()
            payload = json.loads(environ['wsgi.input'].read(length))
            return respond('200 OK', agenda_save.save(assertion, payload, os.environ))
        except Conflict:
            return respond('409 Conflict', {'error': 'agenda_changed_or_save_in_progress'})
        except ConnectionError as error:
            return respond('403 Forbidden' if str(error) == 'sign_in_required' else '503 Service Unavailable', {'error': str(error)})
        except (ValueError, TypeError, KeyError):
            return respond('400 Bad Request', {'error': 'invalid_agenda_save'})
        except Exception:
            return respond('503 Service Unavailable', {'error': 'agenda_save_unavailable'})

    if path == "/api/assessment-save":
        from .adapters import assessment_save
        from .adapters.hosted_agenda import ConnectionError
        from .core.cloud_state import Busy, Conflict, LeaseLost, StoreUnavailable
        import re as _re
        try:
            assertion = environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION")
            if method == "GET":
                from urllib.parse import parse_qs
                query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=1)
                if set(query) != {"file_id"} or len(query["file_id"]) != 1:
                    raise ValueError("invalid_assessment_source")
                return respond("200 OK", assessment_save.capture(
                    assertion, query["file_id"][0], os.environ))
            if method != "POST":
                return respond("405 Method Not Allowed", {"error": "method_not_allowed"})
            origins = {v for v in os.environ.get("HUB_STATE_PROBE_ORIGINS", "").split(",")
                       if v.startswith("https://") and "/" not in v[8:]}
            if (environ.get("HTTP_ORIGIN") not in origins
                    or environ.get("CONTENT_TYPE") != "application/json"
                    or environ.get("QUERY_STRING", "")):
                return respond("403 Forbidden", {"error": "save_origin_required"})
            length = int(environ.get("CONTENT_LENGTH", "0"))
            if not 1 <= length <= 400000:
                raise ValueError("invalid_assessment_save")
            payload = json.loads(environ["wsgi.input"].read(length))
            return respond("200 OK", assessment_save.save(assertion, payload, os.environ))
        except Busy:
            return respond("409 Conflict", {"error": "assessment_save_busy"})
        except LeaseLost:
            return respond("409 Conflict", {"error": "assessment_claim_expired"})
        except Conflict as error:
            code = str(error)
            if code not in {"submission_changed_reassess", "assessment_record_conflict"}:
                code = "assessment_save_conflict"
            return respond("409 Conflict", {"error": code})
        except ConnectionError as error:
            return respond("403 Forbidden" if str(error) == "sign_in_required"
                           else "503 Service Unavailable", {"error": str(error)})
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            # Only this module's fixed validation codes may cross the boundary.
            code = str(error)
            if not _re.fullmatch(r"[a-z0-9_]{1,64}", code):
                code = "invalid_assessment_save"
            return respond("400 Bad Request", {"error": code})
        except StoreUnavailable:
            return respond("503 Service Unavailable", {"error": "assessment_state_unavailable"})
        except Exception:
            return respond("503 Service Unavailable", {"error": "assessment_save_unavailable"})

    if path == "/api/assessment-run":
        from .adapters import assessment_provider
        from .adapters.hosted_agenda import ConnectionError
        import re as _re
        try:
            assertion = environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION")
            if method == "GET":
                if environ.get("QUERY_STRING", ""):
                    from urllib.parse import parse_qs
                    query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=1)
                    if set(query) != {"draft_for"} or len(query["draft_for"]) != 1:
                        raise ValueError("invalid_provider_request")
                    return respond("200 OK", assessment_provider.stored_draft_for(
                        assertion, query["draft_for"][0], os.environ))
                return respond("200 OK", assessment_provider.status(assertion, os.environ))
            if method != "POST":
                return respond("405 Method Not Allowed", {"error": "method_not_allowed"})
            origins = {v for v in os.environ.get("HUB_STATE_PROBE_ORIGINS", "").split(",")
                       if v.startswith("https://") and "/" not in v[8:]}
            if (environ.get("HTTP_ORIGIN") not in origins
                    or environ.get("CONTENT_TYPE") != "application/json"
                    or environ.get("QUERY_STRING", "")):
                return respond("403 Forbidden", {"error": "save_origin_required"})
            length = int(environ.get("CONTENT_LENGTH", "0"))
            if not 1 <= length <= 100000:
                raise ValueError("invalid_provider_request")
            payload = json.loads(environ["wsgi.input"].read(length))
            return respond("200 OK", assessment_provider.draft(assertion, payload, os.environ))
        except ConnectionError as error:
            return respond("403 Forbidden" if str(error) == "sign_in_required"
                           else "503 Service Unavailable", {"error": str(error)})
        except (ValueError, KeyError, TypeError, OverflowError) as error:
            code = str(error)
            if not _re.fullmatch(r"[a-z0-9_]{1,64}", code):
                code = "invalid_provider_request"
            return respond("400 Bad Request", {"error": code})
        except Exception:
            return respond("503 Service Unavailable", {"error": "provider_unavailable"})

    if path == "/api/submission-monitor":
        from .adapters.submission_monitor import read_status, scan_and_persist
        from .adapters.hosted_agenda import ConnectionError
        from .core.cloud_state import Busy, Conflict, LeaseLost, StoreUnavailable
        try:
            assertion = environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION")
            if method == "GET":
                from urllib.parse import parse_qs
                query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=2)
                if set(query) != {"date", "class_key"} or any(len(v) != 1 for v in query.values()):
                    raise ValueError()
                return respond("200 OK", read_status(assertion, query["date"][0],
                                                     query["class_key"][0], os.environ))
            if method != "POST":
                return respond("405 Method Not Allowed", {"error": "method_not_allowed"})
            # A scan persists observations, so it needs the same explicit
            # same-origin JSON gate as the other state-changing endpoints.
            origins = {v for v in os.environ.get("HUB_STATE_PROBE_ORIGINS", "").split(",")
                       if v.startswith("https://") and "/" not in v[8:]}
            if (environ.get("HTTP_ORIGIN") not in origins
                    or environ.get("CONTENT_TYPE") != "application/json"
                    or environ.get("QUERY_STRING", "")):
                return respond("403 Forbidden", {"error": "monitor_origin_required"})
            length = int(environ.get("CONTENT_LENGTH", "0"))
            if not 1 <= length <= 256:
                raise ValueError()
            data = json.loads(environ["wsgi.input"].read(length))
            if (not isinstance(data, dict) or set(data) != {"action", "date", "class_key"}
                    or data["action"] != "scan"):
                raise ValueError()
            return respond("200 OK", scan_and_persist(assertion, data["date"],
                                                      data["class_key"], os.environ))
        except Busy:
            return respond("409 Conflict", {"error": "monitor_scan_in_progress"})
        except (Conflict, LeaseLost):
            return respond("409 Conflict", {"error": "monitor_scan_conflict"})
        except StoreUnavailable:
            return respond("503 Service Unavailable", {"error": "monitor_state_unavailable"})
        except ConnectionError as error:
            return respond("403 Forbidden" if str(error) == "sign_in_required"
                           else "503 Service Unavailable",
                           {"error": str(error), "external_writes": 0})
        except (ValueError, KeyError, TypeError, OverflowError):
            return respond("400 Bad Request", {"error": "invalid_monitor_request"})
        except Exception:
            return respond("503 Service Unavailable", {"error": "monitor_unavailable"})

    # Remaining endpoints are read-only.
    if method not in ("GET", "HEAD"):
        return respond("405 Method Not Allowed", {"error": "method_not_allowed"},
                       extra=[("Allow", "GET, HEAD")])
    if path == "/healthz":
        return respond("200 OK", {"status": "ok", "external_writes": 0})
    if path == "/api/readiness":
        if method == "HEAD":
            return respond("200 OK", b"")  # health probes do not read school sources
        from .adapters.hosted_agenda import check_readiness
        return respond("200 OK", check_readiness(
            environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION"),
            teaching_provider_configured=teaching_provider is not None))
    if path == "/api/submission-inventory" and method == "GET":
        from urllib.parse import parse_qs
        from .adapters.submission_inventory import read_inventory
        from .adapters.hosted_agenda import ConnectionError
        try:
            query = parse_qs(environ.get("QUERY_STRING", ""), max_num_fields=2)
            if set(query) != {"date", "class_key"} or any(len(v) != 1 for v in query.values()):
                raise ValueError()
            return respond("200 OK", read_inventory(environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION"),
                query["date"][0], query["class_key"][0], os.environ))
        except ConnectionError as error:
            return respond("403 Forbidden" if str(error) == "sign_in_required" else "503 Service Unavailable",
                           {"error": str(error), "external_writes": 0})
        except ValueError:
            return respond("400 Bad Request", {"error": "invalid_submission_scope", "external_writes": 0})
        except Exception:
            return respond("503 Service Unavailable", {"error": "submission_source_unavailable", "external_writes": 0})
    if path == "/api/roster-sync" and method == "GET":
        from .adapters.roster_sync import read_roster_sync_status
        from .adapters.hosted_agenda import ConnectionError
        try:
            return respond("200 OK", read_roster_sync_status(
                environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION"), os.environ))
        except ConnectionError:
            return respond("403 Forbidden", {"error": "sign_in_required", "external_writes": 0})
        except Exception:
            return respond("503 Service Unavailable", {"error": "roster_sync_unavailable", "external_writes": 0})
    if path == "/api/grading-records" and method == "GET":
        from .adapters.grading_records import read_records
        from .adapters.hosted_agenda import ConnectionError
        if environ.get("QUERY_STRING", ""):
            return respond("400 Bad Request", {"error": "invalid_grading_scope", "external_writes": 0})
        try:
            return respond("200 OK", read_records(
                environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION"), os.environ))
        except ConnectionError as error:
            return respond("403 Forbidden" if str(error) == "sign_in_required" else "503 Service Unavailable",
                           {"error": str(error), "external_writes": 0})
        except Exception:
            return respond("503 Service Unavailable", {"error": "grading_records_unavailable", "external_writes": 0})
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
    if path == "/api/run-directions" and method == "GET":
        from .adapters.run_directions import read_values
        from .adapters.hosted_agenda import ConnectionError
        try:
            return respond("200 OK", read_values(environ.get("HTTP_X_GOOG_IAP_JWT_ASSERTION")))
        except ConnectionError as error:
            status = "403 Forbidden" if str(error) == "sign_in_required" else "503 Service Unavailable"
            return respond(status, {"error": str(error), "external_writes": 0})
        except Exception:
            # Never echo a configured value, even in a failure.
            return respond("503 Service Unavailable", {"error": "run_directions_unavailable", "external_writes": 0})
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
