from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from sdgen.blueprint import Blueprint
from sdgen.brief import Brief, dump_brief
from sdgen.content import Content, dump_markdown, load_markdown, validate_content
from sdgen.llm import LLMClient, get_llm
from sdgen.manifest import Manifest

SKIP_KINDS = {"static", "divider"}
FENCE_RE = re.compile(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", re.DOTALL)

SYSTEM_PROMPT = """You are a senior SAP integration solution architect writing a solution-design document.
Write only from the brief. Never invent systems, interfaces, numbers, names or dates.
Where the brief lacks information, write [TBC] followed by a short question in brackets.
Formatting rules for every field: one paragraph per line; bullet lines start with "- " and are
indented two spaces per level; use **bold** sparingly; tables are pipe tables with exactly the
listed columns; stay within the character budget of each field.
Return only the content file in the required format, nothing else."""


class DraftResult(BaseModel):
    markdown: str
    content: Content
    warnings: list[str] = Field(default_factory=list)
    llm: str = ""


def draft_content(brief: Brief, blueprint: Blueprint, manifest: Manifest, llm: LLMClient | None = None) -> DraftResult:
    llm = llm or get_llm()
    system, user = build_prompt(brief, blueprint, manifest)
    reply = llm.complete(system, user)
    content = load_markdown(_unfence(reply), manifest)
    if brief.subject.strip() and not content.globals.get("subject"):
        content.globals["subject"] = brief.subject.strip()
    warnings = [w for w in validate_content(content, manifest) if "image" not in w]
    return DraftResult(markdown=dump_markdown(content, manifest), content=content, warnings=warnings, llm=llm.name)


def build_prompt(brief: Brief, blueprint: Blueprint, manifest: Manifest) -> tuple[str, str]:
    sections = writable_sections(blueprint, manifest)
    lines = ["# Task", "Write the content file for the document outlined below, using the brief.", "", "# Outline"]
    for section in sections:
        lines.append(f"## {section['title']} (section '{section['section']}', {section['kind']})")
        if section["ask"]:
            lines.append(f"What this section asks for: {section['ask']}")
        lines.append("Fields:")
        for field in section["fields"]:
            detail = field["kind"]
            if field["columns"]:
                detail += "; columns: " + ", ".join(field["columns"])
            if field["max_chars"]:
                detail += f"; about {field['max_chars']} characters"
            if field["token"]:
                detail += "; a few words only, it replaces a short placeholder"
            lines.append(f"- {field['key']} — {field['label']} ({detail})")
        if section["example"]:
            lines.append("Example from an earlier document (tone and depth only, do not reuse its facts):")
            lines.append('"""')
            lines.append(section["example"])
            lines.append('"""')
        lines.append("")
    lines += ["# Brief", dump_brief(brief), "# Output format"]
    lines.append("A Markdown content file: front-matter with `subject`, then one `## <field key>` heading per field listed above, in order.")
    lines.append("Text fields: paragraphs and bullet lines. Table fields: a pipe table with exactly the listed columns, one row per entry.")
    lines.append("")
    context = {
        "brief": {"subject": brief.subject, **brief.texts()},
        "sections": [{k: v for k, v in s.items() if k != "example"} for s in sections],
    }
    lines.append("```json")
    lines.append(json.dumps(context, ensure_ascii=False))
    lines.append("```")
    return SYSTEM_PROMPT, "\n".join(lines)


def writable_sections(blueprint: Blueprint, manifest: Manifest) -> list[dict]:
    result = []
    for section in blueprint.sections:
        if section.kind in SKIP_KINDS:
            continue
        fields = [manifest.field(k) for k in section.fields]
        fields = [f for f in fields if f is not None and f.kind != "image"]
        if not fields:
            continue
        result.append(
            {
                "section": section.key,
                "title": section.title,
                "kind": section.kind,
                "ask": section.ask,
                "example": section.example,
                "fields": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "kind": f.kind,
                        "columns": list(f.columns),
                        "max_chars": max((b.max_chars or 0) for b in f.bindings) or None,
                        "token": any(b.mode == "token" for b in f.bindings),
                        "guidance": f.guidance,
                    }
                    for f in fields
                ],
            }
        )
    return result


def _unfence(reply: str) -> str:
    match = FENCE_RE.match(reply.strip())
    return match.group(1) if match else reply
