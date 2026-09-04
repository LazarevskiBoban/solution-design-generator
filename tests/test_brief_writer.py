from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from sdgen.analyze import analyze_deck
from sdgen.blueprint import derive_blueprint
from sdgen.brief import Brief, brief_skeleton, dump_brief, load_brief
from sdgen.cli import main
from sdgen.inventory import inspect_deck
from sdgen.llm import DEFAULT_AZURE_API_VERSION, MAX_OUTPUT_TOKENS, LLMError, LLMNotConfigured, MockLLM, OpenAILLM, get_llm
from sdgen.manifest import GlobalSpec
from sdgen.registry import Registry
from sdgen.writer import build_prompt, draft_content, writable_sections

BRIEF = Brief(
    subject="Bank Statement (CAMT.053) Integration",
    about="Banks deliver CAMT.053 statements daily. They must be posted in SAP S/4HANA without manual upload.",
    problem_outcome="Today statements are uploaded by hand. Outcome: automatic import and clearing.",
    approach="Banks push files over SFTP to the middleware. SAP BTP validates the files and calls the S/4HANA bank statement API.",
    apis_references="Bank Statement API | https://api.sap.com/api/bankstatement | target\nCAMT.053 guide | https://example.org/camt053 | source format",
    investigation_notes="Three banks in scope. Retry policy to be confirmed.",
)


def _template(sample_deck, tmp_path):
    deck = inspect_deck(sample_deck)
    analysis = analyze_deck(deck)
    manifest = analysis.to_manifest("demo")
    manifest.globals.append(GlobalSpec(key="subject", replaces="Demo Integration"))
    blueprint = derive_blueprint(deck, analysis, manifest, "demo")
    return Registry(tmp_path / "templates").add("demo", sample_deck, manifest, blueprint=blueprint)


def test_brief_roundtrip_and_skeleton():
    text = dump_brief(BRIEF)
    assert text.startswith('---\nsubject: "Bank Statement (CAMT.053) Integration"\n---')
    assert load_brief(text) == BRIEF
    empty = load_brief(brief_skeleton())
    assert empty.is_empty and empty.facts == {}
    by_label = load_brief("---\nsubject: X\n---\n## What the integration is about\ntext\n")
    assert by_label.about == "text" and by_label.subject == "X"

    with_facts = BRIEF.model_copy(update={"facts": {"version": "0.1", "effort": "Architect | Design | 20 | 10 | 5"}, "acceptance_criteria": "1. Every file is posted once."})
    dumped = dump_brief(with_facts)
    assert "## fact:version\n0.1\n" in dumped and "## acceptance_criteria\n1. Every file is posted once." in dumped
    assert load_brief(dumped) == with_facts
    assert with_facts.facts_text().startswith("Document version: 0.1\nEffort by role:\nArchitect")


def test_fact_questions_follow_the_template(sample_deck, tmp_path):
    from sdgen.brief import fact_questions

    entry = _template(sample_deck, tmp_path)
    questions = {q.spec.key: q for q in fact_questions(entry.blueprint, entry.manifest)}
    assert questions["countries"].used_by == ["Executive Overview"]
    assert set(questions) >= {"systems", "volumes", "frequency", "environments"}
    assert "effort" not in questions and "version" not in questions


PROVIDER_VARS = ("SDGEN_LLM", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_API_VERSION")


def test_get_llm_defaults_to_mock_and_rejects_unconfigured(monkeypatch):
    for var in PROVIDER_VARS:
        monkeypatch.delenv(var, raising=False)
    assert get_llm().name == "mock"
    monkeypatch.setenv("SDGEN_LLM", "anthropic")
    with pytest.raises(LLMNotConfigured):
        get_llm()
    with pytest.raises(LLMNotConfigured):
        get_llm("nonsense")
    with pytest.raises(LLMNotConfigured, match="endpoint, API key, deployment"):
        get_llm("azure")
    with pytest.raises(LLMNotConfigured, match="OPENAI_API_KEY"):
        get_llm("openai")


def _fake_client(reply: str, finish: str = "stop"):
    completions = SimpleNamespace(calls=[])

    def create(**kwargs):
        completions.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply), finish_reason=finish)])

    completions.create = create
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_openai_adapter_sends_prompts_and_returns_text():
    client, completions = _fake_client("## a\nhello\n")
    llm = OpenAILLM(client, "gpt-test", name="azure")
    assert llm.name == "azure" and llm.complete("sys", "usr") == "## a\nhello\n"
    call = completions.calls[0]
    assert call["model"] == "gpt-test"
    assert call["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert call["max_completion_tokens"] == MAX_OUTPUT_TOKENS


def test_openai_adapter_reports_truncation_and_failures():
    client, _ = _fake_client("partial", finish="length")
    with pytest.raises(LLMError, match="output limit"):
        OpenAILLM(client, "m").complete("s", "u")

    def boom(**kwargs):
        raise RuntimeError("socket closed")

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=boom)))
    with pytest.raises(LLMError, match="socket closed"):
        OpenAILLM(client, "m").complete("s", "u")


