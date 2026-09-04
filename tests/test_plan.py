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
    assert extra.key == "extra_acceptance" and extra.kind == "table" and extra.columns == ["Ref", "Scenario", "Expected result", "Evidence"]
    assert extra.prototype == first.key and extra.before == ""
    assert plan.flows == []  # the first section is not a diagram

    from sdgen.plan import extended_blueprint, extended_manifest, extra_slides

    wide = extended_manifest(entry.manifest, entry.blueprint, plan.extras)
    spec = wide.field("extra_acceptance")
    assert spec.kind == "table" and spec.columns == extra.columns and spec.bindings[0].slide == 1
    assert spec.bindings[0].shape == entry.manifest.field("scope").bindings[0].shape
    outline = extended_blueprint(entry.blueprint, plan.extras)
    assert [s.key for s in outline.sections] == [first.key, second.key, "extra_acceptance"]
    assert outline.section("extra_acceptance").kind == "table" and outline.section("extra_acceptance").slide == 1
    slides = extra_slides(plan, wide, outline, {"extra_acceptance": [{"Ref": "1"}]})
    assert len(slides) == 1 and slides[0].value == [{"Ref": "1"}] and slides[0].before == 0
    assert extra_slides(plan, wide, outline, {}, hidden=["extra_acceptance"]) == []

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


def test_leftover_texts_and_clear_decisions(sample_deck, tmp_path):
    from sdgen.plan import leftover_texts

    entry = _template(sample_deck, tmp_path)
    leftovers = leftover_texts(entry.template_path, entry.manifest, entry.blueprint)
    texts = [item.text for item in leftovers]
    assert "Scope" in texts and "ISPIC" in texts and "BTP" in texts
    assert not any("Something long enough" in t for t in texts) and all(item.slide == 1 for item in leftovers)

    scope = next(item for item in leftovers if item.text == "Scope")
    llm = _JsonLLM({"decisions": [], "clear": [{"slide": 1, "shape": scope.shape, "reason": "earlier project"}, {"slide": 9, "shape": 99}, {"slide": "x", "shape": 1}]})
    plan = plan_sections(BRIEF, entry.blueprint, entry.manifest, llm, leftovers=leftovers)
    assert [(c.slide, c.shape, c.text, c.reason) for c in plan.clear] == [(1, scope.shape, "Scope", "earlier project")]
    assert f"- 1 | {scope.shape} | Scope" in llm.prompts[0][1]
    assert plan_sections(BRIEF, entry.blueprint, entry.manifest, MockLLM(), leftovers=leftovers).clear == []


def test_extras_avoid_the_version_control_slide_as_prototype(sample_deck, tmp_path):
    from sdgen.blueprint import Blueprint, Section
    from sdgen.manifest import Binding, FieldSpec, Manifest, ShapeRef
    from sdgen.plan import _prototype

    manifest = Manifest(
        name="m",
        fields=[
            FieldSpec(key="versions", label="Document Version Control", kind="table", columns=["Version", "Date", "Author", "Contributors", "Change"], bindings=[Binding(slide=2, shape=ShapeRef(id=1))]),
            FieldSpec(key="criteria", label="Success Criteria", kind="table", columns=["Ref", "Success Criteria", "Adoption Measure", "Notes"], bindings=[Binding(slide=9, shape=ShapeRef(id=1))]),
            FieldSpec(key="effort", label="Effort", kind="table", columns=["Ref", "Platform", "Deliverable", "Role", "M1", "M2", "Mn", "Total"], bindings=[Binding(slide=26, shape=ShapeRef(id=1))]),
        ],
    )
    blueprint = Blueprint(
        name="m",
        sections=[
            Section(key="versions", title="Document Version Control", kind="table", slide=2, fields=["versions"]),
            Section(key="criteria", title="Success Criteria", kind="table", slide=9, fields=["criteria"]),
            Section(key="effort", title="Effort Estimation", kind="table", slide=26, fields=["effort"]),
        ],
    )
    assert _prototype(blueprint, "table", ["Ref", "Scenario", "Expected result", "Evidence"], manifest) == "criteria"
    assert _prototype(blueprint, "table", ["Ref", "Item", "Owner", "Status", "Due", "Notes", "Risk", "Link"], manifest) == "effort"
