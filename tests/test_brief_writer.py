from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from sdgen.analyze import analyze_deck
from sdgen.blueprint import derive_blueprint
from sdgen.brief import Brief, brief_skeleton, dump_brief, load_brief
from sdgen.cli import main
from sdgen.inventory import inspect_deck
from sdgen.blueprint import Blueprint, Section
from sdgen.content import Content
from sdgen.llm import DEFAULT_AZURE_API_VERSION, MAX_OUTPUT_TOKENS, LLMError, LLMNotConfigured, MockLLM, OpenAILLM, get_llm, parse_json
from sdgen.manifest import Binding, FieldSpec, GlobalSpec, Manifest, ShapeRef
from sdgen.registry import Registry
from sdgen.writer import build_prompt, draft_content, example_outline, extract_facts, redraft_section, writable_sections

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
    asked = BRIEF.model_copy(update={"open_questions": "Which bank sends BAI2? | Treasury"})
    assert "## open_questions\nWhich bank sends BAI2? | Treasury" in dump_brief(asked) and load_brief(dump_brief(asked)) == asked
    assert load_brief(text).open_questions == "" and "## open_questions" in brief_skeleton()
    assert load_brief("---\nsubject: X\n---\n## decisions_log\nold notes\n").decisions_log == "old notes"


def test_joined_lines_are_detected_and_split():
    from sdgen.brief import brief_lint, looks_joined, split_joined, table_cut_short

    joined = "Which bank? | Treasury Is PGP needed? | Security"
    assert looks_joined(joined, 2) and not looks_joined("Which bank? | Treasury", 2)
    assert not looks_joined("File based | Programme | decided", 3) and looks_joined("A | P | decided B | P | open", 3)
    assert split_joined("q1 | who1 | q2 | who2", 2) == "q1 | who1\nq2 | who2"
    assert split_joined("A | P | decided B | P | open", 3) == "A | P | decided B\nP | open"
    assert table_cut_short("Flow\tSource\tTarget\nLockbox in\tVM\tS/4\nStatements in\tVM")
    assert not table_cut_short("Flow\tSource\nLockbox\tVM") and not table_cut_short("plain prose | with one bar")
    brief = BRIEF.model_copy(update={"open_questions": joined, "decisions_log": "A | P | decided", "facts": {"systems": "S/4 | target | keep BTP | middleware | new"}})
    found = dict(brief_lint(brief))
    assert set(found) == {"open_questions", "fact:systems"} and "one entry per line" in found["open_questions"]
    assert brief_lint(BRIEF) == []


def test_resplit_lines_trusts_only_a_verbatim_reply():
    from sdgen.writer import resplit_lines

    text = "Which bank? | Treasury Is PGP needed? | Security"
    good = _ScriptedLLM(["Which bank? | Treasury\nIs PGP needed? | Security"])
    assert resplit_lines(good, "Open questions", text, 2) == "Which bank? | Treasury\nIs PGP needed? | Security"
    assert good.prompts == [text]
    reworded = _ScriptedLLM(["Which bank? | Treasury\nIs PGP required? | Security"])
    assert resplit_lines(reworded, "Open questions", text, 2) == "Which bank? | Treasury Is PGP needed?\nSecurity"


