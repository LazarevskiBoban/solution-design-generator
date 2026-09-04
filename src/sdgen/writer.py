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
EXAMPLE_CHARS = 700
FENCE_RE = re.compile(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", re.DOTALL)

SYSTEM_PROMPT = """You are a senior SAP integration solution architect writing a solution-design document.
Write only from the brief. Never invent systems, interfaces, numbers, names or dates.
Where the brief lacks information, write [TBC] followed by a short question in brackets.
You receive an answer skeleton. Return it filled in: keep every "## " heading exactly as written
and in the same order, replace each <!-- hint --> with the content, and add nothing else: no other
headings, no commentary, no code fence.
Under a heading: one paragraph per line; bullet lines start with "- " and are indented two spaces
per level; use **bold** sparingly; do not repeat the field label at the start of the content.
A table keeps its header row and gets one row per entry, at least one row, with exactly the
listed columns. Stay within the character budget of each field."""


class DraftResult(BaseModel):
    markdown: str
    content: Content
    warnings: list[str] = Field(default_factory=list)
    llm: str = ""


def draft_content(brief: Brief, blueprint: Blueprint, manifest: Manifest, llm: LLMClient | None = None) -> DraftResult:
    llm = llm or get_llm()
    system, user = build_prompt(brief, blueprint, manifest, include_context=bool(getattr(llm, "wants_context", False)))
    reply = llm.complete(system, user)
    content = load_markdown(_unfence(reply), manifest)
    strip_label_prefixes(content, manifest)
    if brief.subject.strip() and not content.globals.get("subject"):
        content.globals["subject"] = brief.subject.strip()
    warnings = [w for w in validate_content(content, manifest) if "image" not in w]
    return DraftResult(markdown=dump_markdown(content, manifest), content=content, warnings=warnings, llm=llm.name)


def build_prompt(brief: Brief, blueprint: Blueprint, manifest: Manifest, include_context: bool = False) -> tuple[str, str]:
    sections = writable_sections(blueprint, manifest)
    lines = ["# Task", "Fill in the answer skeleton at the end of this message, using the outline and the brief.", "", "# Outline"]
    for section in sections:
        lines.append(f"## {section['title']} ({section['kind']})")
        if section["ask"]:
            lines.append(f"What this section asks for: {section['ask']}")
        lines.append("Fields: " + ", ".join(f["key"] for f in section["fields"]))
        if section["example"]:
            lines.append("Example from an earlier document (tone and depth only, do not reuse its facts):")
            lines.append('"""')
            lines.append(_clip(section["example"], EXAMPLE_CHARS))
            lines.append('"""')
        lines.append("")
    lines += ["# Brief", dump_brief(brief), "# Answer skeleton", answer_skeleton(brief, sections)]
    if include_context:
        context = {
            "brief": {"subject": brief.subject, **brief.texts()},
            "sections": [{k: v for k, v in s.items() if k != "example"} for s in sections],
        }
        lines += ["", "Machine-readable copy of the outline and brief for tooling; do not repeat it in the answer:", "```json", json.dumps(context, ensure_ascii=False), "```"]
    return SYSTEM_PROMPT, "\n".join(lines)


def answer_skeleton(brief: Brief, sections: list[dict]) -> str:
    lines = ["---", f"subject: {json.dumps(brief.subject.strip())}", "---", ""]
    for section in sections:
        lines.append(f"<!-- Section: {section['title']} -->")
        for field in section["fields"]:
            hint = f"{field['label']}: {field['kind']}"
            if field["max_chars"]:
                hint += f", about {field['max_chars']} characters"
            if field["token"]:
                hint += ", a few words only, it replaces a short placeholder"
            if field["guidance"]:
                hint += f". {field['guidance']}"
            lines.append(f"## {field['key']}")
            lines.append(f"<!-- {' '.join(hint.split())} -->")
            if field["kind"] == "table" and field["columns"]:
                lines.append("| " + " | ".join(field["columns"]) + " |")
                lines.append("|" + "---|" * len(field["columns"]))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


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


def strip_label_prefixes(content: Content, manifest: Manifest) -> None:
    # Models tend to start a value with "Label:"; the template already shows the label.
    for key, value in content.fields.items():
        spec = manifest.field(key)
        if spec is None or not isinstance(value, str):
            continue
        prefixes = {spec.label.strip().lower()} | {(b.keep_prefix or "").strip().rstrip(":").lower() for b in spec.bindings}
        first, sep, rest = value.partition(":")
        if sep and first.strip().lower() in prefixes - {""}:
            content.fields[key] = rest.lstrip(" ")


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip() + " …"


def _unfence(reply: str) -> str:
    match = FENCE_RE.match(reply.strip())
    return match.group(1) if match else reply
