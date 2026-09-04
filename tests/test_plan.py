from sdgen.analyze import analyze_deck
from sdgen.blueprint import derive_blueprint
from sdgen.brief import Brief
from sdgen.design import Design, DesignStore
from sdgen.inventory import inspect_deck
from sdgen.llm import MockLLM
from sdgen.manifest import GlobalSpec
from sdgen.plan import SectionPlan, apply_plan, default_plan, plan_sections
from sdgen.registry import Registry

BRIEF = Brief(subject="Lockbox", about="Banks send lockbox files.", approach="SWIFT to SFTP to BTP to S/4HANA.")


def _template(sample_deck, tmp_path):
    deck = inspect_deck(sample_deck)
    analysis = analyze_deck(deck)
    manifest = analysis.to_manifest("demo")
    manifest.globals.append(GlobalSpec(key="subject", replaces="Demo Integration"))
    blueprint = derive_blueprint(deck, analysis, manifest, "demo")
    return Registry(tmp_path / "templates").add("demo", sample_deck, manifest, blueprint=blueprint)


class _JsonLLM:
    name = "fake"
    label = "fake:plan"

    def __init__(self, data):
        self.data = data
        self.prompts = []

    def complete(self, system, user):
        return ""

    def complete_json(self, system, user, schema, name="result"):
        self.prompts.append((system, user, name))
        return self.data


def test_default_plan_follows_section_kinds(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    plan = default_plan(entry.blueprint)
    first, second = entry.blueprint.sections
    assert plan.decision(first.key).source == "draft" and plan.decision(second.key).source == "keep"
    assert all(d.use for d in plan.decisions) and plan.flows == [] and plan.extras == []


def test_plan_sections_merges_the_model_answer_and_applies(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    first, second = entry.blueprint.sections
    llm = _JsonLLM(
        {
            "decisions": [
                {"key": first.key, "use": True, "title": "Overview for Lockbox", "source": "draft", "reason": "fits"},
                {"key": second.key, "use": False, "source": "keep", "reason": "carrier only"},
                {"key": "nonsense", "use": False, "source": "draft"},
            ],
            "extras": [{"key": "acceptance", "title": "Acceptance Criteria", "kind": "table", "columns": [], "before": "nowhere"}],
            "flows": [{"section": first.key, "title": "x", "purpose": "y"}],
        }
    )
    plan = plan_sections(BRIEF, entry.blueprint, entry.manifest, llm)
    system, user, name = llm.prompts[0]
    assert name == "section_plan" and "# Template outline" in user and first.key in user and "Banks send lockbox files" in user
    assert plan.model == "fake:plan"
    assert plan.decision(first.key).title == "Overview for Lockbox" and plan.decision(second.key).use is False
    assert [d.key for d in plan.decisions] == [first.key, second.key]
    extra = plan.extras[0]
    assert extra.key == "acceptance" and extra.kind == "table" and extra.columns == ["Ref", "Scenario", "Expected result", "Evidence"]
    assert extra.prototype == first.key and extra.before == ""
    assert plan.flows == []  # the first section is not a diagram

    design = Design(name="d", template="demo")
    apply_plan(plan, design, entry.blueprint)
    assert design.hidden == [second.key] and design.titles == {first.key: "Overview for Lockbox"} and design.modes == {}
    assert design.plan is plan

    store = DesignStore(tmp_path / "designs")
    store.save(design)
    loaded = store.load("d")
    assert loaded.plan == plan and loaded.titles == design.titles and (tmp_path / "designs" / "d" / "plan.yaml").is_file()


def test_mock_plan_is_the_default(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    plan = plan_sections(BRIEF, entry.blueprint, entry.manifest, MockLLM())
    assert plan.model == "mock" and [d.key for d in plan.decisions] == [s.key for s in entry.blueprint.sections]
    assert SectionPlan.model_validate(plan.model_dump()) == plan
