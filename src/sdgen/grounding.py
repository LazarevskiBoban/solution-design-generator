"""The brief is the only source: the draft carries nothing more than it names and nothing less than it lists."""

from __future__ import annotations

import re

from sdgen.brief import BRIEF_FIELDS, Brief, dump_brief
from sdgen.content import Content
from sdgen.manifest import Manifest
from sdgen.material import material_corpus

GROUNDING_RULE = """Sources: the brief, its facts and the reference material attached to it, nothing else. Every
name, system, host, path, folder, parameter value, date, number, identifier and step you write
must appear there; where the brief does not state something, write [TBC: what to ask] instead of
filling the gap. General product knowledge may explain a term the brief uses but never adds a
value, a component, a name or a step the brief does not name. When unsure, leave it out."""

TBC_RE = re.compile(r"\[TBC[^\]]*\]", re.IGNORECASE)
TOKEN_RE = re.compile(r"[A-Za-z0-9</][A-Za-z0-9_./:<>\-]*")
IDENTIFIER_RE = re.compile(r"[_/:.]|\d|^[A-Z]{3,}$|^[A-Z][a-z]+[A-Z]")
REFERENCE_ID_RE = re.compile(r"^[A-Z]+(?:-[A-Z]+)*-\d+$")  # GAP-APP-01, INT-IN-01: numbering the writer makes up
MARKER_RE = re.compile(r"(?:^|\s)\d{1,3}\.\s")
IGNORED_TERMS = {"n/a", "tbc", "id", "etc."}
PLACEHOLDER_PREFIX = "[To be completed"
# Brief lists and the pattern that finds the field they feed, by label or key.
COVERAGE: list[tuple[str, re.Pattern[str]]] = [
    ("acceptance_criteria", re.compile(r"acceptance|test scenario", re.IGNORECASE)),
    ("decisions_log", re.compile(r"\bdecision", re.IGNORECASE)),
    ("operations", re.compile(r"operation|error handling|runbook", re.IGNORECASE)),
    ("open_questions", re.compile(r"open question", re.IGNORECASE)),
]
_LABELS = {key: label for key, label, _ in BRIEF_FIELDS}


def corpus(brief: Brief) -> str:
    """Everything the author supplied, lower-cased, for membership checks."""
    return "\n".join([dump_brief(brief), brief.facts_text(), material_corpus(brief.material)]).lower()


def ungrounded_terms(text: str, corpus_text: str, ignore: set[str] | None = None) -> list[str]:
    """Identifier-like words of the text (codes, paths, hosts, CamelCase and ALLCAPS names) that the corpus never mentions."""
    skip = IGNORED_TERMS | {term.lower() for term in (ignore or set())}
    found: list[str] = []
    for token in TOKEN_RE.findall(TBC_RE.sub(" ", text)):
        token = token.strip(".,;:-()")
        if len(token) < 3 or token.isdigit() or not IDENTIFIER_RE.search(token) or REFERENCE_ID_RE.match(token):
            continue
        lowered = token.lower()
        if lowered in skip or lowered in corpus_text:
            continue
        if token not in found:
            found.append(token)
    return found


def grounding_warnings(content: Content, brief: Brief, manifest: Manifest) -> list[str]:
    known = corpus(brief)
    warnings = []
    for key, value in content.fields.items():
        spec = manifest.field(key)
        if spec is None:
            continue
        text = _text_of(value)
        if not text:
            continue
        strange = ungrounded_terms(text, known, ignore={spec.label, *spec.columns})
        if strange:
            warnings.append(f"field '{key}' ({spec.label}) names things not in the brief: {', '.join(strange[:6])}")
    return warnings


def entry_count(text: str) -> int:
    """How many entries a brief list holds: numbered items, pasted table rows or one sentence per line."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    tabbed = [line for line in lines if line.count("\t") >= 2]
    if len(tabbed) >= 2:
        return len(tabbed) - 1  # the first tab-separated line is the pasted header
    count = 0
    for line in lines:
        markers = len(MARKER_RE.findall(line))
        if markers:
            count += markers
        elif " | " in line or line.endswith((".", "?", "!", ";")) or line.startswith(("-", "*", "•")):
            count += 1
    return count


def written_count(value) -> int:
    if isinstance(value, list):
        return sum(1 for row in value if isinstance(row, dict) and not str(next(iter(row.values()), "")).startswith(PLACEHOLDER_PREFIX))
    if isinstance(value, str):
        return sum(1 for line in value.splitlines() if line.strip())
    return 0


def coverage_warnings(content: Content, brief: Brief, manifest: Manifest) -> list[str]:
    """Fields fed by a list in the brief that carry fewer entries than the list."""
    warnings = []
    for brief_key, pattern in COVERAGE:
        expected = entry_count(getattr(brief, brief_key, ""))
        if expected < 2:
            continue
        for key, value in content.fields.items():
            spec = manifest.field(key)
            if spec is None or not pattern.search(f"{spec.label} {spec.key}"):
                continue
            written = written_count(value)
            if 0 < written < expected:
                warnings.append(f"field '{key}' ({spec.label}) covers {written} of {expected} entries of the brief's {_LABELS.get(brief_key, brief_key).lower()}")
    return warnings


def is_listed(label: str, key: str) -> bool:
    """Whether a field is fed by one of the brief's lists."""
    return any(pattern.search(f"{label} {key}") for _, pattern in COVERAGE)


def _text_of(value) -> str:
    if isinstance(value, list):
        return " ".join(str(cell) for row in value if isinstance(row, dict) for cell in row.values())
    return value if isinstance(value, str) else ""
