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


def test_detail_prototypes_pick_plain_slides_of_the_same_kind(sample_deck, tmp_path):
    from sdgen.blueprint import Blueprint, Section, composite_fields
    from sdgen.manifest import Binding, FieldSpec, Manifest, ShapeRef
    from sdgen.plan import detail_prototypes

    entry = _template(sample_deck, tmp_path)
    assert set(composite_fields(entry.blueprint, entry.manifest)) == {"business_need", "scope", "first_point"}
    assert detail_prototypes(entry.blueprint, entry.manifest) == {}  # the sample deck has no plain slide to clone

    manifest = Manifest(
        name="m",
        fields=[
            FieldSpec(key="a", label="Need", bindings=[Binding(slide=1, shape=ShapeRef(id=1), keep_prefix="Need:")]),
            FieldSpec(key="t", label="Scope", kind="table", columns=["Function", "Bank"], bindings=[Binding(slide=1, shape=ShapeRef(id=2))]),
            FieldSpec(key="n", label="Notes", kind="bullets", bindings=[Binding(slide=2, shape=ShapeRef(id=3), max_chars=900)]),
            FieldSpec(key="g", label="Grid", kind="table", columns=["A", "B", "C"], bindings=[Binding(slide=3, shape=ShapeRef(id=4), keep_last_row_if="Total")]),
        ],
    )
    blueprint = Blueprint(
        name="m",
        sections=[
            Section(key="over", title="Overview", kind="composite", slide=1, fields=["a", "t"]),
            Section(key="notes", title="Notes", kind="text", slide=2, fields=["n"]),
            Section(key="grid", title="Grid", kind="table", slide=3, fields=["g"]),
        ],
    )
    found = detail_prototypes(blueprint, manifest)
    assert set(found) == {"a", "t"}
    assert found["a"].key == "a_details" and found["a"].kind == "bullets" and found["a"].bindings[0].slide == 2 and found["a"].bindings[0].keep_prefix is None
    assert found["t"].kind == "table" and found["t"].columns == ["Function", "Bank"] and found["t"].bindings[0].slide == 3 and found["t"].bindings[0].keep_last_row_if is None


def _developer_template():
    from sdgen.blueprint import Blueprint, Section
    from sdgen.manifest import Binding, FieldSpec, Manifest, ShapeRef

    manifest = Manifest(
        name="m",
        fields=[
            FieldSpec(key="a", label="Need", bindings=[Binding(slide=1, shape=ShapeRef(id=1))]),
            FieldSpec(key="g", label="Grid", kind="table", columns=["Ref", "Item", "Owner", "Status"], bindings=[Binding(slide=2, shape=ShapeRef(id=4))]),
            FieldSpec(key="e", label="Effort", kind="table", columns=["Role", "Month-1"], bindings=[Binding(slide=3, shape=ShapeRef(id=5))]),
        ],
    )
    blueprint = Blueprint(
        name="m",
        sections=[
            Section(key="over", title="Overview", kind="text", slide=1, fields=["a"]),
            Section(key="grid", title="Grid", kind="table", slide=2, fields=["g"]),
            Section(key="effort", title="Effort Estimation", kind="table", slide=3, fields=["e"]),
        ],
    )
    return blueprint, manifest


def test_developer_extras_are_always_proposed_and_deduplicated(sample_deck, tmp_path):
    from sdgen.plan import DEVELOPER_EXTRAS, developer_extras, merge_plan

    entry = _template(sample_deck, tmp_path)
    assert developer_extras(entry.blueprint, entry.manifest) == []  # the sample deck has no plain table slide to clone
    blueprint, manifest = _developer_template()
    base = default_plan(blueprint, manifest=manifest)
    assert [e.key for e in base.extras] == [key for key, _, _, _ in DEVELOPER_EXTRAS]
    assert all(e.include and e.kind == "table" and e.prototype == "grid" and e.before == "effort" for e in base.extras)
    data = {"extras": [{"key": "acceptance", "title": "Acceptance Criteria", "kind": "table"}, {"key": "build_checklist", "title": "Build checklist", "kind": "table"}, {"key": "x", "title": "RACI", "kind": "table"}]}
    plan = merge_plan(base, data, blueprint, set(), manifest=manifest)
    assert [e.key for e in plan.extras][:2] == ["extra_acceptance", "extra_interface_inventory"] and len(plan.extras) == 1 + len(DEVELOPER_EXTRAS)
    assert plan.extras[-3].columns == ["#", "Step", "Depends on", "Owner"]


