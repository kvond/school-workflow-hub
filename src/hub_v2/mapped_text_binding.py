"""Prospective, source-authenticated maps of existing direct text responses.

This is not a distribution registry, a question-source contract or a writer.
"""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re

from . import mapped_text_scene as s, response_binding as rb, writing_fields

KIND = s.KIND
REVIEW = "Explicit original text targets need review"
ID = re.compile(r"[A-Za-z0-9_-]{20,128}")


class ExportDeck(dict):
    """Private capture authority is intentionally absent from serialized JSON."""


@dataclass(frozen=True)
class _Capture:
    snapshot: str
    file_id: str
    revision: str
    family: bool


@dataclass(frozen=True)
class _Witness:
    snapshot: str
    file_id: str
    revision: str
    pages: tuple


@dataclass(frozen=True)
class _Reference:
    data: dict
    page: dict
    student_snapshot: str


def bound(page):
    return [u for u in (page or {}).get("units", []) if u.get("binding", {}).get("kind") == KIND]


def active(deck):
    if not deck: return False
    capture = getattr(deck, "_mapped_capture", None)
    return (any(bound(p) for p in deck["slides"]) or type(capture) is _Capture and capture.family
            or type(getattr(deck, "_mapped_witness", None)) is _Witness)


def capture_export(deck, before, after, raw):
    """Trusted host only: call with actual provider metadata bookends and bytes.

    No request/deck can supply these bookends. Normal/combined readers invoke
    this only after their actual provider reads. Authoring hosts do the same.
    """
    s.need(before == after and isinstance(before, dict) and ID.fullmatch(str(before.get("id", ""))))
    s.need(before.get("trashed") is False and str(before.get("version", "")).isdigit() and bool(before.get("modifiedTime")))
    s.need(before.get("mimeType") in ("application/vnd.google-apps.presentation", "application/vnd.openxmlformats-officedocument.presentationml.presentation"))
    s.need(isinstance(raw, bytes))
    if before["mimeType"].endswith("presentationml.presentation"):
        if before.get("md5Checksum"): s.need(hashlib.md5(raw).hexdigest() == before["md5Checksum"])
        if before.get("size") is not None: s.need(str(len(raw)) == str(before["size"]))
    pages = [p for p in deck["slides"] if type(p["units"]) is s.SceneUnits]
    s.need(pages)
    for page in pages:
        s.pptx(page)
        s.need(page["units"]._mapped_proof.raw_sha256 == hashlib.sha256(raw).hexdigest())
    result = ExportDeck(deepcopy(deck))
    result["explicit_source_id"] = before["id"]
    result["explicit_source_revision"] = str(before["version"])
    result._mapped_capture = _Capture(s.integrity(result), before["id"], str(before["version"]), any(bound(p) for p in result["slides"]))
    return result


def _capture(deck):
    cap = getattr(deck, "_mapped_capture", None)
    s.need(type(deck) is ExportDeck and type(cap) is _Capture and cap.snapshot == s.integrity(deck))
    s.need(deck.get("explicit_source_id") == cap.file_id and deck.get("explicit_source_revision") == cap.revision)
    return cap


def _page(deck, number):
    matches = [p for p in deck["slides"] if p["number"] == number]
    s.need(len(matches) == 1)
    return matches[0]


def _unit(page, bounds):
    matches = [u for u in page["units"] if s.box(u.get("box")) == bounds]
    s.need(len(matches) == 1 and not matches[0].get("picture"))
    return matches[0]


def _codes(page):
    from .completion import MARKER
    s.need(not any(re.search(r"\[\[(?:NOTES|DRAFT|BANK|OPTIONAL)\b", MARKER.sub("", u["text"])) for u in page["units"]))
    return [(m.group(1), m.group(2), m.group(3).strip(), s.box(u["box"]))
            for u in page["units"] for m in MARKER.finditer(u["text"])]


def _schema(data, page, size):
    s.need(isinstance(data, dict) and set(data) == {"kind", "source_id", "page_number", "page_size", "responses", "scene_sha256"})
    s.need(data["kind"] == KIND and ID.fullmatch(str(data["source_id"])) and type(data["page_number"]) is int)
    s.need(data["page_number"] == page["number"] and data["page_size"] == list(size))
    rows = data["responses"]
    s.need(isinstance(rows, list) and 1 <= len(rows) <= 2)
    s.need(isinstance(data["scene_sha256"], dict) and set(data["scene_sha256"]) == {"native", "pptx"})
    s.need(all(re.fullmatch(r"[a-f0-9]{64}", str(v)) for v in data["scene_sha256"].values()))
    identities, fields = [], []
    for row in rows:
        s.need(isinstance(row, dict) and set(row) == {"category", "cycle", "id", "marker_box", "target"})
        s.need(row["category"] in ("NOTES", "DRAFT") and isinstance(row["cycle"], str) and isinstance(row["id"], str))
        field = row["target"]
        s.need(isinstance(field, dict) and set(field) == {"box", "given"} and isinstance(field["given"], str) and len(field["given"]) <= 4000)
        s.need(rb.rectangle(field["box"], size) and rb.rectangle(row["marker_box"], size))
        s.need(s.box(field["box"]) == field["box"] and s.box(row["marker_box"]) == row["marker_box"])
        identities.append((row["category"], row["cycle"], row["id"]))
        fields.append(field)
    s.need([i[0] for i in identities] == (["NOTES"] if len(rows) == 1 else ["NOTES", "DRAFT"]))
    s.need(len({i[1:] for i in identities}) == 1)
    s.need(len({tuple(f["box"]) for f in fields}) == len(fields))
    s.need(sorted(_codes(page)) == sorted((*ident, row["marker_box"]) for ident, row in zip(identities, rows)))
    return fields


