"""Trusted version-only repair of already registered private archival records.

No Drive writes, new identities, submission events or feedback are created.
The two original record validators remain authoritative. Joint work has its
own contract and is deliberately outside this operation.
"""
from copy import deepcopy
import re

from . import google, named_archival_work as named, student_archive as a


KINDS = {
    named.KIND: ("archivenamedwork", "copied_file_revision"),
    a.IMPORTED_ORIGINAL: ("archiveimportedwork", "imported_file_revision"),
}
HISTORY_PREFIX = "archivekindreconciliation"


def _kind(record, scope):
    kind = record.get("source_kind")
    if kind not in KINDS:
        raise a.Held("Choose an existing named native archive or authenticated imported original.")
    prefix, revision_field = KINDS[kind]
    if kind == named.KIND:
        provenance = named.provenance(record.get("archive_provenance"))
        parts = [scope, record.get("class_key"), record.get("student_key"), provenance["original_id"]]
        original = record.get("original_file_revision") or {}
        if (not isinstance(original, dict) or original.get("id") != provenance["original_id"]
                or original.get("mimeType") != named.MIME or original.get("trashed") is not False
                or original.get("createdTime") != provenance["original_created_at"]
                or not original.get("modifiedTime")
                or not re.fullmatch(r"[1-9][0-9]*", str(original.get("version") or ""))):
            raise a.Held("The named archive needs its frozen original revision; do not substitute today's original.")
    else:
        provenance = a._import_provenance(record.get("import_provenance"))
        parts = [scope, record.get("class_key"), record.get("student_key"), provenance["original_url"],
                 provenance["revision"], provenance["attachment_path"]]
        if record.get("lesson_status") != "verified":
            raise a.Held("The imported original's existing source role is unverified.")
    if (record.get("record_key") != a.key(prefix, parts)
            or not isinstance(record.get("source_context"), dict)
            or record["source_context"].get("class_key") != record.get("class_key")):
        raise a.Held("The existing archival identity, source receipt or class context changed.")
    return revision_field


def _verify(meta, record, scope):
    if record["source_kind"] == named.KIND:
        named.verify(meta, record, scope=scope)
    else:
        a._verify_imported_original(meta, record)
    if meta.get("name") != record.get("name"):
        raise a.Held("The archived file name changed; a version-only repair cannot rename its record.")


def _snapshot(token, config, class_key, record_key):
    if not isinstance(record_key, str) or not re.fullmatch(r"(?:archivenamedwork|archiveimportedwork)_[a-f0-9]{64}", record_key):
        raise a.Held("Choose the exact existing named archive or imported-original record.")
    actual_root, course = a._location(token, config, class_key)
    scope, port = a._scope_root(config, actual_root), a.configured_store(config)
    index_key = a.key("archiveclass", [scope, class_key])
    initial = port.read([index_key, record_key])
    index, doc = (initial.documents[k] for k in (index_key, record_key))
    record = doc.value if doc else {}
    owner_key = record.get("student_key")
    if not isinstance(owner_key, str) or not re.fullmatch(r"archivestudent_[a-f0-9]{64}", owner_key):
        raise a.Held("The saved student identity is unavailable; preserve the existing history.")
    owner_doc = a._read(port, owner_key)
    owner = owner_doc.value if owner_doc else {}
    if (not index or index.value.get("schema") != a.SCHEMA or not doc or not owner_doc
            or record.get("schema") != a.SCHEMA or record.get("status") != "verified"
            or record.get("record_key") != record_key or record.get("class_key") != class_key
            or record.get("kind") != "work" or owner.get("schema") != a.SCHEMA
            or owner.get("status") != "verified" or owner.get("record_key") != owner_key
            or owner.get("student_key") != owner_key or owner.get("class_key", class_key) != class_key
            or index.value.get("students", {}).get(owner_key) != owner.get("drive_file_id")
            or owner.get("items", {}).get(record_key) != {"kind": "work", "date": record.get("date")}
            or record.get("parent") != owner.get("drive_file_id")):
        raise a.Held("The archival record and its existing private owner/index do not agree.")
    revision_field = _kind(record, scope)
    section = a._private(google.drive_file(token, owner["parent"], a.FIELDS), parent=course["id"], folder=True)
    folder = a._private(google.drive_file(token, owner["drive_file_id"], a.FIELDS), parent=section["id"], folder=True)
    if (folder.get("properties") or {}).get(a.TAG) != owner_key:
        raise a.Held("The existing private student folder identity changed.")
    meta = google.drive_file(token, record["drive_file_id"], a.FIELDS)
    revision = record.get(revision_field)
    if (not isinstance(revision, dict)
            or not re.fullmatch(r"[1-9][0-9]*", str(revision.get("version") or ""))
            or not re.fullmatch(r"[1-9][0-9]*", str(meta.get("version") or ""))
            or int(meta["version"]) < int(revision["version"])):
        raise a.Held("The archived file's metadata revision moved backward or is unavailable.")
    candidate = {**record, revision_field: {**revision, "version": meta["version"]}}
    # Only a version substitution in memory is permitted. The unchanged original
    # validator still checks timestamps, parent, complete ACLs, tags and role.
    _verify(meta, candidate, scope)
    frozen = record.get("copied_content_proof")
    if not isinstance(frozen, dict) or a._work_content(token, meta) != frozen:
        raise a.Held("The archived file differs from its frozen content proof. Preserve the history; metadata repair cannot clear it.")
    documents = {index_key: index, owner_key: owner_doc, record_key: doc}
    metadata = [course, section, folder, meta]
    after = port.read(list(documents))
    if (after.documents != documents
            or any(google.drive_file(token, item["id"], a.FIELDS) != item for item in metadata)):
        raise a.Held("The archived file or private ownership changed during proof verification. Reread before applying.")
    proof = {"class_key": class_key, "record_key": record_key, "source_kind": record["source_kind"],
        "drive_file_id": meta["id"], "revision_field": revision_field,
        "from_version": revision["version"], "to_version": meta["version"], "content_proof": frozen,
        "documents": {k: {"version": v.version, "sha256": a.content_hash(v.value)} for k, v in documents.items()},
        "metadata_sha256": a.content_hash(metadata)}
    preview = {"status": "unchanged" if revision["version"] == meta["version"] else "ready",
               "revision": a.content_hash(proof), "proof": proof, "checked_at": after.read_time}
    return preview, documents, metadata, candidate, scope


