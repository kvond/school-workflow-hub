"""Shared IAP/runtime identity verification and metadata-only connection check."""
from datetime import datetime, timezone
import os
import re

from .google_sheets import SheetsReader, SheetsReadError

SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
IAP_CERTS = "https://www.gstatic.com/iap/verify/public_key"


class ConnectionError(ValueError):
    pass


def _request():
    from google.auth.transport.requests import Request
    import requests
    session = requests.Session()
    session.trust_env = False
    transport = Request(session=session)
    def bounded(*args, **kwargs):
        kwargs["timeout"] = 10
        kwargs["allow_redirects"] = False
        return transport(*args, **kwargs)
    return bounded


def authorize(assertion, audience, allowed_emails, request=None):
    if not isinstance(assertion, str) or not 1 <= len(assertion) <= 16000:
        raise ConnectionError("sign_in_required")
    try:
        from google.oauth2 import id_token
        claims = id_token.verify_token(assertion, request or _request(),
                                      audience=audience, certs_url=IAP_CERTS)
        if (claims.get("iss") != "https://cloud.google.com/iap"
                or claims.get("aud") != audience
                or not isinstance(claims.get("sub"), str) or not claims["sub"]
                or not isinstance(claims.get("email"), str)
                or claims["email"].lower() not in allowed_emails):
            raise ValueError("invalid identity")
    except Exception:
        raise ConnectionError("sign_in_required") from None


def service_token(expected_email):
    try:
        from google.auth import compute_engine
        # Use only the attached runtime identity, never a downloaded JSON key
        # or the caller's bearer token. Google obtains fresh short-lived tokens.
        credential = compute_engine.Credentials(scopes=[SCOPE])
        credential.refresh(_request())
        if credential.service_account_email != expected_email or not credential.token:
            raise ValueError("unexpected runtime identity")
        return credential.token
    except Exception:
        raise ConnectionError("runtime_identity_unavailable") from None


def _identity_configuration(config):
    audience = config.get("HUB_IAP_AUDIENCE", "")
    allowed = {v.strip().lower() for v in config.get("HUB_ALLOWED_EMAILS", "").split(",") if v.strip()}
    if (not re.fullmatch(r"/projects/[0-9]+/locations/[a-z0-9-]+/services/[a-z0-9-]+", audience)
            or not allowed or any("@" not in email for email in allowed)):
        raise ConnectionError("connection_not_configured")
    return audience, allowed


def _source_configuration(config):
    source = config.get("HUB_AGENDA_SPREADSHEET_ID", "")
    service_email = config.get("HUB_READER_SERVICE_ACCOUNT", "")
    if (config.get("HUB_AGENDA_READ_ENABLED") != "true"
            or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", source)
            or not service_email.endswith(".gserviceaccount.com")):
        raise ConnectionError("connection_not_configured")
    return source, service_email


def authenticated_reader(assertion, config=None):
    config = os.environ if config is None else config
    audience, allowed = _identity_configuration(config)
    source, service_email = _source_configuration(config)
    authorize(assertion, audience, allowed)
    token = service_token(service_email)
    return SheetsReader(token), source


def check_connection(assertion, config=None):
    reader, source = authenticated_reader(assertion, config)
    try:
        data = reader.read(source)  # metadata only, no ranges
    except SheetsReadError:
        raise ConnectionError("agenda_read_failed") from None
    return {
        "schema_version": "hub-agenda-connection/v1",
        "status": "metadata_read_verified",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "tab_count": len(data["sheets"]),
        "cell_data_loaded": False, "lesson_mapping_verified": False,
        "save_enabled": False, "run_enabled": False, "external_writes": 0,
    }


def check_readiness(assertion, config=None, *, teaching_provider_configured=False):
    """Report only this request's checks, with no student data or write actions.

    A configured provider or successful sign-in does not prove private storage,
    a dated lesson mapping, a school-system write, or an entire workflow.
    """
    config = os.environ if config is None else config
    checks = {name: {"status": "not_connected"} for name in (
        "private_storage", "shared_run_claims", "agenda_save", "submissions",
        "infinite_campus", "grade_posting", "feedback_filing", "parent_email", "printing",
    )}
    checks.update({
        "authentication": {"status": "not_configured"},
        "agenda_read": {"status": "not_checked"},
        "dated_lessons": {"status": "not_checked", "check_endpoint": "/api/agenda-day"},
        "prompts": {"status": "not_checked", "check_endpoint": "/api/agenda-prompts"},
        "teaching_results": {"status": "not_checked"},
    })
    result = {
        "schema_version": "hub-hosting-readiness/v2",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "mode": "not_connected", "authentication_verified": False,
        "school_connected": False, "school_connection_scope": "none",
        "persistent_storage_verified": False, "save_enabled": False,
        "run_enabled": False, "external_writes": 0, "checks": checks,
    }
    try:
        audience, allowed = _identity_configuration(config)
    except ConnectionError:
        return result
    try:
        authorize(assertion, audience, allowed)
    except ConnectionError:
        checks["authentication"]["status"] = "sign_in_required"
        return result
    checks["authentication"]["status"] = "verified"
    result.update(authentication_verified=True, mode="authenticated")
    checks["teaching_results"]["status"] = (
        "configured_not_verified" if teaching_provider_configured else "not_connected")
    try:
        source, service_email = _source_configuration(config)
    except ConnectionError:
        checks["agenda_read"]["status"] = "not_configured"
        return result
    try:
        token = service_token(service_email)
        data = SheetsReader(token).read(source)  # metadata only, no lesson cells
        if not isinstance(data.get("sheets"), list):
            raise ValueError("invalid metadata")
    except Exception:
        # Never return provider errors, source IDs, runtime identities or tokens.
        checks["agenda_read"]["status"] = "unavailable"
        return result
    checks["agenda_read"] = {"status": "verified", "evidence": "spreadsheet_metadata",
                             "tab_count": len(data["sheets"]), "cell_data_loaded": False}
    result.update(mode="connected_readonly", school_connected=True,
                  school_connection_scope="agenda_metadata_only")
    return result
