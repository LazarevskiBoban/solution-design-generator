from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from sdgen.blueprint import Blueprint, composite_fields
from sdgen.brief import Brief, FactQuestion, dump_brief, split_joined
from sdgen.content import Content, detail_key, dump_markdown, is_detail_key, load_markdown, stem_of, validate_content
from sdgen.fill.text import strip_leading_label
from sdgen.grounding import GROUNDING_RULE, coverage_warnings, grounding_warnings, is_listed
from sdgen.llm import LLMClient, get_llm
from sdgen.manifest import Manifest
from sdgen.material import material_corpus, material_text
from sdgen.mechanical import mechanical_fills
from sdgen.plan import DEVELOPER_KEYS

SKIP_KINDS = {"static", "divider"}
EXAMPLE_CHARS = 400
FENCE_RE = re.compile(r"^```(?:markdown|md)?\s*\n(.*?)\n```\s*$", re.DOTALL)
NUMBER_RE = re.compile(r"\d[\d,.]*\s?%?")
NUMBERING_COLUMN_RE = re.compile(r"^(#|no\.?|ref(erence)?|id|seq(uence)?|depends on)$", re.IGNORECASE)
INTEGRATION_RE = re.compile(r"integration|architecture|mapping|flow|interface|api|duplicate|file|format", re.IGNORECASE)
QUALITY_RE = re.compile(r"deviation|success|criteria|report|analytic|effort|decision|question|acceptance|operation|test|risk|build", re.IGNORECASE)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
MIN_DUPLICATE_WORDS = 8
CHARS_PER_WORD = 6

SYSTEM_PROMPT = f"""You are a senior SAP integration solution architect writing a solution-design document
that developers will build and test from.
{GROUNDING_RULE}
You receive an answer skeleton. Return it filled in: keep every "## " heading exactly as written
and in the same order, replace each <!-- hint --> with the content, and add nothing else: no other
headings, no commentary, no code fence.
Under a heading: one paragraph per line; bullet lines start with "- " and are indented two spaces
per level; use **bold** sparingly; do not repeat the field label at the start of the content.
Write sharp and short. Each field states a character target and a line count: write between
50 and 85 percent of the characters and never more, using the structure shown for the field.
One idea per bullet, never more bullets than the field has lines, at most fifteen words per
bullet, no nested bullets unless the structure shows them; a box of three lines or fewer gets
one or two short lines. Prose: at most three sentences per paragraph; no filler such as "the
design must ensure that" or "it is important to note". Fields marked as a few words get at most
four words. The earlier document quoted in the outline shows structure and tone only: never
reuse its system names, wording or facts. Reference material attached to the brief is the
customer's own source material: use it like the brief.
Overview fields summarise for management: what, why and with which systems. Developer detail
(steps, checks, parameters, cut-off times, naming) goes to the build notes field when the
skeleton has one, never into an overview.
A field followed by a details field: the box holds the summary within its target; the details
field holds the complete, self-contained version for a plain slide that follows it, or stays
empty when the box already says everything; never a paraphrase of the box.
A table keeps its header row and gets one row per entry, at least one row, with exactly the
listed columns. A table fed by a list in the brief (acceptance criteria, decisions, operations,
references) gets one row per entry of that list, never fewer; rows that do not fit one slide
continue on a copy of it. A cell the brief does not state is [TBC]; the row stays. Never write
the same sentence into two fields; each field adds something new."""

