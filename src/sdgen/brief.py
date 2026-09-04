from __future__ import annotations

import json

from pydantic import BaseModel, Field

from sdgen.analyze import slugify
from sdgen.content import parse_markdown_sections, strip_comments

BRIEF_FIELDS: list[tuple[str, str, str]] = [
    ("about", "What the integration is about", "Two or three sentences: which business process, which systems, why now."),
    ("problem_outcome", "Problem and expected outcome", "What hurts today and what must be true once this is live."),
    ("approach", "How we plan to solve it", "Source, middleware, target; protocols, frequency, volumes, error handling, security."),
    ("apis_references", "APIs and references", "One per line: title | URL | note."),
    ("investigation_notes", "Investigation details", "Findings, constraints, assumptions, open questions."),
    ("mapping_summary", "Mappings", "Filled from the mapping workbook; add remarks if needed."),
]


class DiagramInput(BaseModel):
    section: str
    path: str
    caption: str = ""


class Brief(BaseModel):
    subject: str = ""
    about: str = ""
    problem_outcome: str = ""
    approach: str = ""
    apis_references: str = ""
    investigation_notes: str = ""
    mapping_summary: str = ""
    diagrams: list[DiagramInput] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(getattr(self, key).strip() for key, _, _ in BRIEF_FIELDS) and not self.subject.strip()

    def texts(self) -> dict[str, str]:
        return {key: getattr(self, key) for key, _, _ in BRIEF_FIELDS}


def load_brief(text: str) -> Brief:
    front, sections = parse_markdown_sections(text)
    data: dict[str, str] = {"subject": str(front.get("subject", "") or "")}
    by_label = {slugify(label): key for key, label, _ in BRIEF_FIELDS}
    for heading, body in sections:
        key = heading.strip()
        if key not in Brief.model_fields:
            key = by_label.get(slugify(heading), slugify(heading))
        if key in Brief.model_fields and key != "diagrams":
            data[key] = strip_comments(body).strip()
    return Brief(**data)


def dump_brief(brief: Brief) -> str:
    lines = ["---", f"subject: {json.dumps(brief.subject)}", "---", ""]
    for key, _, _ in BRIEF_FIELDS:
        lines.append(f"## {key}")
        lines.append(getattr(brief, key).strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def brief_skeleton() -> str:
    lines = ["---", 'subject: ""  # Integration name used in slide titles', "---", ""]
    for key, label, guidance in BRIEF_FIELDS:
        lines.append(f"## {key}")
        lines.append(f"<!-- {label}. {guidance} -->")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
