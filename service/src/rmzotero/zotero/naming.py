"""Document names following Zotero's file renaming template.

The default template (Zotero 7+) is::

    {{ firstCreator suffix=" - " }}{{ year suffix=" - " }}{{ title truncate="100" }}

The plugin can send the user's own template; a practical subset of the syntax
is supported: variables with ``prefix``, ``suffix``, ``truncate``, ``case``
(lower/upper/title) and ``start``/``join`` are ignored gracefully.
"""

from __future__ import annotations

import re

DEFAULT_TEMPLATE = '{{ firstCreator suffix=" - " }}{{ year suffix=" - " }}{{ title truncate="100" }}'

PRIMARY_CREATOR = {"book": "author", "bookSection": "author", "journalArticle": "author", "thesis": "author"}


def _creators(item: dict) -> list[dict]:
    creators = item.get("creators") or []
    primary = PRIMARY_CREATOR.get(item.get("itemType", ""), "author")
    main = [c for c in creators if c.get("creatorType") == primary]
    if not main:
        main = [c for c in creators if c.get("creatorType") == "editor"] or creators
    return main


def _last(c: dict) -> str:
    return c.get("lastName") or c.get("name") or ""


def first_creator(item: dict) -> str:
    cs = [c for c in _creators(item) if _last(c)]
    if not cs:
        return ""
    if len(cs) == 1:
        return _last(cs[0])
    if len(cs) == 2:
        return f"{_last(cs[0])} and {_last(cs[1])}"
    return f"{_last(cs[0])} et al."


def year(item: dict) -> str:
    m = re.search(r"\b(\d{4})\b", item.get("date") or "")
    return m.group(1) if m else ""


def _vars(item: dict) -> dict[str, str]:
    return {
        "firstCreator": first_creator(item),
        "year": year(item),
        "title": (item.get("title") or "").strip(),
        "shortTitle": (item.get("shortTitle") or item.get("title") or "").strip(),
        "publicationTitle": item.get("publicationTitle") or "",
        "itemType": item.get("itemType") or "",
    }


_TOKEN = re.compile(r"\{\{\s*(\w+)((?:\s+\w+=\"[^\"]*\")*)\s*\}\}")
_PARAM = re.compile(r"(\w+)=\"([^\"]*)\"")


def render(item: dict, template: str | None = None) -> str:
    template = template or DEFAULT_TEMPLATE
    values = _vars(item)

    def sub(m: re.Match) -> str:
        name, params = m.group(1), dict(_PARAM.findall(m.group(2) or ""))
        v = values.get(name, "")
        if not v:
            return ""
        if "truncate" in params:
            try:
                v = v[: int(params["truncate"])].rstrip()
            except ValueError:
                pass
        case = params.get("case")
        if case == "lower":
            v = v.lower()
        elif case == "upper":
            v = v.upper()
        elif case == "title":
            v = v.title()
        return params.get("prefix", "") + v + params.get("suffix", "")

    out = _TOKEN.sub(sub, template)
    out = re.sub(r"\s+", " ", out).strip(" -_")
    return out or (item.get("title") or "Untitled")


def document_names(item: dict | None, attachments: list[dict], template: str | None = None) -> dict[str, str]:
    """Names for each synced attachment of an item (ADR 0011: suffix when there are several)."""
    if item is None:  # standalone attachments are named by their own title
        return {a["key"]: (a.get("title") or a.get("filename") or "Untitled") for a in attachments}
    base = render(item, template)
    if len(attachments) <= 1:
        return {a["key"]: base for a in attachments}
    titles = [a.get("title") or "" for a in attachments]
    out = {}
    for a, t in zip(attachments, titles):
        label = t if t and titles.count(t) == 1 else (a.get("filename") or a["key"])
        out[a["key"]] = f"{base} — {label}"
    return out
