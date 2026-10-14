"""Read-only Schoology REST v1 client.

Authentication is Schoology's two-legged OAuth 1.0a with the teacher's API key
and secret (Schoology > your district domain > /api). PLAINTEXT signing over HTTPS
is what Schoology documents for two-legged requests.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import requests

from preflight.models import SchoologyItem
from preflight.sources.gdrive import find_urls

API = "https://api.schoology.com/v1"


class SchoologySource(Protocol):
    def items(self, section_id: str, start: dt.date, end: dt.date) -> list[SchoologyItem]: ...


def _parse_due(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _attachment_urls(obj: dict) -> list[str]:
    urls: list[str] = []
    att = obj.get("attachments") or {}
    for link in (att.get("links") or {}).get("link", []) or []:
        if link.get("url"):
            urls.append(link["url"])
    for f in (att.get("files") or {}).get("file", []) or []:
        if f.get("download_path"):
            urls.append(f["download_path"])
    for embed in (att.get("embeds") or {}).get("embed", []) or []:
        urls.extend(find_urls(embed.get("embed", "")))
    urls.extend(find_urls(obj.get("description") or obj.get("body") or ""))
    if obj.get("url"):
        urls.append(obj["url"])
    seen: list[str] = []
    for u in urls:
        if u not in seen:
            seen.append(u)
    return seen


class SchoologyAPI:
    def __init__(self, key: str, secret: str, session: requests.Session | None = None):
        self._key = key
        self._secret = secret
        self._http = session or requests.Session()

    def _auth_header(self) -> str:
        params = {
            "oauth_consumer_key": self._key,
            "oauth_nonce": secrets.token_hex(8),
            "oauth_signature_method": "PLAINTEXT",
            "oauth_timestamp": str(int(time.time())),
            "oauth_token": "",
            "oauth_version": "1.0",
            "oauth_signature": f"{quote(self._secret, safe='')}&",
        }
        return "OAuth realm=\"Schoology API\", " + ", ".join(f'{k}="{v}"' for k, v in params.items())

    def _get(self, path: str, **params) -> dict:
        resp = self._http.get(
            f"{API}{path}",
            params=params,
            headers={"Authorization": self._auth_header(), "Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _paged(self, path: str, key: str, **params) -> list[dict]:
        out: list[dict] = []
        start = 0
        while True:
            data = self._get(path, start=start, limit=200, **params)
            batch = data.get(key) or []
            out.extend(batch)
            total = int(data.get("total", len(out)) or len(out))
            start += len(batch)
            if not batch or start >= total:
                return out

    def items(self, section_id: str, start: dt.date, end: dt.date) -> list[SchoologyItem]:
        out: list[SchoologyItem] = []
        for a in self._paged(f"/sections/{section_id}/assignments", "assignment", with_attachments=1):
            due = _parse_due(a.get("due"))
            out.append(SchoologyItem(
                section=section_id, kind="assignment", title=a.get("title", ""),
                urls=_attachment_urls(a), due=due,
                published=str(a.get("published", "1")) == "1", item_id=str(a.get("id", "")),
            ))
        for d in self._paged(f"/sections/{section_id}/documents", "document", with_attachments=1):
            out.append(SchoologyItem(
                section=section_id, kind="document", title=d.get("title", ""),
                urls=_attachment_urls(d), item_id=str(d.get("id", "")),
            ))
        for p in self._paged(f"/sections/{section_id}/pages", "page", withcontent=1):
            out.append(SchoologyItem(
                section=section_id, kind="page", title=p.get("title", ""),
                urls=_attachment_urls(p), published=str(p.get("published", "1")) == "1",
                item_id=str(p.get("id", "")),
            ))
        return out


class SnapshotSchoology:
    """Reads schoology.json: {"<section_id>": [{kind, title, urls, due, published}, ...]}."""

    def __init__(self, path: Path):
        self._data = json.loads(Path(path).read_text()) if Path(path).exists() else {}

    def items(self, section_id: str, start: dt.date, end: dt.date) -> list[SchoologyItem]:
        return [
            SchoologyItem(
                section=section_id, kind=i.get("kind", "assignment"), title=i.get("title", ""),
                urls=i.get("urls", []), due=_parse_due(i.get("due")),
                published=i.get("published", True), item_id=i.get("id", ""),
            )
            for i in self._data.get(section_id, [])
        ]