ROUTING = {
    "overview": [
        "Business need follows the structure of the earlier document (problem, expected outcome, status) and uses the volumes from the facts; label each paragraph as the box header lists them (Business Need, Problem, Expected Outcomes).",
        "Executive overview boxes are the management summary: three to five sentences each, no parameter lists.",
        "Solution overview labels each system keep, change or new, taken from the systems fact.",
        "Landscape fields list platforms and systems, not process steps.",
        "Scope rows use the counterparts, countries and company codes from the facts. If the facts name no counterparts, write one row with [TBC: names] and never use example company names.",
    ],
    "integration": [
        "Integration overview steps: one row per hop of the approach, numbered in flow order.",
        "Architecture notes name the concrete protocols, endpoints, checks and error paths from the approach and the operations text.",
        "Where a slide asks for links, use the APIs and references lines.",
        "Mapping or file format fields describe the formats and schemas in scope from the references and the investigation details.",
    ],
    "quality": [
        "Deviations list only true departures from the SAP standard (custom code, non-standard process); candidates come from the investigation details and the approach. If there is none, write one row saying that the standard is followed.",
        "Success criteria and measures: one row per measurable target from the facts and the acceptance criteria; no invented percentages.",
        "Decisions come from the decisions log, numbered. Open questions have their own slide built from the brief's open questions field; write them only into a slide titled for them.",
        "Operations and error handling come from the operations text.",
        "Build notes hold what a developer needs that fits nowhere else: file naming, cut-off times, reprocessing steps, configuration keys, as short labelled paragraphs (Label: text).",
    ],
    "developer": [
        "These tables are what the developer builds from: one row per item, taken from the approach, the operations text, the facts and the reference material.",
        "Interface inventory: one row per party and flow from the scope and the counterparts; direction inbound or outbound; encryption as the brief states it.",
        "Configuration: one row per parameter a developer sets (adapter, folders, polling, data store, partner directory, keystore, alerts) with the value the brief gives.",
        "Connectivity: one row per endpoint per environment from the environments fact; host, port, account and key are [TBC] unless the brief states them.",
        "Security and access: one row per folder or resource and account; Read, Write and Move or delete as yes, no or [TBC].",
        "Cutover steps in order; RACI per activity the brief names (keys, endpoints, folders, runs); build checklist in dependency order: endpoints and access first, keys second, build last.",
        "Assumptions and constraints from the investigation details, each with a type and an owner; non-functional rows from the non-functional facts.",
        "Never invent hosts, ports, accounts, folders, names or numbers: [TBC] keeps the row.",
    ],
}


class DraftResult(BaseModel):
    markdown: str
    content: Content
    warnings: list[str] = Field(default_factory=list)
    llm: str = ""
    calls: int = 0
    mechanical: list[str] = Field(default_factory=list)


def draft_content(
    brief: Brief,
    blueprint: Blueprint,
    manifest: Manifest,
    llm: LLMClient | None = None,
    original: Content | None = None,
    repair: bool = True,
    skip_sections: set[str] | None = None,
    extras: list | None = None,
    token_only: set[str] | None = None,
) -> DraftResult:
    llm = llm or get_llm()
    if extras:
        from sdgen.plan import extended_blueprint, extended_manifest

        manifest = extended_manifest(manifest, blueprint, extras)
        blueprint = extended_blueprint(blueprint, extras)
    fixed = mechanical_fills(brief, blueprint, manifest, original)
    sections = writable_sections(blueprint, manifest, exclude=set(fixed.fields), skip_sections=skip_sections, token_only=token_only)
    content = Content(fields=dict(fixed.fields))
    calls = 0
    groups = group_sections(sections)
    for group, members in groups:
        system, user = build_prompt(brief, blueprint, manifest, include_context=_wants_context(llm), sections=members, group=group)
        drafted = _parse(llm.complete(system, user), manifest)
        calls += 1
        content.fields.update(drafted.fields)
        content.unknown.extend(drafted.unknown)
        if repair:
            missing = _missing_fields(members, content, manifest)
            if missing:
                repaired_sections = _restrict(members, missing)
                system, user = build_prompt(
                    brief,
                    blueprint,
                    manifest,
                    include_context=_wants_context(llm),
                    sections=repaired_sections,
                    group=group,
                    note="A previous answer left these fields empty or malformed. Fill exactly these fields.",
                )
                repaired = _parse(llm.complete(system, user), manifest)
                calls += 1
                content.fields.update({k: v for k, v in repaired.fields.items() if k in missing})
    if repair:
        calls += _repair_duplicates(brief, blueprint, manifest, llm, groups, content)
    strip_label_prefixes(content, manifest)
    if brief.subject.strip():
        content.globals["subject"] = brief.subject.strip()
    warnings = [w for w in validate_content(content, manifest) if "image" not in w]
    untouched = {k for k, v in content.fields.items() if getattr(manifest.field(k), "static", False) or (original is not None and original.fields.get(k) == v)}
    warnings += number_warnings(content, brief, manifest, skip=untouched)
    warnings += grounding_warnings(content, brief, manifest, skip=untouched)
    warnings += coverage_warnings(content, brief, manifest)
    warnings += duplicate_warnings(content)
    return DraftResult(
        markdown=dump_markdown(content, manifest),
        content=content,
        warnings=warnings,
        llm=_label(llm),
        calls=calls,
        mechanical=fixed.notes,
    )


