from __future__ import annotations

import json
import os
import re
from typing import Protocol

CONTEXT_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
PROVIDERS = ("mock", "anthropic", "azure")


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...


class LLMNotConfigured(RuntimeError):
    pass


class MockLLM:
    name = "mock"

    def complete(self, system: str, user: str) -> str:
        match = CONTEXT_RE.search(user)
        context = json.loads(match.group(1)) if match else {}
        return mock_draft(context)


def get_llm(name: str | None = None) -> LLMClient:
    chosen = (name or os.environ.get("SDGEN_LLM") or "mock").strip().lower()
    if chosen == "mock":
        return MockLLM()
    if chosen in PROVIDERS:
        raise LLMNotConfigured(f"provider '{chosen}' is not set up yet; set SDGEN_LLM=mock until a key is configured")
    raise LLMNotConfigured(f"unknown provider '{chosen}'; choose one of {', '.join(PROVIDERS)}")


def mock_draft(context: dict) -> str:
    brief = context.get("brief", {})
    lines: list[str] = []
    subject = str(brief.get("subject", "") or "")
    if subject:
        lines += ["---", f"subject: {json.dumps(subject)}", "---", ""]
    for section in context.get("sections", []):
        source = _pick_source(section, brief)
        for field in section.get("fields", []):
            lines.append(f"## {field['key']}")
            kind = field.get("kind", "text")
            if kind == "table":
                columns = field.get("columns") or ["value"]
                if _is_reference(columns):
                    rows = _reference_rows(brief.get("apis_references", ""), len(columns))
                else:
                    rows = [[f"[Draft] {_first_sentence(source)}"] + ["[TBC]"] * (len(columns) - 1)]
                lines.append("| " + " | ".join(columns) + " |")
                lines.append("|" + "---|" * len(columns))
                lines.extend("| " + " | ".join(row) + " |" for row in rows)
            elif kind == "bullets":
                lines.extend(f"- [Draft] {s}" for s in (_sentences(source)[:4] or ["[TBC]"]))
            else:
                lines.append(f"[Draft] {source or '[TBC]'}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _pick_source(section: dict, brief: dict) -> str:
    words = " ".join([section.get("title", ""), section.get("ask", "")] + [f.get("label", "") for f in section.get("fields", [])]).lower()
    order: list[str]
    if "mapping" in words:
        order = ["mapping_summary", "approach", "about"]
    elif "reference" in words or "api" in words:
        order = ["apis_references", "investigation_notes", "about"]
    elif any(w in words for w in ("investigat", "note", "requirement", "deviation", "dependenc")):
        order = ["investigation_notes", "approach", "about"]
    elif any(w in words for w in ("solution", "architecture", "integration", "design", "flow", "effort")):
        order = ["approach", "about", "problem_outcome"]
    else:
        order = ["about", "problem_outcome", "approach"]
    for key in order:
        value = str(brief.get(key, "") or "").strip()
        if value:
            return " ".join(value.split())
    return ""


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_RE.split(text) if s.strip()]


def _first_sentence(text: str) -> str:
    sentences = _sentences(text)
    return sentences[0] if sentences else "[TBC]"


def _is_reference(columns: list[str]) -> bool:
    return any("url" in c.lower() or "source" in c.lower() for c in columns)


def _reference_rows(text: str, width: int) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if any(parts):
            rows.append((parts + [""] * width)[:width])
    return rows or [["[TBC]"] * width]