def _validate(data, deck, page, *, original=False):
    from .completion import _plain
    fields = _schema(data, page, deck["size"])
    for row in data["responses"]:
        unit = _unit(page, row["target"]["box"])
        if original: s.need(_plain(unit["text"]) == row["target"]["given"])
    fingerprint, semantic = s.scene(deck, page, fields)
    form = "native" if "native_source" in deck else "pptx"
    s.need(fingerprint == data["scene_sha256"][form])
    return fingerprint, semantic


def _carrier(page, data):
    labels = bound(page)
    s.need(len(labels) == 1 and s.box(labels[0]["box"]) == data["responses"][0]["marker_box"])


def author(native, exported, *, targets):
    try:
        return _author(native, exported, targets=targets)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
        raise ValueError(REVIEW) from None


def _author(native, exported, *, targets):
    """Pure authoring from bookended same-file captures and audited native keys.

    targets is [{marker_key: existing key, target_key: existing key}, ...].
    This creates no role, content, metadata attachment or host authorization.
    """
    from .completion import MARKER, _plain
    cap = _capture(exported)
    s.need(native.get("source_kind") == "native_teacher_source")
    s.need(native.get("native_source_id") == cap.file_id and native.get("native_source_revision") == cap.revision)
    s.need(native["size"] == exported["size"] and len(native["slides"]) == len(exported["slides"]))
    s.need(not any(u.get("binding", {}).get("kind") == "above-field-text/v1"
                   for d in (native, exported) for p in d["slides"] for u in p["units"]))
    s.need(isinstance(targets, (list, tuple)) and 1 <= len(targets) <= 2)
    rows, pages = [], set()
    for selected in targets:
        s.need(isinstance(selected, dict) and set(selected) == {"marker_key", "target_key"})
        matches = [(p, u) for p in native["slides"] for u in p["units"] if u["key"] == selected["marker_key"]]
        s.need(len(matches) == 1)
        page, marker = matches[0]; pages.add(page["number"])
        target = [u for u in page["units"] if u["key"] == selected["target_key"]]
        s.need(len(target) == 1)
        codes = list(MARKER.finditer(marker["text"])); s.need(len(codes) == 1)
        category, cycle, ident = codes[0].groups()
        rows.append({"category": category, "cycle": cycle, "id": ident.strip(), "marker_box": s.box(marker["box"]),
                     "target": {"box": s.box(target[0]["box"]), "given": _plain(target[0]["text"])}})
    s.need(len(pages) == 1)
    rows.sort(key=lambda r: 0 if r["category"] == "NOTES" else 1)
    page = _page(native, next(iter(pages)))
    # Existing descriptions are never overwritten by this contract's carrier.
    raw_leaves, _ = s.native(native, page)
    carrier = [u for u in raw_leaves if u["box"] == rows[0]["marker_box"]]
    s.need(len(carrier) == 1 and carrier[0]["depth"] == 0 and not carrier[0]["node"].get("description"))
    data = {"kind": KIND, "source_id": cap.file_id, "page_number": page["number"], "page_size": list(native["size"]),
            "responses": rows, "scene_sha256": {"native": "0"*64, "pptx": "0"*64}}
    fields = _schema(data, page, native["size"])
    nf, ns = s.scene(native, page, fields)
    other = _page(exported, page["number"]); _schema(data, other, exported["size"])
    pf, ps = s.scene(exported, other, fields)
    s.need(ns == ps)
    data["scene_sha256"] = {"native": nf, "pptx": pf}
    for deck, candidate in ((native, page), (exported, other)): _validate(data, deck, candidate, original=True)
    return data


def with_export_witness(native, exported):
    try:
        return _with_export_witness(native, exported)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
        raise ValueError(REVIEW) from None


