from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from sdgen.analyze import slugify
from sdgen.blueprint import Blueprint
from sdgen.content import parse_markdown_sections, strip_comments
from sdgen.manifest import Manifest
from sdgen.material import Material

BRIEF_FIELDS: list[tuple[str, str, str]] = [
    ("about", "What the integration is about", "Two or three sentences: which business process, which systems, why now."),
    ("problem_outcome", "Problem and expected outcome", "What hurts today and what must be true once this is live."),
    ("approach", "How we plan to solve it", "Source, middleware, target; protocols, frequency, volumes, error handling, security."),
    ("apis_references", "APIs and references", "One per line: title | URL | note."),
    ("investigation_notes", "Investigation details", "Findings, constraints, assumptions."),
    ("acceptance_criteria", "Acceptance criteria and test scenarios", "Numbered, testable criteria per flow: happy path, duplicate file, unmatched item, failure and retry."),
    ("operations", "Error handling, monitoring and operations", "What happens on each failure, alerts, reprocessing, who is notified and who acts."),
    ("non_functional", "Non-functional facts", "Volumes, frequency, file sizes, security, retention, environments, availability."),
    ("decisions_log", "Decisions", "One per line: decision | owner | status."),
    ("open_questions", "Open questions", "One per line: question | who to ask. They get their own slide in the draft, so reviewers can answer them."),
    ("mapping_summary", "Mappings", "Filled from the mapping workbook; add remarks if needed."),
]
DEVELOPER_FIELDS = {"acceptance_criteria", "operations", "non_functional", "decisions_log", "open_questions"}
FACT_PREFIX = "fact:"
# Cells per entry of the "one per line" fields and facts, to spot several entries pasted on one line.
SEPARATED_FIELDS: dict[str, int] = {"apis_references": 3, "decisions_log": 3, "open_questions": 2}
FACT_CELLS: dict[str, int] = {"systems": 3, "parties": 2, "targets": 2, "effort": 5, "investment": 3, "sap_objects": 3, "interfaces": 7, "endpoints": 7, "naming": 4}


class FactSpec(BaseModel):
    key: str
    label: str
    guidance: str = ""
    multiline: bool = False


FACT_CATALOGUE: list[FactSpec] = [
    FactSpec(key="design_start", label="Design start date", guidance="For example 12 February 2026."),
    FactSpec(key="design_end", label="Design end date", guidance="Planned end of the design phase."),
    FactSpec(key="version", label="Document version", guidance="For example 0.1 or 1.0."),
    FactSpec(key="author", label="Author", guidance="Name of the document author."),
    FactSpec(key="contributors", label="Contributors", guidance="Names, separated by semicolons."),
    FactSpec(key="design_doc_url", label="Link to the detailed design", guidance="URL of the detailed design document or repository."),
    FactSpec(key="systems", label="Systems in the flow", guidance="One per line: system | source, middleware or target | keep, change or new. These systems become the lanes of every diagram.", multiline=True),
    FactSpec(key="parties", label="Counterparts", guidance="One per line: name | note. For example the banks or carriers in scope.", multiline=True),
    FactSpec(key="countries", label="Operating countries", guidance="Comma separated."),
    FactSpec(key="company_codes", label="Company codes", guidance="Comma separated SAP company codes."),
    FactSpec(key="currencies", label="Currencies", guidance="Comma separated."),
    FactSpec(key="volumes", label="Volumes", guidance="For example 300 files a month, 12,000 items a day."),
    FactSpec(key="frequency", label="Frequency", guidance="For example end of day, hourly, event driven."),
    FactSpec(key="targets", label="Measurable targets", guidance="One per line: measure | target. For example automatic clearing rate | 95 percent.", multiline=True),
    FactSpec(key="effort", label="Effort by role", guidance="One per line: role | deliverable | month-1 hours | month-2 hours | month-n hours.", multiline=True),
    FactSpec(key="investment", label="Investment", guidance="One per line: type | internal | external.", multiline=True),
    FactSpec(key="sap_objects", label="SAP objects", guidance="One per line: name | type | purpose. Fiori apps, reports, programs, configuration objects.", multiline=True),
    FactSpec(key="interfaces", label="Interfaces", guidance="One per line: party | flow | direction | source | target | encryption | cut-off. Fills the interface inventory slide without the model.", multiline=True),
    FactSpec(key="endpoints", label="Endpoints per environment", guidance="One per line: environment | endpoint | host | port | account | key or cert | network path. Fills the connectivity slide without the model.", multiline=True),
    FactSpec(key="naming", label="File naming", guidance="One per line: flow | pattern | temp name | example. Fills the file naming slide without the model.", multiline=True),
    FactSpec(key="environments", label="Environments", guidance="For example DEV, QAS, PRD and the tenants involved."),
    FactSpec(key="security", label="Security", guidance="Authentication, encryption, certificates, data classification."),
    FactSpec(key="retention", label="Retention", guidance="How long files and logs are kept and where."),
]
FACTS_BY_KEY = {spec.key: spec for spec in FACT_CATALOGUE}
ALWAYS_ASKED = ("systems", "volumes", "frequency", "environments")