def test_plan_flows_carry_a_reference_picture_id():
    from sdgen.blueprint import Blueprint, Section
    from sdgen.manifest import Binding, FieldSpec, Manifest, ShapeRef
    from sdgen.material import new_material

    manifest = Manifest(name="m", fields=[FieldSpec(key="img", label="Diagram", kind="image", bindings=[Binding(slide=4, shape=ShapeRef(id=9))])])
    blueprint = Blueprint(name="m", sections=[Section(key="flow", title="Level 2 flows", kind="diagram", slide=4, fields=["img"])])
    brief = Brief(subject="L", material=[new_material("image", title="Big picture", file="a.png", text="VM to BTP to S/4", id="abc"), new_material("text", title="Note", text="t", id="def")])
    llm = _JsonLLM({"decisions": [], "flows": [{"section": "flow", "title": "Flows", "purpose": "p", "material_id": "abc"}]})
    plan = plan_sections(brief, blueprint, manifest, llm)
    system, user, name = llm.prompts[0]
    assert "# Reference pictures" in user and "- abc | Big picture | VM to BTP to S/4" in user and "def |" not in user.split("# Reference pictures", 1)[1].split("# Brief", 1)[0]
    assert plan.flows[0].material_id == "abc" and "material_id" in system
    unknown = _JsonLLM({"decisions": [], "flows": [{"section": "flow", "title": "Flows", "purpose": "p", "material_id": "zzz"}]})
    assert plan_sections(brief, blueprint, manifest, unknown).flows[0].material_id == ""
    assert plan_sections(Brief(subject="L"), blueprint, manifest, _JsonLLM({"decisions": []})).flows[0].material_id == ""


def test_operations_table_in_the_brief_becomes_a_table_extra():
    from sdgen.plan import merge_plan

    blueprint, manifest = _developer_template()
    brief = Brief(subject="L", operations="Failure\tWhat happens\tAlert to\nLogin fails\tretry 3 times\tSupport\n\nMonitoring\nOne message per file.")
    data = {"extras": [{"key": "operations", "title": "Error handling and operations", "kind": "text"}]}
    plan = merge_plan(default_plan(blueprint, manifest=manifest), data, blueprint, set(), manifest=manifest, brief=brief)
    extra = plan.extras[0]
    assert extra.key == "extra_operations" and extra.kind == "table" and extra.columns == ["Failure", "What happens", "Alert to"] and extra.prototype == "grid"
    prose = merge_plan(default_plan(blueprint, manifest=manifest), data, blueprint, set(), manifest=manifest, brief=Brief(subject="L", operations="Retry three times, then alert."))
    assert prose.extras[0].kind == "text"


def test_plan_titles_are_capped_at_a_word_boundary(sample_deck, tmp_path):
    from sdgen.plan import merge_plan

    entry = _template(sample_deck, tmp_path)
    first = entry.blueprint.sections[0]
    long_title = "Inbound lockbox, statement and payment status file transport for every bank in scope"
    plan = merge_plan(default_plan(entry.blueprint), {"decisions": [{"key": first.key, "use": True, "title": long_title, "source": "draft"}]}, entry.blueprint, set())
    title = plan.decision(first.key).title
    assert len(title) <= 60 and long_title.startswith(title) and not title.endswith(" ") and title.endswith("transport")


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


def test_plan_prompt_includes_material(sample_deck, tmp_path):
    from sdgen.material import new_material

    entry = _template(sample_deck, tmp_path)
    llm = _JsonLLM({"decisions": []})
    told = BRIEF.model_copy(update={"material": [new_material("text", title="Pack", text="Slide 1 big picture")]})
    plan_sections(told, entry.blueprint, entry.manifest, llm)
    user = llm.prompts[0][1]
    assert "# Reference material" in user and "Slide 1 big picture" in user and user.index("# Reference material") < user.index("# Brief")
    plain = _JsonLLM({"decisions": []})
    plan_sections(BRIEF, entry.blueprint, entry.manifest, plain)
    assert "# Reference material" not in plain.prompts[0][1]


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


def test_build_notes_extra_is_a_text_slide(sample_deck, tmp_path):
    entry = _template(sample_deck, tmp_path)
    first = entry.blueprint.sections[0]
    llm = _JsonLLM({"decisions": [], "extras": [{"key": "build_notes", "title": "Build Notes", "kind": "text", "columns": [], "before": ""}]})
    plan = plan_sections(BRIEF, entry.blueprint, entry.manifest, llm)
    extra = plan.extras[0]
    assert extra.key == "extra_build_notes" and extra.kind == "text" and extra.columns == [] and extra.prototype == first.key
    assert "build notes" in llm.prompts[0][0].lower()



