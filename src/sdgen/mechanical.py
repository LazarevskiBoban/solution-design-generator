from __future__ import annotations

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from sdgen.blueprint import Blueprint
from sdgen.brief import Brief, looks_joined, split_joined, table_rows
from sdgen.content import Content
from sdgen.manifest import FieldSpec, Manifest

VERSION_RE = re.compile(r"version|author|contributor", re.IGNORECASE)
EFFORT_RE = re.compile(r"month-|effort", re.IGNORECASE)
INVESTMENT_RE = re.compile(r"\binternal\b|\bexternal\b", re.IGNORECASE)
LINK_RE = re.compile(r"link|url", re.IGNORECASE)
INSIGHT_RE = re.compile(r"insight|summary|headline", re.IGNORECASE)
NUMBER_RE = re.compile(r"\d[\d,.]*")
TBC = "[TBC]"
PASTED_TABLE_FIELDS = ("operations", "acceptance_criteria")  # brief fields that may hold a pasted table with a header
FACT_TABLES = {"extra_interface_inventory": "interfaces", "extra_connectivity": "endpoints", "extra_file_naming": "naming"}


class MechanicalResult(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


def mechanical_fills(brief: Brief, blueprint: Blueprint, manifest: Manifest, original: Content | None = None) -> MechanicalResult:
    """Fields that follow from the brief's facts or the template itself, without the model."""
    result = MechanicalResult()
    for section in blueprint.sections:
        for key in section.fields:
            spec = manifest.field(key)
            if spec is None or spec.kind == "image":
                continue
            value, note = _fill(spec, section.kind, brief, original)
            if value not in (None, "", []):
                result.fields[key] = value
                result.notes.append(f"{spec.label}: {note}")
    return result


URL_COLUMN_RE = re.compile(r"\b(url|link|hyperlink)\b", re.IGNORECASE)
SOURCE_COLUMN_RE = re.compile(r"\bsource\b", re.IGNORECASE)
CONTENT_COLUMN_RE = re.compile(r"content|description|purpose|reference", re.IGNORECASE)


def is_reference_columns(columns: list[str]) -> bool:
    """A references table names a URL column, or a Source column beside a content or purpose column; an interface's Source system is neither."""
    if any(URL_COLUMN_RE.search(c) for c in columns):
        return True
    return any(SOURCE_COLUMN_RE.search(c) for c in columns) and any(CONTENT_COLUMN_RE.search(c) for c in columns)


def reference_rows(text: str, columns: list[str]) -> list[dict[str, str]]:
    if looks_joined(text, len(columns)):
        text = split_joined(text, len(columns))
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if any(parts):
            rows.append({column: (parts[i] if i < len(parts) else "") for i, column in enumerate(columns)})
    return rows


def _fill(spec: FieldSpec, kind: str, brief: Brief, original: Content | None) -> tuple[Any, str]:
    facts = brief.facts
    if kind == "static" and original is not None:
        value = original.fields.get(spec.key)
        if value not in (None, "", []):
            return value, "kept from the template"
    if spec.kind == "table":
        columns = " ".join(spec.columns)
        pasted = _pasted_rows(spec.columns, brief)
        if pasted is not None:
            return pasted, "from the table in the brief"
        fact_key = FACT_TABLES.get(spec.key, "")
        if fact_key and facts.get(fact_key, "").strip():
            return _rows_from_fact(facts[fact_key], spec.columns), "from facts"
        if is_reference_columns(spec.columns) and brief.apis_references.strip():
            return reference_rows(brief.apis_references, spec.columns), "from APIs and references"
        if VERSION_RE.search(columns):
            return [_version_row(spec.columns, facts)], "from facts"
        if EFFORT_RE.search(columns) and facts.get("effort", "").strip():
            return _effort_rows(spec.columns, facts["effort"]), "from facts"
        if INVESTMENT_RE.search(columns) and facts.get("investment", "").strip():
            return _investment_rows(spec.columns, facts["investment"]), "from facts"
        return None, ""
    if any(b.mode == "token" for b in spec.bindings):
        if LINK_RE.search(spec.label) and facts.get("design_doc_url", "").strip():
            return facts["design_doc_url"].strip(), "from facts"
        if INSIGHT_RE.search(spec.label) and brief.about.strip():
            return _first_sentence(brief.about, 120), "from the brief"
        return None, ""
    if kind == "cover" and brief.subject.strip():
        return _cover_lines(brief), "from the subject and facts"
    return None, ""


def _pasted_rows(columns: list[str], brief: Brief) -> list[dict[str, str]] | None:
    """Rows of a table pasted into the brief whose header matches the field's columns."""
    wanted = [_norm(c) for c in columns]
    for field_key in PASTED_TABLE_FIELDS:
        parsed = table_rows(getattr(brief, field_key, ""))
        if parsed is not None and [_norm(c) for c in parsed[0]] == wanted:
            return [dict(zip(columns, row)) for row in parsed[1]]
    return None


def _rows_from_fact(text: str, columns: list[str]) -> list[dict[str, str]]:
    """One row per fact line, cells by position; a '#' column counts the rows."""
    rows = []
    positional = [c for c in columns if c.strip() != "#"]
    for index, line in enumerate(_lines(text), 1):
        parts = [p.strip() for p in line.lstrip("-*• ").split("|")]
        row = {c: (parts[i] if i < len(parts) else "") for i, c in enumerate(positional)}
        for column in columns:
            if column.strip() == "#":
                row[column] = str(index)
        rows.append({c: row.get(c, "") for c in columns})
    return rows


def _norm(text: str) -> str:
    return " ".join(str(text).split()).lower()


def _version_row(columns: list[str], facts: dict[str, str]) -> dict[str, str]:
    row = {}
    for column in columns:
        name = column.lower()
        if "version" in name:
            row[column] = facts.get("version", "").strip() or "0.1"
        elif "date" in name:
            row[column] = date.today().strftime("%d %b %Y")
        elif "author" in name:
            row[column] = facts.get("author", "").strip() or TBC
        elif "contributor" in name:
            row[column] = facts.get("contributors", "").strip()
        elif "change" in name or "description" in name:
            row[column] = "Initial draft"
        else:
            row[column] = ""
    return row


def _effort_rows(columns: list[str], text: str) -> list[dict[str, str]]:
    rows = []
    for index, line in enumerate(_lines(text), 1):
        parts = [p.strip() for p in line.split("|")]
        role = parts[0] if parts else ""
        deliverable = parts[1] if len(parts) > 1 else ""
        months = parts[2:]
        numbers = [_number(m) for m in months]
        row = {}
        for column in columns:
            name = column.lower()
            if "reference" in name:
                row[column] = str(index)
            elif "role" in name:
                row[column] = role
            elif "deliverable" in name:
                row[column] = deliverable
            elif "total" in name:
                row[column] = _format(sum(n for n in numbers if n is not None)) if any(n is not None for n in numbers) else TBC
            elif name.startswith("month"):
                position = _month_position(name)
                row[column] = months[position] if position is not None and position < len(months) else (months[-1] if name.endswith("n") and months else "")
            else:
                row[column] = ""
        rows.append(row)
    return rows


def _investment_rows(columns: list[str], text: str) -> list[dict[str, str]]:
    rows = []
    for line in _lines(text):
        parts = [p.strip() for p in line.split("|")]
        row = {}
        for column in columns:
            name = column.lower()
            if "internal" in name:
                row[column] = parts[1] if len(parts) > 1 else ""
            elif "external" in name:
                row[column] = parts[2] if len(parts) > 2 else ""
            else:
                row[column] = parts[0] if parts else ""
        rows.append(row)
    return rows


def _cover_lines(brief: Brief) -> str:
    facts = brief.facts
    lines = [brief.subject.strip() or TBC]
    start, end = facts.get("design_start", "").strip(), facts.get("design_end", "").strip()
    if start or end:
        lines.append(f"Date: {start or TBC} – {end or TBC}")
    if facts.get("version", "").strip():
        lines.append(f"Version: {facts['version'].strip()}")
    return "\n".join(lines)


def _month_position(name: str) -> int | None:
    match = re.search(r"month[- ]?(\d+)", name)
    return int(match.group(1)) - 1 if match else None


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _number(text: str) -> float | None:
    match = NUMBER_RE.search(text.replace(",", ""))
    try:
        return float(match.group(0)) if match else None
    except ValueError:
        return None


def _format(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def _first_sentence(text: str, limit: int) -> str:
    sentence = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))[0]
    return sentence if len(sentence) <= limit else sentence[:limit].rsplit(" ", 1)[0] + "…"