_VERSION_RE = re.compile(r"version|author|contributor", re.IGNORECASE)
_EFFORT_RE = re.compile(r"month-|effort|\brole\b|deliverable", re.IGNORECASE)
_INVESTMENT_RE = re.compile(r"\binternal\b|\bexternal\b|investment", re.IGNORECASE)
_PARTIES_RE = re.compile(r"\b(carriers?|banks?|suppliers?|customers?|partners?|vendors?)\b", re.IGNORECASE)
_COUNTRY_RE = re.compile(r"countr", re.IGNORECASE)
_COMPANY_RE = re.compile(r"company", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"currenc", re.IGNORECASE)
_TARGET_RE = re.compile(r"success|measure|criteria|kpi", re.IGNORECASE)
_SAP_RE = re.compile(r"report|analytic|fiori|configuration|requirement", re.IGNORECASE)
_LINK_RE = re.compile(r"link|url", re.IGNORECASE)
_SECURITY_RE = re.compile(r"security|authentication|certificate", re.IGNORECASE)
_RETENTION_RE = re.compile(r"retention|archiv", re.IGNORECASE)


class FactQuestion(BaseModel):
    spec: FactSpec
    used_by: list[str] = Field(default_factory=list)


class Brief(BaseModel):
    subject: str = ""
    about: str = ""
    problem_outcome: str = ""
    approach: str = ""
    apis_references: str = ""
    investigation_notes: str = ""
    acceptance_criteria: str = ""
    operations: str = ""
    non_functional: str = ""
    decisions_log: str = ""
    open_questions: str = ""
    mapping_summary: str = ""
    facts: dict[str, str] = Field(default_factory=dict)
    material: list[Material] = Field(default_factory=list)  # pictures, notes and links the model reads next to the brief

    @property
    def is_empty(self) -> bool:
        return not any(getattr(self, key).strip() for key, _, _ in BRIEF_FIELDS) and not self.subject.strip()

    def texts(self) -> dict[str, str]:
        return {key: getattr(self, key) for key, _, _ in BRIEF_FIELDS}

    def facts_text(self) -> str:
        lines = []
        for spec in FACT_CATALOGUE:
            value = self.facts.get(spec.key, "").strip()
            if value:
                lines.append(f"{spec.label}: {value}" if not spec.multiline else f"{spec.label}:\n{value}")
        return "\n".join(lines)


def fact_questions(blueprint: Blueprint | None, manifest: Manifest) -> list[FactQuestion]:
    """Facts the template needs, derived from its fields; independent of any design."""
    used: dict[str, list[str]] = {}
    labels: dict[str, str] = {}

    def need(key: str, title: str, label: str | None = None) -> None:
        titles = used.setdefault(key, [])
        if title not in titles:
            titles.append(title)
        if label:
            labels[key] = label

    for section in blueprint.sections if blueprint else []:
        title = section.title
        if section.kind == "cover":
            for key in ("design_start", "design_end", "version"):
                need(key, title)
        if section.kind == "diagram":
            for key in ("systems", "interfaces", "endpoints", "naming"):
                need(key, title)
        for field_key in section.fields:
            spec = manifest.field(field_key)
            if spec is None:
                continue
            columns = " ".join(spec.columns)
            texts = f"{spec.label} {columns}"
            if spec.kind == "table":
                if _VERSION_RE.search(columns):
                    for key in ("version", "author", "contributors"):
                        need(key, title)
                if _EFFORT_RE.search(columns):
                    need("effort", title)
                if _INVESTMENT_RE.search(columns):
                    need("investment", title)
                match = _PARTIES_RE.search(columns)
                if match:
                    need("parties", title, match.group(1).capitalize())
            if _COUNTRY_RE.search(texts):
                need("countries", title)
            if _COMPANY_RE.search(texts):
                need("company_codes", title)
            if _CURRENCY_RE.search(texts):
                need("currencies", title)
            if _TARGET_RE.search(spec.label):
                need("targets", title)
            if _SAP_RE.search(spec.label):
                need("sap_objects", title)
            if _SECURITY_RE.search(spec.label):
                need("security", title)
            if _RETENTION_RE.search(spec.label):
                need("retention", title)
            if any(b.mode == "token" for b in spec.bindings):
                if _EFFORT_RE.search(spec.label):
                    need("effort", title)
                elif _LINK_RE.search(spec.label):
                    need("design_doc_url", title)
    for key in ALWAYS_ASKED:
        used.setdefault(key, [])
    questions = []
    for spec in FACT_CATALOGUE:
        if spec.key not in used:
            continue
        shown = spec.model_copy(update={"label": labels[spec.key]}) if spec.key in labels else spec
        questions.append(FactQuestion(spec=shown, used_by=used[spec.key]))
    return questions


