"""Read a *structure* definition — the schema half of the two inputs.

Sample data says how a portfolio behaves; a structure says which fields must
exist and what shape they take. Regulatory templates are the usual source, and
they carry real type information worth using.

Three formats are understood:

**ESMA/ECB taxonomy JSON**
    As produced by the deeploans ETL pipelines: a list of fields with a code, a
    name, a description, and a ``format_hint`` such as ``{MONETARY}`` or
    ``{ALPHANUM-28}``. The hints are a closed vocabulary and map cleanly onto
    types — that is what makes this the richest input.

**A bare CSV header**
    Just column names. Enough to fix the output schema; types come from the
    sample.

**A data dictionary CSV**
    A table with name/type/description columns, in whatever casing.

The result is a list of :class:`FieldSpec` — a declared schema with no
distributions attached. :mod:`sdd.profile.build` merges it with a profile to
produce a runnable spec.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from sdd.spec.schema import DType

# The ESMA format-hint vocabulary, mapped to a type and a sensible default
# domain. Taken from the ESMA disclosure technical standards; the same hints
# appear across Annexes 2 to 5, so one table covers every asset class.
FORMAT_HINTS: dict[str, dict[str, Any]] = {
    "MONETARY": {"dtype": "float", "note": "monetary amount"},
    "PERCENTAGE": {"dtype": "float", "min": 0.0, "max": 100.0, "note": "percentage"},
    "DATEFORMAT": {"dtype": "date", "note": "ISO 8601 date"},
    "YEAR": {"dtype": "int", "note": "four-digit year"},
    "INTEGER": {"dtype": "int"},
    "ALPHANUM": {"dtype": "str"},
    "LEI": {"dtype": "str", "note": "20-character Legal Entity Identifier"},
    "NUTS": {"dtype": "category", "note": "NUTS statistical region code"},
    "COUNTRYCODE": {"dtype": "category", "note": "ISO 3166 country code"},
    "CURRENCYCODE": {"dtype": "category", "note": "ISO 4217 currency code"},
    "Y/N": {"dtype": "category", "domain": ["Y", "N"]},
    "LIST": {"dtype": "category", "note": "closed list; values come from the sample"},
    "WATCHLIST": {"dtype": "category", "note": "closed list; values come from the sample"},
}

_HINT_RE = re.compile(r"\{([A-Z/]+)(?:[-_](\d+))?\}")


@dataclass
class FieldSpec:
    """One declared field, before any distribution is attached."""

    name: str
    dtype: DType | None = None
    code: str | None = None
    label: str | None = None
    description: str | None = None
    section: str | None = None
    domain: list[Any] | None = None
    minimum: float | None = None
    maximum: float | None = None
    max_length: int | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class Template:
    name: str
    fields: list[FieldSpec] = field(default_factory=list)
    asset_class: str | None = None
    source: str | None = None

    @property
    def column_names(self) -> list[str]:
        return [f.name for f in self.fields]

    def field(self, name: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "asset_class": self.asset_class,
            "source": self.source,
            "field_count": len(self.fields),
            "fields": [f.to_dict() for f in self.fields],
        }

    def summary(self) -> str:
        typed = sum(1 for f in self.fields if f.dtype)
        lines = [f"{self.name}: {len(self.fields)} field(s), {typed} with a declared type"]
        if self.asset_class:
            lines.append(f"  asset class  {self.asset_class}")
        by_type: dict[str, int] = {}
        for f in self.fields:
            by_type[f.dtype or "untyped"] = by_type.get(f.dtype or "untyped", 0) + 1
        lines.append("  types        " + ", ".join(f"{v} {k}" for k, v in sorted(by_type.items())))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# format hints
# ---------------------------------------------------------------------------


def parse_format_hint(hint: str | None) -> dict[str, Any]:
    """Turn an ESMA format hint into type information.

    ``{ALPHANUM-28}`` -> a string of at most 28 characters.
    ``{PERCENTAGE}``  -> a float bounded to 0-100.
    ``{Y/N}``         -> a category with exactly two allowed values.
    """
    if not hint:
        return {}
    match = _HINT_RE.search(hint.strip())
    if not match:
        return {}
    key, size = match.group(1), match.group(2)
    spec = dict(FORMAT_HINTS.get(key, {}))
    if size and key == "ALPHANUM":
        spec["max_length"] = int(size)
    elif size and key == "INTEGER":
        spec["maximum"] = float(size)
    return spec


# ---------------------------------------------------------------------------
# loaders
# ---------------------------------------------------------------------------


def load_template(path: str | Path, *, name_field: str = "field_name") -> Template:
    """Load a structure definition, detecting the format from the file."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".json":
        return load_taxonomy_json(path, name_field=name_field)
    if suffix in (".csv", ".tsv", ".txt"):
        return load_csv_template(path)
    raise ValueError(
        f"cannot read a structure definition from {path.suffix!r}; "
        "expected .json (taxonomy), or .csv (header or data dictionary)"
    )