def redraft_section(
    brief: Brief,
    blueprint: Blueprint,
    manifest: Manifest,
    section_key: str,
    instruction: str,
    current: Content,
    llm: LLMClient,
) -> dict:
    """Writes one section again with an instruction; returns only that section's fields."""
    sections = [s for s in writable_sections(blueprint, manifest) if s["section"] == section_key]
    if not sections:
        return {}
    keys = [f["key"] for f in sections[0]["fields"]]
    keys += [detail_key(k) for k in keys]
    existing = "\n".join(f"## {k}\n{_as_text(current.fields.get(k))}" for k in keys if current.fields.get(k) not in (None, "", []))
    note = "Rewrite this section. Instruction from the reviewer: " + instruction.strip()
    if existing:
        note += "\nCurrent draft of the section:\n" + existing
    system, user = build_prompt(brief, blueprint, manifest, include_context=_wants_context(llm), sections=sections, group=_group_of(sections[0]), note=note)
    drafted = _parse(llm.complete(system, user), manifest)
    strip_label_prefixes(drafted, manifest)
    return {k: v for k, v in drafted.fields.items() if k in keys}


def extract_facts(brief: Brief, questions: list[FactQuestion], llm: LLMClient) -> dict[str, str]:
    """Facts the model can read verbatim from the brief's prose; unknown facts are left out."""
    if not questions:
        return {}
    schema = {
        "type": "object",
        "properties": {q.spec.key: {"type": "string", "description": q.spec.guidance} for q in questions},
        "additionalProperties": False,
    }
    system = (
        "You extract facts from a solution-design brief and the reference material attached to it. Return a JSON object whose keys are the fact keys given. "
        "Include a key only when the brief or its reference material states the fact explicitly; copy values verbatim, do not guess or infer. "
        "Multi-line facts use one line per item in the format described. " + GROUNDING_RULE
    )
    block = material_text(brief.material)
    user = "# Fact keys\n" + "\n".join(f"- {q.spec.key}: {q.spec.label}. {q.spec.guidance}" for q in questions)
    user += ("\n\n# Reference material\n" + block if block else "") + "\n\n# Brief\n" + dump_brief(brief)
    found = llm.complete_json(system, user, schema, name="facts")
    wanted = {q.spec.key for q in questions}
    return {k: str(v).strip() for k, v in found.items() if k in wanted and str(v).strip()}


