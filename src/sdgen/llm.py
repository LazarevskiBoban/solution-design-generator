from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol

CONTEXT_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
PROVIDERS = ("mock", "azure", "openai", "anthropic")
MAX_OUTPUT_TOKENS = 16000
REASONING_OUTPUT_TOKENS = 32000
DEFAULT_AZURE_API_VERSION = "2025-04-01-preview"
DEFAULT_OPENAI_MODEL = "gpt-5"
REASONING_MODEL_RE = re.compile(r"^(gpt-5|o\d)", re.IGNORECASE)
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
MOCK_JSON: dict[str, dict] = {"facts": {}}


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, user: str) -> str: ...

    def complete_json(self, system: str, user: str, schema: dict, name: str = "result") -> dict: ...


class LLMNotConfigured(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class MockLLM:
    name = "mock"
    label = "mock"
    wants_context = True

    def complete(self, system: str, user: str) -> str:
        match = CONTEXT_RE.search(user)
        context = json.loads(match.group(1)) if match else {}
        return mock_draft(context)

    def complete_json(self, system: str, user: str, schema: dict, name: str = "result") -> dict:
        return dict(MOCK_JSON.get(name, {}))


class OpenAILLM:
    """Chat-completions adapter; the client is either an OpenAI or an AzureOpenAI client."""

    def __init__(self, client: Any, model: str, name: str = "openai", effort: str = "low") -> None:
        self.name = name
        self.model = model
        self.effort = effort
        self._client = client

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}"

    @property
    def reasoning(self) -> bool:
        return bool(REASONING_MODEL_RE.match(self.model))

    def with_effort(self, effort: str) -> OpenAILLM:
        return OpenAILLM(self._client, self.model, self.name, effort)

    def complete(self, system: str, user: str) -> str:
        try:
            return self._request(system, user)
        except Exception as exc:
            raise _translate(exc, self.model) from exc

    def complete_json(self, system: str, user: str, schema: dict, name: str = "result") -> dict:
        # Newest first: schema-constrained output, JSON mode, then plain text parsed by hand.
        attempts = [
            {"response_format": {"type": "json_schema", "json_schema": {"name": name, "schema": schema}}},
            {"response_format": {"type": "json_object"}},
            {},
        ]
        hint = f"\nReturn only a JSON object that matches this schema:\n{json.dumps(schema)}"
        failure: Exception | None = None
        for index, extra in enumerate(attempts):
            try:
                text = self._request(system if index == 0 else system + hint, user, **extra)
            except Exception as exc:
                if _is_bad_request(exc) and index < len(attempts) - 1:
                    failure = exc
                    continue
                raise _translate(exc, self.model) from exc
            try:
                return parse_json(text)
            except ValueError as exc:
                failure = exc
        raise LLMError(f"'{self.model}' did not return valid JSON: {failure}")

    def _request(self, system: str, user: str, **extra: Any) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_completion_tokens": REASONING_OUTPUT_TOKENS if self.reasoning else MAX_OUTPUT_TOKENS,
        }
        if self.reasoning:
            kwargs["reasoning_effort"] = self.effort
        kwargs.update(extra)
        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMError(f"'{self.model}' stopped at the output limit, so the draft is incomplete; shorten the brief or the outline")
        if choice.finish_reason == "content_filter":
            raise LLMError(f"'{self.model}' declined the request (content filter)")
        return choice.message.content or ""


def parse_json(text: str) -> dict:
    body = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", body, re.DOTALL)
    if fence:
        body = fence.group(1)
    match = JSON_OBJECT_RE.search(body)
    if not match:
        raise ValueError("no JSON object in the reply")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("the reply is not a JSON object")
    return data


def _is_bad_request(exc: Exception) -> bool:
    return type(exc).__name__ == "BadRequestError"


def get_llm(name: str | None = None, **settings: str | None) -> LLMClient:
    chosen = (name or os.environ.get("SDGEN_LLM") or "mock").strip().lower()
    if chosen == "mock":
        return MockLLM()
    if chosen == "azure":
        return azure_llm(**settings)
    if chosen == "openai":
        return openai_llm(**settings)
    if chosen in PROVIDERS:
        raise LLMNotConfigured(f"provider '{chosen}' is not set up yet; use azure, openai or mock")
    raise LLMNotConfigured(f"unknown provider '{chosen}'; choose one of {', '.join(PROVIDERS)}")