def test_azure_factory_builds_client_from_settings_or_env(monkeypatch):
    openai = pytest.importorskip("openai")
    for var in PROVIDER_VARS:
        monkeypatch.delenv(var, raising=False)
    created = []

    class Recorder:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setattr(openai, "AzureOpenAI", Recorder)
    llm = get_llm("azure", endpoint=" https://demo.openai.azure.com/ ", api_key="k", model="gpt-5-deploy")
    assert llm.name == "azure" and llm.model == "gpt-5-deploy"
    assert created[-1] == {"api_key": "k", "azure_endpoint": "https://demo.openai.azure.com/", "api_version": DEFAULT_AZURE_API_VERSION}

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://env.openai.azure.com/")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "envkey")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gpt-env")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    monkeypatch.setenv("SDGEN_LLM", "azure")
    llm = get_llm()
    assert llm.model == "gpt-env"
    assert created[-1] == {"api_key": "envkey", "azure_endpoint": "https://env.openai.azure.com/", "api_version": "2025-04-01-preview"}


def test_draft_content_parses_a_fenced_provider_reply(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    reply = "```markdown\n---\nsubject: Bank Statement\n---\n<!-- Section: Executive Overview -->\n## business_need (Business Need)\nBusiness need: Banks deliver statements daily.\n\n## `Scope`\n| Function | Countries |\n|---|---|\n| Finance | ZA |\n\n## first_point\n- one\n```"
    client, _ = _fake_client(reply)
    result = draft_content(BRIEF, entry.blueprint, entry.manifest, OpenAILLM(client, "m", name="azure"))
    assert result.llm == "azure" and result.warnings == []
    assert result.content.globals["subject"] == "Bank Statement"
    assert result.content.fields["business_need"] == "Banks deliver statements daily."
    assert result.content.fields["scope"] == [{"Function": "Finance", "Countries": "ZA"}]
    assert result.content.fields["first_point"] == "- one"


def test_prompt_lists_sections_fields_and_brief(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    sections = writable_sections(entry.blueprint, entry.manifest)
    assert [s["title"] for s in sections] == ["Executive Overview"]
    keys = {f["key"] for f in sections[0]["fields"]}
    assert keys == {"business_need", "scope", "first_point"}
    system, user = build_prompt(BRIEF, entry.blueprint, entry.manifest)
    assert "Never invent" in system and "skeleton" in system
    assert "## Executive Overview (" in user
    fields_line = next(line for line in user.splitlines() if line.startswith("Fields: "))
    assert set(fields_line[8:].split(", ")) == keys
    assert "Banks deliver CAMT.053" in user and "```json" not in user
    skeleton = user.split("# Answer skeleton", 1)[1]
    assert skeleton.startswith('\n---\nsubject: "Bank Statement (CAMT.053) Integration"\n---\n')
    assert "<!-- Section: Executive Overview -->" in skeleton
    assert "## scope\n<!-- Scope: table" in skeleton and "| Function | Countries |\n|---|---|\n" in skeleton
    assert "## business_need\n<!-- Business Need: text" in skeleton
    _, with_context = build_prompt(BRIEF, entry.blueprint, entry.manifest, include_context=True)
    assert "```json" in with_context


def test_mock_draft_fills_every_writable_field(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    result = draft_content(BRIEF, entry.blueprint, entry.manifest, MockLLM())
    assert result.llm == "mock"
    content = result.content
    assert content.globals["subject"] == BRIEF.subject
    assert content.fields["business_need"].startswith("[Draft] Banks deliver")
    assert content.fields["first_point"].startswith("- [Draft]")
    assert content.fields["scope"][0]["Function"].startswith("[Draft]")
    assert content.fields["scope"][0]["Countries"] == "[TBC]"
    assert result.warnings == []
    assert "## business_need" in result.markdown


def test_mock_keeps_cover_subtitle_short():
    from sdgen.llm import mock_draft

    context = {
        "brief": {"subject": "CAMT.053 Integration", "about": "Long. Text. With. Many. Sentences."},
        "sections": [{"section": "cover", "title": "Cover", "kind": "cover", "ask": "", "fields": [{"key": "subtitle", "kind": "bullets"}]}],
    }
    assert "## subtitle\n[Draft] CAMT.053 Integration" in mock_draft(context)


def test_mock_respects_budgets_and_tokens():
    from sdgen.llm import mock_draft

    about = "First sentence is short. Second sentence adds quite a few more words to it. Third one is here."
    context = {
        "brief": {"subject": "X", "about": about},
        "sections": [
            {
                "section": "overview",
                "title": "Executive Overview",
                "kind": "composite",
                "ask": "",
                "fields": [
                    {"key": "need", "kind": "text", "max_chars": 40},
                    {"key": "effort", "kind": "text", "token": True},
                    {"key": "points", "kind": "bullets", "max_chars": 60},
                ],
            }
        ],
    }
    draft = mock_draft(context)
    assert "## need\n[Draft] First sentence is short.\n" in draft
    assert "## effort\n[TBC]\n" in draft
    assert draft.endswith("## points\n- [Draft] First sentence is short.\n")


def test_cli_draft_writes_content(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    brief_path = tmp_path / "brief.md"
    brief_path.write_text(dump_brief(BRIEF), encoding="utf-8")
    out = tmp_path / "content.md"
    result = CliRunner().invoke(main, ["draft", "demo", str(brief_path), "-o", str(out), "--templates", str(tmp_path / "templates")])
    assert result.exit_code == 0, result.output
    assert "Draft from 'mock'" in result.output
    assert "[Draft]" in out.read_text(encoding="utf-8")
    skeleton = CliRunner().invoke(main, ["brief"])
    assert skeleton.exit_code == 0 and "## approach" in skeleton.output