def build_prompt(
    brief: Brief,
    blueprint: Blueprint,
    manifest: Manifest,
    include_context: bool = False,
    sections: list[dict] | None = None,
    group: str | None = None,
    note: str | None = None,
) -> tuple[str, str]:
    sections = writable_sections(blueprint, manifest) if sections is None else sections
    lines = ["# Task", "Fill in the answer skeleton at the end of this message, using the outline, the brief and the facts."]
    if note:
        lines += ["", note]
    lines += ["", "# Outline"]
    for section in sections:
        lines.append(f"## {section['title']} ({section['kind']})")
        if section["ask"]:
            lines.append(f"What this section asks for: {section['ask']}")
        lines.append("Fields: " + ", ".join(f["key"] for f in section["fields"]))
        if section["example"]:
            lines.append("Earlier document, for structure and depth only (its facts belong to another project):")
            lines.append(example_outline(section["example"]))
        lines.append("")
    routing = ROUTING.get(group or "", [])
    if routing:
        lines += ["# How to use the brief", *[f"- {r}" for r in routing], ""]
    lines += ["# Facts (with the brief and its reference material, the only source for names, dates, numbers and identifiers)", brief.facts_text() or "(no facts given: write [TBC: question] where one is needed)", ""]
    block = material_text(brief.material, tags={s["section"] for s in sections})
    if block:
        lines += ["# Reference material (attached to the brief by its author; use it like the brief)", block, ""]
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
            if field["token"]:
                hint += ", a few words only, it replaces a short placeholder"
            elif field["max_chars"]:
                hint += f", target {field['max_chars']} characters"
                if field.get("max_lines"):
                    hint += f" on about {field['max_lines']} lines"
                hint += f" (about {max(1, field['max_chars'] // CHARS_PER_WORD)} words; write 50 to 85 percent of it"
                hint += "; a bullet takes at least one line)" if field.get("max_lines") else ")"
            elif field["kind"] == "table" and field.get("listed"):
                hint += ", one row per entry of the brief's list, never fewer"
                if field.get("max_rows"):
                    hint += f"; about {field['max_rows']} rows fit one slide and the rest continue on a copy of it"
            elif field["kind"] == "table" and field.get("max_rows"):
                hint += f", about {field['max_rows']} rows fit the slide; further rows continue on a copy of it"
            if field["guidance"]:
                hint += f". {field['guidance']}"
            lines.append(f"## {field['key']}")
            lines.append(f"<!-- {' '.join(hint.split())} -->")
            if field["kind"] == "table" and field["columns"]:
                lines.append("| " + " | ".join(field["columns"]) + " |")
                lines.append("|" + "---|" * len(field["columns"]))
            lines.append("")
            if field.get("details"):
                lines.append(f"## {detail_key(field['key'])}")
                lines.append(f"<!-- Optional. The complete {field['label']} for a plain slide right after this one when the box cannot hold it all: full text, or the full table with the same columns; leave empty when the box text says it all. -->")
                if field["kind"] == "table" and field["columns"]:
                    lines.append("| " + " | ".join(field["columns"]) + " |")
                    lines.append("|" + "---|" * len(field["columns"]))
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def example_outline(example: str) -> str:
    """The labels and sub-headings of the earlier text, then a short excerpt."""
    headings: list[str] = []
    for raw in example.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        label, sep, rest = line.partition(":")
        if sep and 0 < len(label) <= 40 and not label[0].isdigit():
            headings.append(label.strip())
        elif len(line) <= 40 and not line.endswith(".") and not line.startswith(("-", "|")) and "|" not in line:
            headings.append(line)
    seen: list[str] = []
    for heading in headings:
        if heading not in seen:
            seen.append(heading)
    text = " ".join(example.split())
    excerpt = text if len(text) <= EXAMPLE_CHARS else text[:EXAMPLE_CHARS].rsplit(" ", 1)[0] + " …"
    parts = []
    if seen:
        parts.append("Structure: " + "; ".join(seen[:12]))
    parts.append('Excerpt: """' + excerpt + '"""')
    return "\n".join(parts)


