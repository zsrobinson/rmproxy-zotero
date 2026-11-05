"""Zotero's item schema: which fields and creator types each item type has.

Used to check items Claude creates or edits before they reach the web API, and to change an
item's type the way Zotero does: a field moves to the new type's field with the same base field
(a blog post's ``blogTitle`` and a web page's ``websiteTitle`` are both ``publicationTitle``),
fields the new type doesn't have are dropped, and creator roles it doesn't have become its
primary role. The full schema comes from https://api.zotero.org/schema; ``MINI`` is a faithful
subset for tests.
"""

from __future__ import annotations

NOT_REGULAR = {"attachment", "note", "annotation"}
ALWAYS_ALLOWED = {"extra"}  # every item type has it, whatever a trimmed schema says


class Schema:
    def __init__(self, data: dict):
        self.version = data.get("version")
        self.fields: dict[str, dict[str, str]] = {}  # item type -> field -> base field (itself if none)
        self.creators: dict[str, list[str]] = {}  # item type -> creator types, primary first
        for t in data.get("itemTypes") or []:
            name = t["itemType"]
            self.fields[name] = {f["field"]: f.get("baseField") or f["field"] for f in t.get("fields") or []}
            cts = t.get("creatorTypes") or []
            self.creators[name] = [c["creatorType"] for c in sorted(cts, key=lambda c: not c.get("primary"))]

    def item_types(self) -> list[str]:
        return sorted(t for t in self.fields if t not in NOT_REGULAR)

    def has_type(self, item_type: str) -> bool:
        return item_type in self.fields

    def valid_field(self, item_type: str, field: str) -> bool:
        return field in ALWAYS_ALLOWED or field in self.fields.get(item_type, {})

    def primary_creator(self, item_type: str) -> str:
        return (self.creators.get(item_type) or ["author"])[0]

    def unknown_fields(self, item_type: str, fields: dict) -> list[str]:
        return sorted(f for f in fields if not self.valid_field(item_type, f))

    def describe(self, item_type: str) -> str:
        return (f"{item_type} fields: {', '.join(sorted(self.fields.get(item_type, {})))}; "
                f"creator types: {', '.join(self.creators.get(item_type, []))}")

    def convert(self, item: dict, new_type: str) -> tuple[dict, dict, list[dict] | None]:
        """What changing ``item`` to ``new_type`` does: (fields to set under their new names,
        fields that are dropped with their values, creators with roles fixed, or None if unchanged)."""
        old = self.fields.get(item.get("itemType", ""), {})
        new = self.fields[new_type]
        by_base = {base: f for f, base in new.items()}
        moved, dropped = {}, {}
        for f, v in item.items():
            if f not in old or f in new or f in ALWAYS_ALLOWED or v in ("", None):
                continue
            target = by_base.get(old[f])
            if target and not item.get(target) and target not in moved:
                moved[target] = v
            else:
                dropped[f] = v
        creators = None
        allowed = set(self.creators.get(new_type, []))
        if allowed and any(c.get("creatorType") not in allowed for c in item.get("creators") or []):
            primary = self.primary_creator(new_type)
            creators = [{**c, "creatorType": c.get("creatorType") if c.get("creatorType") in allowed else primary}
                        for c in item.get("creators") or []]
        return moved, dropped, creators

    def apply_type_change(self, data: dict, new_type: str) -> dict:
        """The item as Zotero stores it after a type change (for the in-memory stand-in)."""
        moved, _dropped, creators = self.convert(data, new_type)
        old = self.fields.get(data.get("itemType", ""), {})
        out = {k: v for k, v in data.items() if not (k in old and not self.valid_field(new_type, k))}
        out.update(moved)
        out["itemType"] = new_type
        if creators is not None:
            out["creators"] = creators
        return out


def parse_creators(creators, item_type: str, schema: Schema | None) -> list[dict]:
    """Creators in Zotero's form from friendlier input: "Jane Smith" and "Smith, Jane" become
    first/last names with the type's primary role; dicts pass through (``{"name": ...}`` for an
    organisation), with ``creatorType`` defaulting to the primary role."""
    primary = schema.primary_creator(item_type) if schema else "author"
    out = []
    for c in creators or []:
        if isinstance(c, str):
            s = c.strip()
            if not s:
                continue
            if "," in s:
                last, first = (p.strip() for p in s.split(",", 1))
            else:
                parts = s.split()
                first, last = " ".join(parts[:-1]), parts[-1]
            out.append({"creatorType": primary, "firstName": first, "lastName": last} if first else
                       {"creatorType": primary, "name": last})
        elif isinstance(c, dict):
            d = {k: v for k, v in c.items() if k in ("creatorType", "firstName", "lastName", "name") and v not in (None, "")}
            if not ({"name"} & set(d) or {"lastName"} & set(d)):
                raise ValueError(f"a creator needs a name or a lastName: {c}")
            d.setdefault("creatorType", primary)
            out.append(d)
        else:
            raise ValueError(f"creators are names or objects, not {type(c).__name__}")
    if schema and schema.creators.get(item_type):
        bad = sorted({c["creatorType"] for c in out} - set(schema.creators[item_type]))
        if bad:
            raise ValueError(f"{item_type} has no creator type {', '.join(bad)} (it has {', '.join(schema.creators[item_type])})")
    return out


def _t(name: str, fields: str, creators: str) -> dict:
    fs = []
    for f in fields.split():
        field, _, base = f.partition("=")
        fs.append({"field": field, **({"baseField": base} if base else {})})
    cs = [{"creatorType": c, **({"primary": True} if i == 0 else {})} for i, c in enumerate(creators.split())]
    return {"itemType": name, "fields": fs, "creatorTypes": cs}


_COMMON = "shortTitle url accessDate language rights extra"
_LIB = "archive archiveLocation libraryCatalog callNumber"
MINI = {"version": 0, "itemTypes": [
    _t("journalArticle", f"title abstractNote publicationTitle volume issue pages date series seriesTitle seriesText "
       f"journalAbbreviation DOI ISSN {_LIB} {_COMMON}", "author contributor editor translator reviewedAuthor"),
    _t("book", f"title abstractNote series seriesNumber volume numberOfVolumes edition place publisher date numPages ISBN "
       f"{_LIB} {_COMMON}", "author contributor editor seriesEditor translator"),
    _t("bookSection", f"title abstractNote bookTitle=publicationTitle series seriesNumber volume numberOfVolumes edition place "
       f"publisher date pages ISBN {_LIB} {_COMMON}", "author contributor editor bookAuthor seriesEditor translator"),
    _t("report", f"title abstractNote reportNumber=number reportType=type seriesTitle place institution=publisher date pages "
       f"{_LIB} {_COMMON}", "author contributor seriesEditor translator"),
    _t("webpage", f"title abstractNote websiteTitle=publicationTitle websiteType=type date {_COMMON}", "author contributor translator"),
    _t("blogPost", f"title abstractNote blogTitle=publicationTitle websiteType=type date {_COMMON}", "author commenter contributor"),
    _t("newspaperArticle", f"title abstractNote publicationTitle place edition date section pages ISSN {_LIB} {_COMMON}",
       "author contributor reviewedAuthor translator"),
    _t("magazineArticle", f"title abstractNote publicationTitle volume issue date pages ISSN {_LIB} {_COMMON}",
       "author contributor reviewedAuthor translator"),
    _t("attachment", "title accessDate url", ""),
    _t("note", "", ""),
]}