def _with_export_witness(native, exported):
    cap = _capture(exported)
    s.need(native.get("source_kind") in ("native_teacher_source", "native_student_source"))
    s.need(native.get("native_source_id") == cap.file_id and native.get("native_source_revision") == cap.revision)
    s.need(native["size"] == exported["size"] and len(native["slides"]) == len(exported["slides"]))
    entries = []
    for page, other in zip(native["slides"], exported["slides"]):
        s.need(page["number"] == other["number"])
        s.need([(u["text"], s.box(u["box"]), bool(u.get("picture"))) for u in page["units"]] ==
               [(u["text"], s.box(u["box"]), bool(u.get("picture"))) for u in other["units"]])
        labels, other_labels = bound(page), bound(other)
        if not labels and not other_labels: continue
        s.need(len(labels) == len(other_labels) == 1 and labels[0]["binding"] == other_labels[0]["binding"])
        data = labels[0]["binding"]
        _carrier(page, data); _carrier(other, data)
        _validate(data, native, page)
        pf, _ = _validate(data, exported, other)
        entries.append((page["number"], pf, s.digest(data)))
    s.need(entries)
    result = ExportDeck(deepcopy(native))
    result._mapped_witness = _Witness(s.integrity(result), cap.file_id, cap.revision, tuple(entries))
    return result


def _witnessed(deck, page, data):
    if "native_source" not in deck:
        _capture(deck); return
    witness = getattr(deck, "_mapped_witness", None)
    s.need(type(deck) is ExportDeck and type(witness) is _Witness and witness.snapshot == s.integrity(deck))
    s.need(witness.file_id == deck["native_source_id"] and witness.revision == deck["native_source_revision"])
    s.need((page["number"], data["scene_sha256"]["pptx"], s.digest(data)) in witness.pages)


def plan(student, template):
    """The fresh registered teacher's map, never a submitted map, drives roles."""
    try:
        s.need(template and student["size"] == template["size"] and len(student["slides"]) == len(template["slides"]))
        expected = [(p, u) for p in template["slides"] for u in bound(p)]
        received = [(p, u) for p in student["slides"] for u in bound(p)]
        s.need(expected and len(expected) == len(received))
        # First version leaves mixed explicit families held rather than relaxing
        # either family's full-scene rules.
        s.need(not any(u.get("binding", {}).get("kind") == "above-field-text/v1"
                       for deck in (template, student) for p in deck["slides"] for u in p["units"]))
        references = {}
        for page, carrier in expected:
            s.need(len(bound(page)) == 1 and page["number"] not in references)
            data = carrier["binding"]
            _carrier(page, data)
            source_id = template.get("native_source_id", template.get("explicit_source_id"))
            s.need(data.get("source_id") == source_id)
            _witnessed(template, page, data); _validate(data, template, page, original=True)
            matches = [(p, u) for p, u in received if p["number"] == page["number"] and u.get("binding") == data]
            s.need(len(matches) == 1)
            other = matches[0][0]; _carrier(other, data); _witnessed(student, other, data); _validate(data, student, other)
            references[page["number"]] = _Reference(deepcopy(data), deepcopy(page), s.integrity(other))
        return references, False
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError):
        return {}, True


def _own_label_echo(row, template, current, words, plain):
    """Only a proven own canonical role scaffold, never an arbitrary prompt."""
    from .completion import MARKER
    from .legacy_label_binding import ROLE
    if row["marker_box"] == row["target"]["box"]: return False
    source = _unit(template, row["marker_box"])
    codes = [(m.group(1), m.group(2), m.group(3).strip()) for m in MARKER.finditer(source["text"])]
    if codes != [(row["category"], row["cycle"], row["id"])]: return False
    label = " ".join(plain(source["text"]).split())
    role = ROLE.fullmatch(label)
    if not role or row["category"] != ("NOTES" if role[2].lower() == "first" else "DRAFT"): return False
    if role[1] and role[3] and role[1] != role[3]: return False
    # Only after proving the exact own canonical role, punctuation/quotes are
    # not new learner words. Keep the ordinary source/short-choice rule intact.
    signature = lambda text: [token for token in writing_fields.tokens(text) if re.fullmatch(r"\w+", token)]
    supplied = signature(label)
    return supplied in (signature(words), signature(plain(current["text"])))


def answers(markers, written, template, student, size, plain, *, reference=None):
    from .completion import COPY_WORDS
    result = {(m["category"], m["id"]): [] for m in markers}
    bad = (result, list(written), REVIEW)
    try:
        s.need(type(reference) is _Reference and reference.page == template and student
               and reference.student_snapshot == s.integrity(student))
        data = reference.data
        s.need(len(bound(student)) == 1 and bound(student)[0]["binding"] == data)
        for row in data["responses"]:
            unit = _unit(student, row["target"]["box"])
            words = writing_fields.new_text(row["target"]["given"], plain(unit["text"]))
            if words:
                evidence = {**unit, "verified_workspace_answer": words}
                if (_own_label_echo(row, template, unit, words, plain) or
                        len(words.split()) >= COPY_WORDS and writing_fields.tokens(words) in [writing_fields.tokens(plain(u["text"]))
                        for u in template["units"] if plain(u["text"])]):
                    evidence["workspace_supplied_copy"] = True
                result[(row["category"], row["id"])].append((evidence, words))
        targets = {id(_unit(student, row["target"]["box"])) for row in data["responses"]}
        return result, [item for item in written if id(item[0]) not in targets], "authenticated mapped text targets"
    except (ValueError, TypeError, KeyError, IndexError):
        return bad