def preview(token, config, class_key, record_key):
    """Read-only frozen-proof preview, using the registered kind's own validator."""
    return _snapshot(token, config, class_key, record_key)[0]


def reconcile(token, write_token, config, class_key, record_key, *, expected_revision,
              actor, before_source_write):
    """One guarded CAS repair and immutable prior-record history; no Drive writes.

    The trusted host supplies current source ownership/cutoff checks. Content
    proof and full paginated ACL reads finish before the archive lease starts.
    Every subsequent mutation uses the existing final class-owner/budget check.
    A lost CAS response is read back, never retried as a write.
    """
    if (not callable(before_source_write) or not isinstance(actor, str) or not actor
            or actor != a._clean(actor, 200) or not isinstance(expected_revision, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected_revision)):
        raise a.Held("Archive metadata repair requires its exact preview, attributed operator and source-owner guard.")
    checked, documents, metadata, candidate, scope = _snapshot(token, config, class_key, record_key)
    if checked["revision"] != expected_revision:
        raise a.Conflict("The archival metadata preview changed. Reread before applying.")
    if checked["status"] == "unchanged":
        return {"status": "verified", "class_key": class_key, "record_key": record_key,
                "source_kind": candidate["source_kind"], "changed": False}
    run = a._Run(token, write_token, config, class_key, before_source_write=before_source_write)
    if run.root != scope or run.course != metadata[0]:
        raise a.Held("The private archive configuration or course changed after the proof read.")
    history_key = a.key(HISTORY_PREFIX, [record_key, expected_revision])
    if a._read(run.port, history_key):
        raise a.Held("This repair already has a saved receipt. Read its outcome before further writes.")
    history = {"schema": "hub-archive-kind-reconciliation/v1", "record_key": history_key,
        "class_key": class_key, "source_kind": candidate["source_kind"], "actor": actor,
        "verified_at": checked["checked_at"], "preview_revision": expected_revision,
        "proof": checked["proof"], "before": deepcopy(documents[record_key].value),
        "verified_metadata": metadata}
    updated = {**candidate, "last_metadata_reconciliation": history_key}
    desired = {k: v.value for k, v in documents.items()}
    desired.update({record_key: updated, history_key: history})
    # Short source bookends are reads, outside the mutation reservation. Full
    # permissions were checked above and will be read back after the saved CAS.
    for item in metadata:
        fresh = google.drive_file(token, item["id"], a.SOURCE_FIELDS)
        if any(fresh.get(field) != item.get(field) for field in a.SOURCE_FIELDS.split(",")):
            raise a.Held("The archived file or destination changed after its proof. Preserve the saved record and reread.")
    if run.port.read(list(documents)).documents != documents:
        raise a.Conflict("The archived record or owner/index changed after its proof. Reread before applying.")
    run.heartbeat(force=True)
    try:
        run.port.commit({k: (v, documents[k].version if k in documents else None) for k, v in desired.items()})
    except (a.Conflict, a.StoreUnavailable):
        actual = run.port.read(list(desired)).documents
        if any(actual.get(k) is None or actual[k].value != v for k, v in desired.items()):
            raise
    actual = run.port.read(list(desired)).documents
    if any(actual.get(k) is None or actual[k].value != v for k, v in desired.items()):
        raise a.StoreUnavailable("archive_kind_reconciliation_readback_unconfirmed")
    for item in metadata:
        if google.drive_file(token, item["id"], a.FIELDS) != item:
            raise a.Held("The archived file or destination changed after the index save. Preserve the receipt and stop before further writes.")
    _verify(metadata[-1], actual[record_key].value, scope)
    result = {"status": "verified", "class_key": class_key, "record_key": record_key,
        "source_kind": candidate["source_kind"], "changed": True, "history_key": history_key,
        "from_version": checked["proof"]["from_version"], "to_version": checked["proof"]["to_version"]}
    run.finish(result)
    return result