def writable_sections(
    blueprint: Blueprint,
    manifest: Manifest,
    exclude: set[str] | None = None,
    skip_sections: set[str] | None = None,
    token_only: set[str] | None = None,
) -> list[dict]:
    """Sections and fields to write; `token_only` sections are kept as they are except for their placeholder tokens."""
    result = []
    detailed = set(composite_fields(blueprint, manifest))
    for section in blueprint.sections:
        if section.kind in SKIP_KINDS or section.key in (skip_sections or set()) or section.generated:
            continue
        fields = [manifest.field(k) for k in section.fields]
        fields = [f for f in fields if f is not None and f.kind != "image" and f.key not in (exclude or set())]
        if section.key in (token_only or set()):
            fields = [f for f in fields if any(b.mode == "token" for b in f.bindings)]
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
                        "max_lines": max((b.max_lines or 0) for b in f.bindings) or None,
                        "max_rows": max((b.max_rows or 0) for b in f.bindings) or None,
                        "token": any(b.mode == "token" for b in f.bindings),
                        "guidance": f.guidance,
                        "listed": f.kind == "table" and is_listed(f.label, f.key),
                        "details": f.key in detailed and not any(b.mode == "token" for b in f.bindings),
                    }
                    for f in fields
                ],
            }
        )
    return result