def load_brief(text: str) -> Brief:
    front, sections = parse_markdown_sections(text)
    data: dict = {"subject": str(front.get("subject", "") or ""), "facts": {}}
    by_label = {slugify(label): key for key, label, _ in BRIEF_FIELDS}
    for heading, body in sections:
        key = heading.strip()
        if key.startswith(FACT_PREFIX):
            value = strip_comments(body).strip()
            if value:
                data["facts"][key[len(FACT_PREFIX):].strip()] = value
            continue
        if key not in Brief.model_fields:
            key = by_label.get(slugify(heading), slugify(heading))
        if key in Brief.model_fields and key not in ("material", "facts"):
            data[key] = strip_comments(body).strip()
    return Brief(**data)


def dump_brief(brief: Brief) -> str:
    lines = ["---", f"subject: {json.dumps(brief.subject)}", "---", ""]
    for key, _, _ in BRIEF_FIELDS:
        lines.append(f"## {key}")
        lines.append(getattr(brief, key).strip())
        lines.append("")
    for key, value in brief.facts.items():
        if value.strip():
            lines.append(f"## {FACT_PREFIX}{key}")
            lines.append(value.strip())
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def brief_skeleton() -> str:
    lines = ["---", 'subject: ""  # Integration name used in slide titles', "---", ""]
    for key, label, guidance in BRIEF_FIELDS:
        lines.append(f"## {key}")
        lines.append(f"<!-- {label}. {guidance} -->")
        lines.append("")
    lines.append("<!-- Facts: add '## fact:<key>' sections as needed, for example fact:version, fact:author, fact:effort. -->")
    for spec in FACT_CATALOGUE:
        lines.append(f"## {FACT_PREFIX}{spec.key}")
        lines.append(f"<!-- {spec.label}. {spec.guidance} -->")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def looks_joined(text: str, cells: int) -> bool:
    """Several entries pasted on one line, where the field expects one entry per line."""
    lines = [line for line in text.splitlines() if line.strip()]
    return len(lines) == 1 and cells > 1 and lines[0].count("|") >= 2 * (cells - 1)


def split_joined(text: str, cells: int) -> str:
    """Regroups the cells of one pasted line into lines of `cells` cells each."""
    parts = [part.strip() for part in text.split("|")]
    rows = [parts[i : i + cells] for i in range(0, len(parts), cells)]
    return "\n".join(" | ".join(row) for row in rows if any(row))


def table_cut_short(text: str) -> bool:
    """A pasted table (tab or pipe separated rows) whose last row has fewer cells than its header."""
    rows = [_cells(line) for line in text.splitlines() if line.strip()]
    tabular = [row for row in rows if len(row) > 1]
    if len(tabular) < 2 or len(rows[-1]) < 2:
        return False
    return len(tabular[-1]) < len(tabular[0])


def brief_lint(brief: Brief) -> list[tuple[str, str]]:
    """(key, message) for fields whose pasted text lost its line breaks or ends in a cut table row."""
    found: list[tuple[str, str]] = []
    for key, label, _ in BRIEF_FIELDS:
        value = getattr(brief, key)
        cells = SEPARATED_FIELDS.get(key)
        if cells and looks_joined(value, cells):
            found.append((key, f"{label}: expects one entry per line but holds one line with {value.count('|')} separators."))
        elif table_cut_short(value):
            found.append((key, f"{label}: looks like a pasted table whose last row is incomplete."))
    for key, cells in FACT_CELLS.items():
        value = brief.facts.get(key, "")
        if looks_joined(value, cells):
            found.append((f"{FACT_PREFIX}{key}", f"{FACTS_BY_KEY[key].label}: expects one entry per line but holds one line with {value.count('|')} separators."))
    return found


def table_rows(text: str) -> tuple[list[str], list[list[str]]] | None:
    """A pasted table with a header line: tab-separated as copied from Excel or a slide, or a Markdown pipe table."""
    lines = [line for line in text.splitlines() if line.strip()]
    if any("\t" in line for line in lines):
        rows = [[cell.strip() for cell in line.split("\t")] for line in lines if "\t" in line]
    elif lines and all(line.strip().startswith("|") for line in lines):
        rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines if not re.fullmatch(r"[|\s:\-]+", line)]
    else:
        return None
    if len(rows) < 2 or len(rows[0]) < 2:
        return None
    width = len(rows[0])
    return rows[0], [(row + [""] * width)[:width] for row in rows[1:]]


def _cells(line: str) -> list[str]:
    separator = "\t" if "\t" in line else "|"
    return [cell.strip() for cell in line.split(separator)]