def test_open_questions_extra_is_built_from_the_brief(sample_deck, tmp_path):
    from sdgen.blueprint import Blueprint, Section
    from sdgen.manifest import Binding, FieldSpec, Manifest, ShapeRef
    from sdgen.plan import OPEN_QUESTIONS_KEY, extended_blueprint, open_question_rows, open_questions_extras, open_questions_text

    text = "Which bank sends BAI2? | Treasury\n- Is PGP needed for lockbox?\n\n"
    assert open_question_rows(text, ["Ref", "Question", "Ask"]) == [{"Ref": "1", "Question": "Which bank sends BAI2?", "Ask": "Treasury"}, {"Ref": "2", "Question": "Is PGP needed for lockbox?", "Ask": ""}]
    assert open_questions_text(text) == "1. Which bank sends BAI2? (ask: Treasury)\n2. Is PGP needed for lockbox?"
    joined = "Which bank sends BAI2? | Treasury Is PGP needed? | Security"
    assert [r["Ref"] for r in open_question_rows(joined, ["Ref", "Question", "Ask"])] == ["1", "2"]

    entry = _template(sample_deck, tmp_path)
    design = Design(name="d", template="demo", brief=Brief(subject="L", open_questions=text))
    extras = open_questions_extras(design, entry.blueprint, entry.manifest)
    assert [e.key for e in extras] == [OPEN_QUESTIONS_KEY] and extras[0].kind == "text" and extras[0].generated and extras[0].title == "Open Questions"
    extended = extended_blueprint(entry.blueprint, extras)
    assert extended.section(OPEN_QUESTIONS_KEY).generated and open_questions_extras(design, extended, entry.manifest) == extras
    assert open_questions_extras(Design(name="d", template="demo"), entry.blueprint, entry.manifest) == []
    design.hidden = [OPEN_QUESTIONS_KEY]
    assert open_questions_extras(design, entry.blueprint, entry.manifest) == []

    manifest = Manifest(
        name="m",
        fields=[
            FieldSpec(key="criteria", label="Success Criteria", kind="table", columns=["Ref", "Success Criteria", "Adoption Measure", "Notes"], bindings=[Binding(slide=9, shape=ShapeRef(id=1))]),
            FieldSpec(key="effort", label="Effort", kind="table", columns=["Ref", "Role", "M1", "Total"], bindings=[Binding(slide=26, shape=ShapeRef(id=1))]),
        ],
    )
    blueprint = Blueprint(
        name="m",
        sections=[
            Section(key="criteria", title="Success Criteria", kind="table", slide=9, fields=["criteria"]),
            Section(key="effort", title="Effort Estimation", kind="table", slide=26, fields=["effort"]),
        ],
    )
    wide = Design(name="w", template="m", brief=Brief(subject="L", open_questions=text))
    extra = open_questions_extras(wide, blueprint, manifest)[0]
    assert extra.kind == "table" and extra.columns == ["Ref", "Question", "Ask", "Status"] and extra.prototype == "criteria" and extra.before == "effort"
    owned = blueprint.model_copy(update={"sections": blueprint.sections + [Section(key="oq", title="Open Questions", kind="table", slide=30, fields=["criteria"])]})
    assert open_questions_extras(wide, owned, manifest) == []


def test_walkthrough_extras_follow_diagram_only_slides():
    from sdgen.blueprint import Blueprint, Section
    from sdgen.design import Design
    from sdgen.flow import FlowEdge, FlowNode, FlowSpec
    from sdgen.manifest import Binding, FieldSpec, Manifest, ShapeRef
    from sdgen.plan import extended_blueprint, walkthrough_extras

    manifest = Manifest(
        name="t",
        fields=[
            FieldSpec(key="flow_img", label="Flow", kind="image", bindings=[Binding(slide=2, shape=ShapeRef(id=5))]),
            FieldSpec(key="arch_img", label="Arch", kind="image", bindings=[Binding(slide=3, shape=ShapeRef(id=6))]),
            FieldSpec(key="arch_note", label="Note", kind="bullets", bindings=[Binding(slide=3, shape=ShapeRef(id=7), max_chars=500)]),
            FieldSpec(key="body", label="Body", kind="text", bindings=[Binding(slide=4, shape=ShapeRef(id=8), max_chars=900)]),
        ],
    )
    blueprint = Blueprint(
        name="t",
        sections=[
            Section(key="flow", title="Level 2 Flows", kind="diagram", slide=2, fields=["flow_img"]),
            Section(key="arch", title="Architecture", kind="diagram", slide=3, fields=["arch_img", "arch_note"]),
            Section(key="notes", title="Notes", kind="text", slide=4, fields=["body"]),
        ],
    )
    spec = FlowSpec(nodes=[FlowNode(id="a", label="A"), FlowNode(id="b", label="B")], edges=[FlowEdge(source="a", target="b", label="x")], steps=["A sends B."])
    flows = {"flow": spec, "arch": spec}
    design = Design(name="d", template="t", titles={"flow": "Inbound path"})
    extras = walkthrough_extras(design, blueprint, manifest, flows, ["flow", "arch", "notes"])
    assert [e.key for e in extras] == ["flow_walkthrough"]
    extra = extras[0]
    assert extra.title == "Inbound path: how it works" and extra.before == "arch" and extra.prototype == "notes" and extra.generated and extra.kind == "text"
    extended = extended_blueprint(blueprint, extras)
    assert [s.key for s in extended.sections] == ["flow", "flow_walkthrough", "arch", "notes"] and extended.section("flow_walkthrough").generated

    design.modes["arch"] = "blank"
    assert [(e.key, e.before) for e in walkthrough_extras(design, blueprint, manifest, flows, ["arch", "flow", "notes"])] == [("flow_walkthrough", "notes"), ("arch_walkthrough", "flow")]
    design.hidden = ["flow"]
    assert [e.key for e in walkthrough_extras(design, blueprint, manifest, flows)] == ["arch_walkthrough"]
