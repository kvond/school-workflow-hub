"""Small IAM-authenticated Firestore REST adapter for the shared state contract.

Only fixed Google HTTPS endpoints and an explicit project/database/collection.
No browser tokens, credential files, redirects, unbounded reads or fallback DB.
"""
import json
import os
import re

from school_workflow_hub.core.cloud_state import (
    Conflict, Document, Snapshot, StoreUnavailable, bounded_json, instant,
)

API = "https://firestore.googleapis.com/v1/"
SCOPE = "https://www.googleapis.com/auth/datastore"
MAX_RESPONSE = 6_000_000


def attached_token(expected_email):
    from .hosted_agenda import _request
    try:
        from google.auth import compute_engine
        credential = compute_engine.Credentials(scopes=[SCOPE])
        credential.refresh(_request())
        if credential.service_account_email != expected_email or not credential.token:
            raise ValueError()
        return credential.token
    except Exception:
        raise StoreUnavailable("state_runtime_identity_unavailable") from None


class FirestoreState:
    def __init__(self, project, *, database="(default)", collection="hub_state_v1",
                 token_provider, transport=None):
        if (not isinstance(project, str) or not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project)
                or not isinstance(database, str)
                or not re.fullmatch(r"\(default\)|[a-z][a-z0-9-]{2,61}[a-z0-9]", database)
                or collection != "hub_state_v1"):
            raise ValueError("invalid_state_location")
        self.root = "projects/" + project + "/databases/" + database + "/documents"
        self.prefix = self.root + "/" + collection + "/"
        self.token_provider = token_provider
        self.transport = transport or self._http

    def _name(self, record_id):
        if not isinstance(record_id, str) or not re.fullmatch(r"[a-z]+_[a-f0-9]{64}", record_id):
            raise ValueError("invalid_state_record_id")
        return self.prefix + record_id

    def _http(self, operation, body, *, before_write=None):
        import requests
        response = None
        # Authentication can require several provider reads. It must not spend
        # a source-write reservation before the actual one-shot CAS starts.
        try:
            token = self.token_provider()
        except Exception:
            raise StoreUnavailable("state_service_unavailable") from None
        if before_write is not None:
            before_write()  # Preserve ownership/cutoff exceptions; no request sent.
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.post(API + self.root + ":" + operation, json=body,
                    headers={"Authorization": "Bearer " + token},
                    timeout=(5, 15), allow_redirects=False, stream=True)
                if response.status_code in (409, 412):
                    raise Conflict("state_version_conflict")
                if response.status_code == 400:
                    error = json.loads(response.raw.read(4096, decode_content=True))
                    if error.get("error", {}).get("status") == "FAILED_PRECONDITION":
                        raise Conflict("state_version_conflict")
                if response.status_code != 200:
                    raise StoreUnavailable("state_service_unavailable")
                chunks, size = [], 0
                for chunk in response.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > MAX_RESPONSE:
                        raise StoreUnavailable("state_response_limit")
                    chunks.append(chunk)
                return json.loads(b"".join(chunks))
        except Conflict:
            raise
        except Exception:
            # Includes uncertain commits: caller must reread/retry the SAME key.
            raise StoreUnavailable("state_service_unavailable") from None
        finally:
            if response is not None:
                response.close()

    def read(self, record_ids):
        if (not isinstance(record_ids, (list, tuple)) or not 1 <= len(record_ids) <= 40
                or len(set(record_ids)) != len(record_ids)):
            raise ValueError("invalid_state_read_batch")
        names = {self._name(record_id): record_id for record_id in record_ids}
        rows = self.transport("batchGet", {"documents": list(names)})
        documents, times = {}, []
        try:
            if not isinstance(rows, list):
                raise ValueError()
            for row in rows:
                name = row.get("missing") or row.get("found", {}).get("name")
                if name not in names or names[name] in documents:
                    raise ValueError()
                instant(row["readTime"])
                times.append(row["readTime"])
                if "missing" in row:
                    if "found" in row:
                        raise ValueError()
                    documents[names[name]] = None
                else:
                    data = row["found"]
                    instant(data["updateTime"])
                    value = json.loads(data["fields"]["payload"]["stringValue"])
                    if not isinstance(value, dict):
                        raise ValueError()
                    bounded_json(value)
                    documents[names[name]] = Document(value, data["updateTime"])
            if len(documents) != len(names):
                raise ValueError()
            # Firestore guarantees prior rows unchanged through later read times.
            return Snapshot(documents, max(times, key=instant))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise StoreUnavailable("invalid_state_snapshot") from None

    def commit(self, changes):
        return self._commit(changes)

    def commit_guarded(self, changes, *, before_write):
        """One trusted CAS, guarded after token acquisition; never retry it.

        The guard's own lease/control port must remain unwrapped. Injected
        transports must implement their own bounded request/authentication.
        """
        if not callable(before_write):
            raise ValueError("invalid_state_write_guard")
        return self._commit(changes, before_write=before_write)

    def _commit(self, changes, *, before_write=None):
        if not isinstance(changes, dict) or not 1 <= len(changes) <= 40:
            raise ValueError("invalid_state_write_batch")
        writes = []
        for record_id, (value, version) in changes.items():
            if not isinstance(value, dict):
                raise ValueError("state_record_must_be_object")
            if version is not None:
                instant(version)
            writes.append({"update": {"name": self._name(record_id), "fields": {
                "payload": {"stringValue": bounded_json(value)}}},
                "currentDocument": {"exists": False} if version is None else {"updateTime": version}})
        body = {"writes": writes}
        if len(json.dumps(body).encode()) > 4_000_000:
            raise ValueError("state_commit_limit")
        if before_write is None:
            result = self.transport("commit", body)
        elif self.transport == self._http:
            result = self._http("commit", body, before_write=before_write)
        else:
            before_write()
            result = self.transport("commit", body)
        try:
            instant(result["commitTime"])
            if len(result["writeResults"]) != len(writes):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise StoreUnavailable("state_commit_unconfirmed") from None


def configured_store(config=None):
    config = os.environ if config is None else config
    if config.get("HUB_STATE_BACKEND") != "firestore":
        raise StoreUnavailable("state_not_configured")
    email = config.get("HUB_STATE_SERVICE_ACCOUNT", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]+@[A-Za-z0-9.-]+\.gserviceaccount\.com", email):
        raise StoreUnavailable("state_not_configured")
    try:
        return FirestoreState(config.get("HUB_STATE_PROJECT", ""),
            database=config.get("HUB_STATE_DATABASE", "(default)"),
            token_provider=lambda: attached_token(email))
    except ValueError:
        raise StoreUnavailable("state_not_configured") from None