def load_taxonomy_json(path: str | Path, *, name_field: str = "field_name") -> Template:
    """Read an ESMA/ECB taxonomy JSON.

    ``name_field`` picks which attribute becomes the column name. The deeploans
    taxonomies carry both a code (``CREL1``) and a human name (``Unique
    Identifier``); sample tapes are usually keyed by code, so that is offered as
    an alternative rather than assumed.
    """
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("fields", raw) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError(f"{path} does not contain a list of fields")

    fields: list[FieldSpec] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get(name_field) or entry.get("field_code") or entry.get("name")
        if not name:
            continue
        hint = parse_format_hint(entry.get("format_hint"))
        fields.append(
            FieldSpec(
                name=str(name),
                dtype=hint.get("dtype"),
                code=entry.get("field_code"),
                label=entry.get("field_name"),
                description=_shorten(entry.get("content_to_report") or entry.get("description")),
                section=entry.get("section"),
                domain=hint.get("domain"),
                minimum=hint.get("min"),
                maximum=hint.get("max") or hint.get("maximum"),
                max_length=hint.get("max_length"),
                note=hint.get("note"),
            )
        )

    return Template(
        name=str(raw.get("template", path.stem)) if isinstance(raw, dict) else path.stem,
        fields=fields,
        asset_class=str(raw.get("asset_class")) if isinstance(raw, dict) else None,
        source=str(path),
    )


def load_csv_template(path: str | Path) -> Template:
    """Read either a bare header row or a data dictionary."""
    path = Path(path)
    head = pd.read_csv(path, nrows=50)
    lowered = {str(c).strip().lower(): c for c in head.columns}

    name_key = next(
        (
            lowered[k]
            for k in ("name", "field", "field_name", "column", "column_name")
            if k in lowered
        ),
        None,
    )
    type_key = next(
        (lowered[k] for k in ("type", "dtype", "data_type", "format") if k in lowered), None
    )

    # A data dictionary has a name column *and* something describing each field;
    # a bare header just has the columns themselves.
    if name_key and (type_key or any(k in lowered for k in ("description", "content_to_report"))):
        desc_key = next(
            (lowered[k] for k in ("description", "content_to_report", "comment") if k in lowered),
            None,
        )
        full = pd.read_csv(path)
        fields = [
            FieldSpec(
                name=str(row[name_key]).strip(),
                dtype=_normalise_dtype(str(row[type_key]))
                if type_key and pd.notna(row.get(type_key))
                else None,
                description=_shorten(str(row[desc_key]))
                if desc_key and pd.notna(row.get(desc_key))
                else None,
            )
            for _, row in full.iterrows()
            if pd.notna(row.get(name_key))
        ]
        return Template(name=path.stem, fields=fields, source=str(path))

    return Template(
        name=path.stem,
        fields=[FieldSpec(name=str(c).strip()) for c in head.columns],
        source=str(path),
    )


def template_from_columns(names: list[str], *, name: str = "columns") -> Template:
    """Build a template from a list of column names."""
    return Template(name=name, fields=[FieldSpec(name=str(n)) for n in names])


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_DTYPE_WORDS: dict[str, DType] = {
    "int": "int",
    "integer": "int",
    "bigint": "int",
    "long": "int",
    "float": "float",
    "double": "float",
    "decimal": "float",
    "numeric": "float",
    "number": "float",
    "money": "float",
    "monetary": "float",
    "percentage": "float",
    "percent": "float",
    "str": "str",
    "string": "str",
    "text": "str",
    "varchar": "str",
    "char": "str",
    "bool": "bool",
    "boolean": "bool",
    "date": "date",
    "datetime": "date",
    "timestamp": "date",
    "category": "category",
    "categorical": "category",
    "enum": "category",
    "list": "category",
}


def _normalise_dtype(value: str) -> DType | None:
    cleaned = value.strip().lower()
    if cleaned in _DTYPE_WORDS:
        return _DTYPE_WORDS[cleaned]
    hint = parse_format_hint(value)
    if hint.get("dtype"):
        return hint["dtype"]
    for word, dtype in _DTYPE_WORDS.items():
        if word in cleaned:
            return dtype
    return None


def _shorten(text: str | None, limit: int = 200) -> str | None:
    """Regulatory field descriptions run to paragraphs; keep the first sentence."""
    if not text:
        return None
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(". ", 1)[0]
    return (cut if len(cut) > 40 else flat[:limit]).rstrip(" .") + "…"