def azure_llm(
    api_key: str | None = None,
    endpoint: str | None = None,
    model: str | None = None,
    api_version: str | None = None,
    **_: str | None,
) -> OpenAILLM:
    openai = _sdk()
    api_key = _setting(api_key, "AZURE_OPENAI_API_KEY")
    endpoint = _setting(endpoint, "AZURE_OPENAI_ENDPOINT")
    model = _setting(model, "AZURE_OPENAI_DEPLOYMENT")
    api_version = _setting(api_version, "AZURE_OPENAI_API_VERSION") or DEFAULT_AZURE_API_VERSION
    missing = [label for label, value in (("endpoint", endpoint), ("API key", api_key), ("deployment", model)) if not value]
    if missing:
        raise LLMNotConfigured(
            "Azure OpenAI needs " + ", ".join(missing) + " (AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT or the sidebar settings)"
        )
    client = openai.AzureOpenAI(api_key=api_key, azure_endpoint=endpoint, api_version=api_version)
    return OpenAILLM(client, model, name="azure")


def openai_llm(api_key: str | None = None, model: str | None = None, **_: str | None) -> OpenAILLM:
    openai = _sdk()
    api_key = _setting(api_key, "OPENAI_API_KEY")
    if not api_key:
        raise LLMNotConfigured("OpenAI needs an API key (OPENAI_API_KEY or the sidebar settings)")
    model = _setting(model, "SDGEN_OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    return OpenAILLM(openai.OpenAI(api_key=api_key), model, name="openai")


def _sdk() -> Any:
    try:
        import openai
    except ImportError as exc:
        raise LLMNotConfigured("the 'openai' package is missing; run pip install -e .[llm]") from exc
    return openai


def _setting(value: str | None, env: str) -> str:
    return (value or "").strip() or (os.environ.get(env) or "").strip()


def _translate(exc: Exception, model: str) -> Exception:
    if isinstance(exc, (LLMError, LLMNotConfigured)):
        return exc
    try:
        import openai
    except ImportError:
        return LLMError(str(exc))
    if isinstance(exc, openai.AuthenticationError):
        return LLMNotConfigured("the provider rejected the API key")
    if isinstance(exc, openai.NotFoundError):
        return LLMError(f"model or deployment '{model}' was not found at this endpoint")
    if isinstance(exc, openai.RateLimitError):
        return LLMError("rate limit reached; try again in a moment")
    if isinstance(exc, openai.APIStatusError):
        return LLMError(f"provider error {exc.status_code}: {exc.message}")
    if isinstance(exc, openai.APIConnectionError):
        return LLMError("could not reach the endpoint; check the URL and the network")
    return LLMError(str(exc))


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
            budget = field.get("max_chars") or 0
            if field.get("token"):
                lines.append("[TBC]")
                lines.append("")
                continue
            if budget:
                source = _fit(source, max(budget - 8, 20))
            if kind == "table":
                columns = field.get("columns") or ["value"]
                if _is_reference(columns):
                    rows = _reference_rows(brief.get("apis_references", ""), len(columns))
                else:
                    rows = [[f"[Draft] {_first_sentence(source)}"] + ["[TBC]"] * (len(columns) - 1)]
                lines.append("| " + " | ".join(columns) + " |")
                lines.append("|" + "---|" * len(columns))
                lines.extend("| " + " | ".join(row) + " |" for row in rows)
            elif kind == "bullets" and section.get("kind") != "cover":
                lines.extend(f"- [Draft] {s}" for s in (_sentences(source)[:4] or ["[TBC]"]))
            else:
                lines.append(f"[Draft] {source or '[TBC]'}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _pick_source(section: dict, brief: dict) -> str:
    if section.get("kind") == "cover":
        return str(brief.get("subject", "") or "").strip()
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


def _fit(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    kept: list[str] = []
    for sentence in _sentences(text):
        candidate = " ".join(kept + [sentence])
        if len(candidate) > budget:
            break
        kept.append(sentence)
    return " ".join(kept) if kept else text[: budget - 1].rstrip() + "…"


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