def test_draft_warnings_include_ungrounded_terms(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    reply = "---\nsubject: X\n---\n## business_need\nBanks deliver statements daily; RFEBLB00 posts them.\n## scope\n| Function | Countries |\n|---|---|\n| Finance | ZA |\n## first_point\n- one\n"
    result = draft_content(BRIEF, entry.blueprint, entry.manifest, _ScriptedLLM([reply]))
    assert any("names things not in the brief: RFEBLB00" in w for w in result.warnings)
    untouched = Content(fields={"business_need": "Banks deliver statements daily; RFEBLB00 posts them."})
    quiet = draft_content(BRIEF, entry.blueprint, entry.manifest, _ScriptedLLM([reply]), original=untouched)
    assert not any("RFEBLB00" in w for w in quiet.warnings)
    skeleton = build_prompt(BRIEF, entry.blueprint, entry.manifest)[1]
    assert "Sources: the brief, its facts and the reference material" in build_prompt(BRIEF, entry.blueprint, entry.manifest)[0] and "## scope" in skeleton


def test_skeleton_offers_details_for_composite_fields(sample_deck, tmp_path):
    from sdgen.writer import answer_skeleton

    entry = _template(sample_deck, tmp_path)
    skeleton = answer_skeleton(BRIEF, writable_sections(entry.blueprint, entry.manifest))
    for key in ("business_need", "scope", "first_point"):
        assert f"## {key}_details" in skeleton and skeleton.index(f"## {key}_details") > skeleton.index(f"## {key}\n")
    block = skeleton.split("## scope_details", 1)[1].split("## ", 1)[0]
    assert "| Function | Countries |" in block and "leave empty when the box text says it all" in block


def test_draft_parses_details_and_keeps_them_out_of_duplicate_warnings(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    sentence = "Banks deliver statements daily and they must be posted automatically."
    reply = f"---\nsubject: X\n---\n## business_need\n{sentence}\n## business_need_details\n{sentence}\nMore detail for the developers here.\n## scope\n| Function | Countries |\n|---|---|\n| Finance | ZA |\n## first_point\n- one\n"
    result = draft_content(BRIEF, entry.blueprint, entry.manifest, _ScriptedLLM([reply]))
    assert result.calls == 1 and result.content.fields["business_need_details"].startswith(sentence)
    assert not any("repeats a sentence" in w for w in result.warnings)
    assert result.markdown.index("## business_need_details") < result.markdown.index("## scope")


def test_redraft_section_returns_details_too(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    section = entry.blueprint.sections[0].key
    reply = "## business_need\nShort.\n## business_need_details\nLong version.\n## scope\n| Function | Countries |\n|---|---|\n| F | ZA |\n## first_point\n- x\n"
    llm = _ScriptedLLM([reply])
    fields = redraft_section(BRIEF, entry.blueprint, entry.manifest, section, "shorter", Content(fields={"business_need_details": "old long"}), llm)
    assert fields["business_need_details"] == "Long version." and "old long" in llm.prompts[0]


def test_table_rows_parses_tab_and_pipe_tables():
    from sdgen.brief import table_rows

    assert table_rows("Failure\tWhat happens\tAlert to\nLogin fails\tretry\tSupport\nHost key\tdrop\n\nprose after the table") == (["Failure", "What happens", "Alert to"], [["Login fails", "retry", "Support"], ["Host key", "drop", ""]])
    assert table_rows("| Flow | Pattern |\n|---|---|\n| Lockbox | BOA_*.txt |") == (["Flow", "Pattern"], [["Lockbox", "BOA_*.txt"]])
    assert table_rows("decision one | owner | decided\ndecision two | owner | open") is None and table_rows("just prose") is None


def test_pasted_tables_and_structured_facts_fill_developer_tables_mechanically():
    from sdgen.blueprint import Blueprint, Section
    from sdgen.mechanical import mechanical_fills

    manifest = Manifest(
        name="m",
        fields=[
            FieldSpec(key="extra_operations", label="Operations", kind="table", columns=["Failure", "What happens", "Alert to"], bindings=[Binding(slide=2, shape=ShapeRef(id=4))]),
            FieldSpec(key="extra_interface_inventory", label="Interface inventory", kind="table", columns=["#", "Party", "Flow", "Direction", "Source", "Target", "Encryption", "Cut-off"], bindings=[Binding(slide=2, shape=ShapeRef(id=4))]),
            FieldSpec(key="extra_connectivity", label="Connectivity", kind="table", columns=["Environment", "Endpoint", "Host", "Port", "Account", "Key or cert", "Network path"], bindings=[Binding(slide=2, shape=ShapeRef(id=4))]),
        ],
    )
    blueprint = Blueprint(name="m", sections=[Section(key=k, title=k, kind="table", slide=2, fields=[k]) for k in ("extra_operations", "extra_interface_inventory", "extra_connectivity")])
    brief = BRIEF.model_copy(update={"operations": "Failure\tWhat happens\tAlert to\nLogin fails\tretry\tSupport", "facts": {"interfaces": "BoA | Lockbox | inbound | reception/Inbound/BOA | BOA/IN | PGP | 09:00\n- JPMC | ACH | outbound | JPMC_1000/OUT | emission/Outbound/JPMC | none"}})
    result = mechanical_fills(brief, blueprint, manifest)
    assert result.fields["extra_operations"] == [{"Failure": "Login fails", "What happens": "retry", "Alert to": "Support"}]
    rows = result.fields["extra_interface_inventory"]
    assert rows[0] == {"#": "1", "Party": "BoA", "Flow": "Lockbox", "Direction": "inbound", "Source": "reception/Inbound/BOA", "Target": "BOA/IN", "Encryption": "PGP", "Cut-off": "09:00"}
    assert rows[1]["#"] == "2" and rows[1]["Party"] == "JPMC" and rows[1]["Cut-off"] == ""
    assert "extra_connectivity" not in result.fields and any("from the table in the brief" in n for n in result.notes)


def test_developer_slides_get_their_own_writer_call(sample_deck, tmp_path):
    from sdgen.plan import ExtraSection
    from sdgen.writer import group_sections

    entry = _template(sample_deck, tmp_path)
    extras = [ExtraSection(key="extra_build_checklist", title="Build checklist", kind="table", columns=["#", "Step", "Depends on", "Owner"], prototype=entry.blueprint.sections[0].key)]
    result = draft_content(BRIEF, entry.blueprint, entry.manifest, MockLLM(), extras=extras)
    assert set(result.content.fields["extra_build_checklist"][0]) == {"#", "Step", "Depends on", "Owner"}
    from sdgen.plan import extended_blueprint, extended_manifest

    sections = writable_sections(extended_blueprint(entry.blueprint, extras), extended_manifest(entry.manifest, entry.blueprint, extras))
    groups = dict(group_sections(sections))
    assert [s["section"] for s in groups["developer"]] == ["extra_build_checklist"]
    system, user = build_prompt(BRIEF, entry.blueprint, entry.manifest, sections=groups["developer"], group="developer")
    assert "[TBC] keeps the row" in user and "dependency order" in user


def test_label_prefixes_are_stripped_in_every_punctuation_form():
    from sdgen.writer import strip_label_prefixes

    manifest = Manifest(
        name="m",
        fields=[
            FieldSpec(key="business_need", label="Business Need", bindings=[Binding(slide=5, shape=ShapeRef(id=5), keep_prefix="Business Need:")]),
            FieldSpec(key="scope", label="Scope"),
        ],
    )
    content = Content(fields={"business_need": "Business Need. OneERP must move files.", "scope": "**Scope:** Lockbox only.\nScope is limited."})
    strip_label_prefixes(content, manifest)
    assert content.fields == {"business_need": "OneERP must move files.", "scope": "Lockbox only.\nScope is limited."}
    content = Content(fields={"scope": "Scope is limited to lockbox."})
    strip_label_prefixes(content, manifest)
    assert content.fields["scope"] == "Scope is limited to lockbox."


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
    assert llm.name == "azure" and llm.label == "azure:gpt-test" and llm.complete("sys", "usr") == "## a\nhello\n"
    call = completions.calls[0]
    assert call["model"] == "gpt-test"
    assert call["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert call["max_completion_tokens"] == MAX_OUTPUT_TOKENS and "reasoning_effort" not in call


def test_complete_json_falls_back_from_schema_to_json_mode():
    class BadRequestError(Exception):
        pass

    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        kind = kwargs.get("response_format", {}).get("type")
        if kind == "json_schema":
            raise BadRequestError("response_format not supported")
        text = '{"version": "1.0"}' if kind == "json_object" else '```json\n{"version": "1.0"}\n```'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    llm = OpenAILLM(client, "gpt-5", name="azure")
    assert llm.complete_json("s", "u", {"type": "object"}, name="facts") == {"version": "1.0"}
    assert [c.get("response_format", {}).get("type") for c in calls] == ["json_schema", "json_object"]
    assert calls[0]["reasoning_effort"] == "low" and calls[0]["max_completion_tokens"] == 32000
    assert "matches this schema" in calls[1]["messages"][0]["content"]
    assert llm.with_effort("medium").effort == "medium" and llm.with_effort("medium").model == "gpt-5"
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json("no json here")
    assert MockLLM().complete_json("s", "u", {}, name="facts") == {}


def test_openai_adapter_sends_image_parts_and_drops_them_only_for_json():
    client, completions = _fake_client("transcript")
    llm = OpenAILLM(client, "gpt-4.1", name="azure")
    assert llm.complete("sys", "usr", images=[(b"\x89PNG", "image/png")]) == "transcript"
    content = completions.calls[0]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "usr"}
    assert content[1]["type"] == "image_url" and content[1]["image_url"]["url"].startswith("data:image/png;base64,iVBORw") and content[1]["image_url"]["detail"] == "high"

    class BadRequestError(Exception):
        pass

    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if isinstance(kwargs["messages"][1]["content"], list):
            raise BadRequestError("images not supported")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": 1}'), finish_reason="stop")])

    text_only = OpenAILLM(SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), "o3-mini", name="azure")
    assert text_only.complete_json("s", "u", {"type": "object"}, images=[(b"x", "image/png")]) == {"ok": 1}
    assert [c["response_format"]["type"] for c in calls] == ["json_schema", "json_schema"] and isinstance(calls[1]["messages"][1]["content"], str)
    with pytest.raises(LLMError, match="does not accept images"):
        text_only.complete("s", "u", images=[(b"x", "image/png")])
    assert MockLLM().complete_json("s", "u", {}, name="facts", images=[(b"x", "image/png")]) == {}


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
    assert result.llm == "azure:m" and result.warnings == [] and result.calls == 1
    assert result.content.globals["subject"] == BRIEF.subject
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
    assert "[TBC: what to ask]" in system and "skeleton" in system and "50 and 85 percent" in system
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
    assert "# Reference material" not in user


def test_prompt_carries_the_reference_material(sample_deck, tmp_path):
    from sdgen.material import new_material

    entry = _template(sample_deck, tmp_path)
    told = BRIEF.model_copy(update={"material": [new_material("text", title="Bank list", text="BoA, JPMC, PNC serve company codes 1000 1002 2000"), new_material("image", title="Other slide", tags=["nowhere"], text="secret")]})
    system, user = build_prompt(told, entry.blueprint, entry.manifest)
    assert "customer's own source material" in system and "the reference material attached to it" in system
    block = user.split("# Reference material", 1)[1].split("# Brief", 1)[0]
    assert "## Bank list (text)" in block and "BoA, JPMC" in block and "Other slide" not in user


def test_number_warnings_accept_numbers_from_material():
    from sdgen.material import new_material
    from sdgen.writer import number_warnings

    manifest = Manifest(name="m", fields=[FieldSpec(key="need", label="Need", bindings=[Binding(slide=1, shape=ShapeRef(id=1))])])
    content = Content(fields={"need": "Files arrive from 3 banks for company codes 1000 and 2000."})
    assert number_warnings(content, BRIEF, manifest)
    told = BRIEF.model_copy(update={"material": [new_material("text", text="Company codes 1000, 1002, 2000 are illustrative.")]})
    assert number_warnings(content, told, manifest) == []


def test_extract_facts_reads_material(sample_deck, tmp_path):
    from sdgen.brief import fact_questions
    from sdgen.material import new_material

    entry = _template(sample_deck, tmp_path)
    seen = {}

    class JsonLLM:
        name = "j"

        def complete(self, system, user):
            return ""

        def complete_json(self, system, user, schema, name="result"):
            seen["system"], seen["user"] = system, user
            return {}

    told = BRIEF.model_copy(update={"material": [new_material("link", title="Country list", url="https://x", text="Operating countries: ZA, KE")]})
    extract_facts(told, fact_questions(entry.blueprint, entry.manifest), JsonLLM())
    assert "reference material" in seen["system"] and "# Reference material" in seen["user"] and "ZA, KE" in seen["user"]
    assert seen["user"].index("# Reference material") < seen["user"].index("# Brief")


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


def test_cli_draft_loads_material_next_to_the_brief(sample_deck, tmp_path, monkeypatch):
    import sdgen.cli as cli
    from sdgen.material import new_material, save_material
    from sdgen.writer import DraftResult

    _template(sample_deck, tmp_path)
    brief_path = tmp_path / "brief.md"
    brief_path.write_text(dump_brief(BRIEF), encoding="utf-8")
    save_material([new_material("text", title="Pack", text="Slide 1 big picture")], tmp_path / "material.yaml")
    seen = {}

    def fake_draft(brief, blueprint, manifest, llm, original=None):
        seen["material"] = [m.title for m in brief.material]
        return DraftResult(markdown="## x\n", content=Content(), llm="mock")

    monkeypatch.setattr(cli, "draft_content", fake_draft)
    result = CliRunner().invoke(main, ["draft", "demo", str(brief_path), "-o", str(tmp_path / "content.md"), "--templates", str(tmp_path / "templates")])
    assert result.exit_code == 0, result.output
    assert seen["material"] == ["Pack"]


class _ScriptedLLM:
    name = "fake"
    label = "fake:x"

    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.images = []

    def complete(self, system, user, images=None):
        self.prompts.append(user)
        self.images.append(images)
        return self.replies.pop(0)

    def complete_json(self, system, user, schema, name="result"):
        return {}


def test_draft_repairs_missing_fields_and_flags_numbers(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    first = "---\nsubject: X\n---\n## business_need\nBanks deliver statements daily and 80% arrive before noon.\n## scope\n| Function | Countries |\n|---|---|\n| Finance | ZA |\n"
    second = "## first_point\n- one\n"
    llm = _ScriptedLLM([first, second])
    result = draft_content(BRIEF, entry.blueprint, entry.manifest, llm)
    assert result.calls == 2 and result.llm == "fake:x"
    assert result.content.fields["first_point"] == "- one"
    assert "left these fields empty" in llm.prompts[1] and "## first_point" in llm.prompts[1] and "## business_need" not in llm.prompts[1]
    assert any("80%" in w and "business_need" in w for w in result.warnings)
    assert "# Facts" in llm.prompts[0] and "# How to use the brief" in llm.prompts[0] and "target 2450" not in llm.prompts[0]
    assert "write 50 to 85 percent" in llm.prompts[0]


def test_example_outline_keeps_structure():
    text = "Business Need: Problem: something long. Expected Outcomes: more text.\nStatus\nScope: Function | Countries\nFinance | ZA"
    outline = example_outline(text)
    assert outline.startswith("Structure: Business Need; Status; Scope") and 'Excerpt: """' in outline


def test_mechanical_fills_from_facts_and_template():
    from sdgen.mechanical import mechanical_fills

    manifest = Manifest(
        name="m",
        fields=[
            FieldSpec(key="refs", label="Referenced Content", kind="table", columns=["Content Description", "URL or Source", "Purpose"], bindings=[Binding(slide=3, shape=ShapeRef(id=1))]),
            FieldSpec(key="versions", label="Document Version Control", kind="table", columns=["Version", "Date", "Author", "Contributors", "Change description"], bindings=[Binding(slide=2, shape=ShapeRef(id=1))]),
            FieldSpec(key="effort", label="Effort Estimation", kind="table", columns=["Gap Reference", "Deliverable", "Role", "Month-1", "Month-2", "Month-n", "Total Effort (hours)"], bindings=[Binding(slide=4, shape=ShapeRef(id=1), keep_last_row_if="Total")]),
            FieldSpec(key="link", label="internal hyperlink to solution design", bindings=[Binding(slide=5, shape=ShapeRef(id=1), mode="token", token="<link>")]),
            FieldSpec(key="subtitle", label="SubTitle", kind="bullets", bindings=[Binding(slide=1, shape=ShapeRef(id=1))]),
            FieldSpec(key="principles", label="Principles", kind="table", columns=["A"], bindings=[Binding(slide=6, shape=ShapeRef(id=1))]),
            FieldSpec(key="need", label="Business Need", bindings=[Binding(slide=5, shape=ShapeRef(id=2))]),
        ],
    )
    blueprint = Blueprint(
        name="m",
        sections=[
            Section(key="cover", title="Cover", kind="cover", slide=1, fields=["subtitle"]),
            Section(key="versions", title="Document Version Control", kind="table", slide=2, fields=["versions"]),
            Section(key="refs", title="Referenced Content", kind="references", slide=3, fields=["refs"]),
            Section(key="effort", title="Effort Estimation", kind="table", slide=4, fields=["effort"]),
            Section(key="overview", title="Executive Overview", kind="composite", slide=5, fields=["link", "need"]),
            Section(key="principles", title="Guiding Principles", kind="static", slide=6, fields=["principles"]),
        ],
    )
    facts = {
        "version": "1.0",
        "author": "Ana",
        "contributors": "Ben; Cy",
        "effort": "Architect | Design | 20 | 10 | 5\nDeveloper | Build | | 40 | 20",
        "design_doc_url": "https://x/y",
        "design_start": "1 May 2026",
        "design_end": "30 June 2026",
    }
    brief = BRIEF.model_copy(update={"facts": facts})
    result = mechanical_fills(brief, blueprint, manifest, Content(fields={"principles": [{"A": "Fit to standard"}]}))
    assert [r["Content Description"] for r in result.fields["refs"]] == ["Bank Statement API", "CAMT.053 guide"]
    assert result.fields["refs"][0]["URL or Source"] == "https://api.sap.com/api/bankstatement"
    row = result.fields["versions"][0]
    assert row["Version"] == "1.0" and row["Author"] == "Ana" and row["Contributors"] == "Ben; Cy" and row["Change description"] == "Initial draft"
    effort = result.fields["effort"]
    assert effort[0]["Gap Reference"] == "1" and effort[0]["Role"] == "Architect" and effort[0]["Total Effort (hours)"] == "35"
    assert effort[1]["Month-1"] == "" and effort[1]["Month-2"] == "40" and effort[1]["Month-n"] == "20" and effort[1]["Total Effort (hours)"] == "60"
    assert result.fields["link"] == "https://x/y"
    assert result.fields["subtitle"] == "Bank Statement (CAMT.053) Integration\nDate: 1 May 2026 – 30 June 2026\nVersion: 1.0"
    assert result.fields["principles"] == [{"A": "Fit to standard"}]
    assert "need" not in result.fields
    assert any(n.startswith("Referenced Content: from APIs") for n in result.notes)

    bare = mechanical_fills(BRIEF, blueprint, manifest, None)
    assert bare.fields["versions"][0]["Author"] == "[TBC]" and "effort" not in bare.fields
    assert bare.fields["subtitle"] == BRIEF.subject

    from sdgen.writer import number_warnings

    flagged = number_warnings(Content(fields={"effort": [{"Role": "Dev", "Month-1": "40"}], "need": "CAMT.053 files, 3 banks."}), BRIEF, manifest)
    assert len(flagged) == 1 and "'effort'" in flagged[0] and "40" in flagged[0]


def test_extract_facts_keeps_only_known_non_empty_keys(sample_deck, tmp_path):
    from sdgen.brief import fact_questions

    entry = _template(sample_deck, tmp_path)
    questions = fact_questions(entry.blueprint, entry.manifest)

    class JsonLLM:
        name = "j"

        def complete(self, system, user):
            return ""

        def complete_json(self, system, user, schema, name="result"):
            assert name == "facts" and "countries" in schema["properties"] and "Banks deliver" in user
            return {"countries": "ZA", "unknown": "x", "volumes": ""}

    assert extract_facts(BRIEF, questions, JsonLLM()) == {"countries": "ZA"}
    assert extract_facts(BRIEF, questions, MockLLM()) == {}


def test_redraft_section_returns_only_that_section(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    llm = _ScriptedLLM(["## business_need\nShorter need.\n## scope\n| Function | Countries |\n|---|---|\n| Finance | ZA |\n## first_point\n- x\n## other\ntext\n"])
    current = Content(fields={"business_need": "Old text"})
    fields = redraft_section(BRIEF, entry.blueprint, entry.manifest, entry.blueprint.sections[0].key, "make it shorter", current, llm)
    assert set(fields) == {"business_need", "scope", "first_point"} and fields["business_need"] == "Shorter need."
    assert "make it shorter" in llm.prompts[0] and "Old text" in llm.prompts[0]


def test_draft_content_writes_extra_sections(sample_deck, tmp_path):
    from sdgen.plan import ExtraSection

    entry = _template(sample_deck, tmp_path)
    first = entry.blueprint.sections[0]
    extras = [ExtraSection(key="extra_acceptance", title="Acceptance Criteria", kind="table", columns=["Ref", "Scenario"], prototype=first.key)]
    result = draft_content(BRIEF, entry.blueprint, entry.manifest, MockLLM(), extras=extras)
    rows = result.content.fields["extra_acceptance"]
    assert isinstance(rows, list) and rows and set(rows[0]) == {"Ref", "Scenario"}
    assert "## extra_acceptance" in result.markdown


def test_duplicate_sentences_across_fields_are_reported():
    from sdgen.content import Content
    from sdgen.writer import duplicate_fields, duplicate_warnings

    shared = "The lockbox file arrives daily from three banks and is posted automatically in the morning run."
    content = Content(fields={"a": shared + " More here.", "b": "- " + shared, "c": [{"Ref": "1", "Text": shared}], "d": "Short one."})
    assert duplicate_fields(content) == {"b": "a", "c": "a"}
    assert duplicate_warnings(content) == ["field 'b' repeats a sentence of 'a'", "field 'c' repeats a sentence of 'a'"]


def test_skeleton_states_words_and_the_budget_band():
    from sdgen.brief import Brief
    from sdgen.writer import answer_skeleton

    section = {"title": "S", "fields": [{"key": "k", "label": "L", "kind": "text", "token": False, "max_chars": 300, "guidance": "", "columns": []}]}
    assert "target 300 characters (about 50 words; write 50 to 85 percent of it)" in answer_skeleton(Brief(subject="X"), [section])



def test_line_budget_and_prompt_rules():
    from sdgen.writer import SYSTEM_PROMPT, answer_skeleton

    section = {"title": "Overview", "fields": [{"key": "need", "label": "Need", "kind": "bullets", "columns": [], "max_chars": 300, "max_lines": 4, "token": False, "guidance": ""}]}
    assert "target 300 characters on about 4 lines (about 50 words; write 50 to 85 percent of it; a bullet takes at least one line)" in answer_skeleton(Brief(subject="X"), [section])
    table = {"title": "Scope", "fields": [{"key": "scope", "label": "Scope", "kind": "table", "columns": ["A", "B"], "max_chars": None, "max_lines": None, "max_rows": 3, "token": False, "guidance": ""}]}
    assert "about 3 rows fit the slide; further rows continue on a copy of it" in answer_skeleton(Brief(subject="X"), [table])
    assert "never more bullets than the field has lines" in SYSTEM_PROMPT and "never\nreuse its system names" in SYSTEM_PROMPT


def test_kept_sections_still_write_their_tokens(sample_deck):
    from sdgen.analyze import analyze_deck
    from sdgen.blueprint import derive_blueprint
    from sdgen.inventory import inspect_deck
    from sdgen.writer import writable_sections

    deck = inspect_deck(sample_deck)
    analysis = analyze_deck(deck)
    manifest = analysis.to_manifest("demo")
    blueprint = derive_blueprint(deck, analysis, manifest, "demo")
    section = next(s for s in blueprint.sections if "business_need" in s.fields)
    manifest.field("business_need").bindings[0].mode = "token"
    kept = writable_sections(blueprint, manifest, token_only={section.key})
    assert [f["key"] for s in kept if s["section"] == section.key for f in s["fields"]] == ["business_need"]
    assert not any(s["section"] == section.key for s in writable_sections(blueprint, manifest, skip_sections={section.key}))


def test_number_warnings_skip_numbering_columns():
    from sdgen.writer import number_warnings

    spec = FieldSpec(key="extra_build_checklist", label="Build checklist", kind="table", columns=["#", "Step", "Depends on", "Owner"], bindings=[Binding(slide=1, shape=ShapeRef(id=1))])
    rows = [{"#": "12", "Step": "Build the iFlow", "Depends on": "7,8,9", "Owner": "Dev"}, {"#": "13", "Step": "Test with 37 files", "Depends on": "12", "Owner": "QA"}]
    warnings = number_warnings(Content(fields={"extra_build_checklist": rows}), BRIEF, Manifest(name="m", fields=[spec]))
    assert warnings == ["field 'extra_build_checklist' (Build checklist) uses numbers not found in the brief: 37"]


def test_reference_tables_need_a_url_or_a_source_beside_a_description():
    from sdgen.mechanical import is_reference_columns

    assert is_reference_columns(["Content Description", "URL or Source", "Purpose"]) and is_reference_columns(["Item", "Link"])
    assert not is_reference_columns(["#", "Party", "Flow", "Direction", "Source", "Target", "Encryption", "Cut-off"])
    assert not is_reference_columns(["Endpoint", "Folder or resource", "Account", "Read", "Write", "Move or delete"])
