"""IAP-protected metadata pilot. No keys, school writes or cell reads."""
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


def check_connection(assertion, config=None):
    config = os.environ if config is None else config
    source = config.get("HUB_AGENDA_SPREADSHEET_ID", "")
    audience = config.get("HUB_IAP_AUDIENCE", "")
    service_email = config.get("HUB_READER_SERVICE_ACCOUNT", "")
    allowed = {v.strip().lower() for v in config.get("HUB_ALLOWED_EMAILS", "").split(",") if v.strip()}
    if (config.get("HUB_AGENDA_READ_ENABLED") != "true"
            or not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", source)
            or not re.fullmatch(r"/projects/[0-9]+/locations/[a-z0-9-]+/services/[a-z0-9-]+", audience)
            or not service_email.endswith(".gserviceaccount.com")
            or not allowed or any("@" not in email for email in allowed)):
        raise ConnectionError("connection_not_configured")
    authorize(assertion, audience, allowed)
    token = service_token(service_email)
    try:
        data = SheetsReader(token).read(source)  # metadata only, no ranges
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