def group_sections(sections: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {"overview": [], "integration": [], "quality": [], "developer": []}
    for section in sections:
        groups[_group_of(section)].append(section)
    return [(name, members) for name, members in groups.items() if members]


def number_warnings(content: Content, brief: Brief, manifest: Manifest, skip: set[str] | None = None) -> list[str]:
    known = " ".join([dump_brief(brief), brief.facts_text(), material_corpus(brief.material)])
    warnings = []
    for key, value in content.fields.items():
        spec = manifest.field(key)
        if spec is None or key in (skip or set()):
            continue
        if isinstance(value, list) and value and isinstance(value[0], dict):
            text = " ".join(str(cell) for row in value for name, cell in row.items() if not NUMBERING_COLUMN_RE.match(name.strip()))
        elif isinstance(value, str):
            text = value
        else:
            continue
        strange = []
        for match in NUMBER_RE.findall(text):
            token = match.strip().rstrip(",.")
            digits = token.rstrip("%").strip().rstrip(",.")
            if not digits or len(digits.replace(",", "").replace(".", "")) < 2 or token in known or digits in known:
                continue
            if token not in strange:
                strange.append(token)
        if strange:
            warnings.append(f"field '{key}' ({spec.label}) uses numbers not found in the brief: {', '.join(strange[:5])}")
    return warnings


def duplicate_fields(content: Content) -> dict[str, str]:
    """Fields that repeat a sentence of an earlier field, mapped to that field."""
    seen: dict[str, str] = {}
    repeats: dict[str, str] = {}
    for key, value in content.fields.items():
        if is_detail_key(key):
            continue  # the complete version repeats its box by design
        for sentence in _sentences_of(value):
            normalised = " ".join(sentence.lower().split())
            if len(normalised.split()) < MIN_DUPLICATE_WORDS:
                continue
            owner = seen.setdefault(normalised, key)
            if owner != key:
                repeats.setdefault(key, owner)
    return repeats


def duplicate_warnings(content: Content) -> list[str]:
    return [f"field '{key}' repeats a sentence of '{owner}'" for key, owner in duplicate_fields(content).items()]


def _sentences_of(value) -> list[str]:
    if isinstance(value, list):
        texts = [str(v) for row in value if isinstance(row, dict) for v in row.values()]
    elif isinstance(value, str):
        texts = [line.lstrip("-*• ").strip() for line in value.splitlines()]
    else:
        return []
    return [s.strip() for text in texts for s in SENTENCE_RE.split(text) if s.strip()]


def _repair_duplicates(brief: Brief, blueprint: Blueprint, manifest: Manifest, llm: LLMClient, groups: list, content: Content) -> int:
    """One call per group whose fields repeat another field; returns the number of calls made."""
    repeats = duplicate_fields(content)
    calls = 0
    for group, members in groups:
        keys = {k for k in repeats if any(k == f["key"] for s in members for f in s["fields"])}
        if not keys:
            continue
        note = "A previous answer repeated sentences across fields. Rewrite exactly these fields so each adds something new: " + "; ".join(f"{k} repeats {repeats[k]}" for k in sorted(keys)) + "."
        system, user = build_prompt(brief, blueprint, manifest, include_context=_wants_context(llm), sections=_restrict(members, keys), group=group, note=note)
        repaired = _parse(llm.complete(system, user), manifest)
        calls += 1
        content.fields.update({k: v for k, v in repaired.fields.items() if k in keys})
    return calls


def strip_label_prefixes(content: Content, manifest: Manifest) -> None:
    # Models tend to start a value with the label; the template already shows it.
    for key, value in content.fields.items():
        spec = manifest.field(stem_of(key))
        if spec is None or not isinstance(value, str):
            continue
        content.fields[key] = strip_leading_label(value, [spec.label] + [b.keep_prefix or "" for b in spec.bindings])


RESPLIT_PROMPT = """You reformat pasted notes. The text holds several entries pasted onto one line.
Return the same text with one entry per line, each entry keeping its cells separated by " | ",
laid out as: {guidance}
Copy every word exactly as given; add, drop or reorder nothing; return only the lines."""


def resplit_lines(llm: LLMClient, label: str, text: str, cells: int, guidance: str = "") -> str:
    """One entry per line for a field pasted as one line; the reply counts only when every word survives."""
    reply = _unfence(llm.complete(RESPLIT_PROMPT.format(guidance=guidance or label), text)).strip()
    if reply and _squash(reply) == _squash(text):
        return reply
    return split_joined(text, cells)


def _squash(text: str) -> str:
    return "".join(text.split())


def _group_of(section: dict) -> str:
    text = f"{section['title']} {section['kind']}"
    if section["section"] in DEVELOPER_KEYS:
        return "developer"
    if section["kind"] in ("cover",):
        return "overview"
    if QUALITY_RE.search(text) or section["kind"] == "references":
        return "quality"
    if INTEGRATION_RE.search(text) or section["kind"] in ("diagram", "mapping"):
        return "integration"
    return "overview"


def _missing_fields(sections: list[dict], content: Content, manifest: Manifest) -> set[str]:
    missing = set()
    for section in sections:
        for field in section["fields"]:
            value = content.fields.get(field["key"])
            if value in (None, "", []):
                missing.add(field["key"])
            elif field["kind"] == "table" and isinstance(value, list) and field["columns"]:
                known = {c.strip().lower() for c in field["columns"]}
                if any(str(k).strip().lower() not in known for row in value for k in row):
                    missing.add(field["key"])
    return missing


def _restrict(sections: list[dict], keys: set[str]) -> list[dict]:
    result = []
    for section in sections:
        fields = [f for f in section["fields"] if f["key"] in keys]
        if fields:
            result.append({**section, "fields": fields})
    return result


def _parse(reply: str, manifest: Manifest) -> Content:
    return load_markdown(_unfence(reply), manifest)


def _wants_context(llm: LLMClient) -> bool:
    return bool(getattr(llm, "wants_context", False))


def _label(llm: LLMClient) -> str:
    return str(getattr(llm, "label", llm.name))


def _as_text(value) -> str:
    if isinstance(value, list):
        return "\n".join(" | ".join(str(c) for c in row.values()) for row in value if isinstance(row, dict))
    return str(value or "")


def _unfence(reply: str) -> str:
    match = FENCE_RE.match(reply.strip())
    return match.group(1) if match else reply


__all__ = [
    "DraftResult",
    "SYSTEM_PROMPT",
    "answer_skeleton",
    "build_prompt",
    "draft_content",
    "example_outline",
    "extract_facts",
    "group_sections",
    "number_warnings",
    "redraft_section",
    "strip_label_prefixes",
    "writable_sections",
]
