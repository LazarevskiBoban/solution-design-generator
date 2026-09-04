import pytest
from click.testing import CliRunner

from sdgen.analyze import analyze_deck
from sdgen.blueprint import derive_blueprint
from sdgen.brief import Brief, brief_skeleton, dump_brief, load_brief
from sdgen.cli import main
from sdgen.inventory import inspect_deck
from sdgen.llm import LLMNotConfigured, MockLLM, get_llm
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
    assert empty.is_empty
    by_label = load_brief("---\nsubject: X\n---\n## What the integration is about\ntext\n")
    assert by_label.about == "text" and by_label.subject == "X"


def test_get_llm_defaults_to_mock_and_rejects_unconfigured(monkeypatch):
    monkeypatch.delenv("SDGEN_LLM", raising=False)
    assert get_llm().name == "mock"
    monkeypatch.setenv("SDGEN_LLM", "anthropic")
    with pytest.raises(LLMNotConfigured):
        get_llm()
    with pytest.raises(LLMNotConfigured):
        get_llm("nonsense")


def test_prompt_lists_sections_fields_and_brief(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    sections = writable_sections(entry.blueprint, entry.manifest)
    assert [s["title"] for s in sections] == ["Executive Overview"]
    keys = {f["key"] for f in sections[0]["fields"]}
    assert keys == {"business_need", "scope", "first_point"}
    system, user = build_prompt(BRIEF, entry.blueprint, entry.manifest)
    assert "Never invent" in system
    assert "## Executive Overview" in user and "- scope — Scope (table; columns: Function, Countries)" in user
    assert "Banks deliver CAMT.053" in user and "```json" in user


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
