"""Turning identifiers, URLs and citations into Zotero items, and finding free PDFs.

Metadata comes from Zotero's own translation-server (the engine behind "Add
Item by Identifier" and the browser connector), so items look exactly as if
Zotero had created them. Open-access PDFs come from links the translators
report, from direct PDF URLs, and from Unpaywall for DOIs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

MAX_PDF_BYTES = 150 * 1024 * 1024
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.I)
# fields translation-server returns that the Zotero web API won't accept on items
NON_API_FIELDS = {"key", "version", "attachments", "notes", "seeAlso", "id", "libraryCatalog_", "complete"}


@dataclass
class Resolution:
    query: str
    items: list[dict] = field(default_factory=list)  # Zotero item data (no key)
    pdf_urls: list[str] = field(default_factory=list)
    choices: dict[str, str] = field(default_factory=dict)  # when the query was ambiguous: id -> title
    error: str | None = None


def normalise_doi(s: str | None) -> str:
    if not s:
        return ""
    m = DOI_RE.search(s)
    return m.group(1).rstrip(".,;)").lower() if m else ""


def isbn_digits(s: str | None) -> set[str]:
    """All ISBNs in a field, as ISBN-13 digit strings."""
    out = set()
    for raw in re.findall(r"[0-9Xx][0-9Xx\- ]{8,16}[0-9Xx]", s or ""):
        d = re.sub(r"[^0-9Xx]", "", raw).upper()
        if len(d) == 10:
            core = "978" + d[:9]
            check = (10 - sum((1 if i % 2 == 0 else 3) * int(c) for i, c in enumerate(core)) % 10) % 10
            out.add(core + str(check))
        elif len(d) == 13:
            out.add(d)
    return out


def normalise_title(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def looks_like_identifier(q: str) -> bool:
    q = q.strip()
    return bool(DOI_RE.search(q) or re.fullmatch(r"(?i)(isbn[: ]*)?[0-9Xx\- ]{10,17}", q) or re.fullmatch(r"(?i)(arxiv:)?\d{4}\.\d{4,5}(v\d+)?", q)
                or re.fullmatch(r"(?i)pmid:?\s*\d+", q))


class Resolver:
    def __init__(self, translation_url: str, contact_email: str = "", *, client: httpx.Client | None = None):
        self.base = translation_url.rstrip("/")
        self.email = contact_email
        self.http = client or httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": "rmzotero (+https://github.com/zsrobinson/rmproxy-zotero)"})

    # -- metadata ------------------------------------------------------------------
    def resolve(self, query: str) -> Resolution:
        q = query.strip()
        res = Resolution(q)
        try:
            if re.match(r"https?://", q, re.I):
                r = self.http.post(f"{self.base}/web", content=q.encode(), headers={"Content-Type": "text/plain"})
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/pdf"):
                    res.pdf_urls.append(q)
                    return res
            else:
                r = self.http.post(f"{self.base}/search", content=q.encode(), headers={"Content-Type": "text/plain"})
        except httpx.HTTPError as e:
            res.error = f"translation-server unreachable: {e}"
            return res
        if r.status_code == 300:
            body = r.json()
            items = body.get("items", body) if isinstance(body, dict) else body
            if isinstance(items, dict):
                res.choices = {str(k): (v if isinstance(v, str) else (v or {}).get("title", str(k))) for k, v in items.items()}
            elif isinstance(items, list):
                res.choices = {str(i.get("DOI") or i.get("key") or n): i.get("title", "") for n, i in enumerate(items)}
            return res
        if r.status_code == 501:
            res.error = "no translator could read this (try a DOI, ISBN or a link to the article page)"
            return res
        if r.status_code >= 400:
            res.error = f"translation-server answered HTTP {r.status_code}: {r.text[:200]}"
            return res
        for raw in r.json() if isinstance(r.json(), list) else []:
            if raw.get("itemType") in ("attachment", "note"):
                continue
            for a in raw.get("attachments") or []:
                if (a.get("mimeType") or "").lower() == "application/pdf" and a.get("url"):
                    res.pdf_urls.append(a["url"])
            res.items.append(clean_item(raw))
        if re.match(r"https?://", q, re.I) and q.lower().split("?")[0].endswith(".pdf"):
            res.pdf_urls.insert(0, q)
        return res

    # -- PDFs ------------------------------------------------------------------------
    def open_access_pdf_urls(self, doi: str) -> list[str]:
        if not doi or not self.email:
            return []
        try:
            r = self.http.get(f"https://api.unpaywall.org/v2/{doi}", params={"email": self.email})
            if r.status_code != 200:
                return []
            data = r.json()
        except (httpx.HTTPError, ValueError):
            return []
        urls = []
        for loc in [data.get("best_oa_location")] + list(data.get("oa_locations") or []):
            if loc and loc.get("url_for_pdf") and loc["url_for_pdf"] not in urls:
                urls.append(loc["url_for_pdf"])
        return urls

    def fetch_pdf(self, url: str) -> bytes | None:
        try:
            with self.http.stream("GET", url) as r:
                if r.status_code != 200:
                    return None
                data = bytearray()
                for chunk in r.iter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_PDF_BYTES:
                        return None
        except httpx.HTTPError:
            return None
        return bytes(data) if data[:5] == b"%PDF-" else None


def clean_item(raw: dict) -> dict:
    item = {k: v for k, v in raw.items() if k not in NON_API_FIELDS and v not in (None, "")}
    item.setdefault("tags", [])
    item["tags"] = [t if isinstance(t, dict) else {"tag": str(t)} for t in item["tags"]]
    item.setdefault("creators", [])
    return item
