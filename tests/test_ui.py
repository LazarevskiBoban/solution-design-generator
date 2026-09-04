import importlib.util
from pathlib import Path

import pytest
from pptx import Presentation
from streamlit.testing.v1 import AppTest

from sdgen.analyze import analyze_deck
from sdgen.blueprint import derive_blueprint
from sdgen.design import DesignStore
from sdgen.inventory import inspect_deck
from sdgen.manifest import GlobalSpec
from sdgen.registry import Registry

APP = Path(__file__).resolve().parent.parent / "ui" / "app.py"


@pytest.fixture
def registry_with_demo(sample_deck, tmp_path, monkeypatch):
    deck = inspect_deck(sample_deck)
    analysis = analyze_deck(deck)
    manifest = analysis.to_manifest("demo")
    manifest.globals.append(GlobalSpec(key="subject", label="Subject", replaces="Demo Integration"))
    blueprint = derive_blueprint(deck, analysis, manifest, "demo")
    root = tmp_path / "templates"
    Registry(root).add("demo", sample_deck, manifest, blueprint=blueprint)
    monkeypatch.setenv("SDGEN_TEMPLATES", str(root))
    monkeypatch.setenv("SDGEN_DESIGNS", str(tmp_path / "designs"))
    monkeypatch.setenv("SDGEN_LLM", "mock")
    return root


def test_templates_page_without_upload_shows_guidance(tmp_path, monkeypatch):
    monkeypatch.setenv("SDGEN_TEMPLATES", str(tmp_path / "empty"))
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "Templates"
    assert any("Upload a deck" in i.value for i in app.info)


def test_design_page_drafts_and_generates(registry_with_demo, tmp_path):
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "New design"
    assert app.selectbox(key="llm_provider").value == "mock"
    assert app.selectbox(key="design_template").value == "demo"

    app.text_input(key="design_name:demo").input("CAMT 053").run()
    state_key = "design:demo:camt-053"
    assert state_key in app.session_state

    app.text_input(key=f"{state_key}:0:b:subject").input("Lockbox Integration")
    app.text_area(key=f"{state_key}:0:b:about").input("Bank statements arrive daily. They must be posted automatically.")
    app.run()
    app.button(key=f"{state_key}:draft").click().run()
    assert not app.exception
    design = app.session_state[state_key]
    assert "[Draft] Bank statements" in design.content_markdown
    assert any(s.value.startswith("Drafted 3 of 3 fields with mock") for s in app.success)
    assert (tmp_path / "designs" / "camt-053" / "content.md").is_file()

    app.button(key=f"{state_key}:generate").click().run()
    assert not app.exception
    assert any("Generated" in s.value for s in app.success)
    file_name, data, issues, slides = app.session_state[f"{state_key}:output"]
    assert file_name == "lockbox_integration.pptx" and slides == 2

    saved = tmp_path / "generated.pptx"
    saved.write_bytes(data)
    slide = Presentation(str(saved)).slides[0]
    assert slide.shapes.title.text == "Executive Overview: Lockbox Integration"
    need = next(s for s in slide.shapes if s.name == "Business Need Box").text_frame.text
    assert need.startswith("Business Need: [Draft] Bank statements arrive daily.")

    section_key = Registry(registry_with_demo).load("demo").blueprint.sections[0].key
    app.radio(key=f"{state_key}:1:mode:{section_key}").set_value("keep").run()
    app.button(key=f"{state_key}:generate").click().run()
    assert not app.exception
    assert app.session_state[state_key].modes == {section_key: "keep"}
    saved.write_bytes(app.session_state[f"{state_key}:output"][1])
    kept = Presentation(str(saved)).slides[0]
    need = next(s for s in kept.shapes if s.name == "Business Need Box").text_frame.text
    assert need.startswith("Business Need: Something long")

    # The confirmation opens a dialog, which the test harness cannot drive; the trigger and the action are checked apart.
    app.selectbox(key="design_choice:demo").select("camt-053").run()
    app.button(key="design:demo:camt-053:delete").click().run()
    assert not app.exception
    app.sidebar.radio[0].set_value("Templates").run()
    app.button(key="template_remove").click().run()
    assert not app.exception

    ui = _app_module()
    blueprint = Registry(registry_with_demo).load("demo").blueprint
    keys = [s.key for s in blueprint.sections]
    board_design = ui.Design(name="x", template="demo")
    assert ui._section_order(board_design, blueprint) == keys
    ui._move_section(board_design, blueprint, keys[-1], -1)
    assert board_design.order == [keys[-1]] + keys[:-1]
    ui._move_section(board_design, blueprint, keys[-1], -1)
    assert board_design.order[0] == keys[-1]

    ui._delete_design(DesignStore(tmp_path / "designs"), "demo", "camt-053")
    assert not (tmp_path / "designs" / "camt-053").exists()
    ui._remove_template(Registry(registry_with_demo), "demo")
    assert not (registry_with_demo / "demo").exists()


def _app_module():
    spec = importlib.util.spec_from_file_location("sdgen_ui_app", APP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mappings_page_shows_grid_and_writes_workbook(registry_with_demo, tmp_path):
    from sdgen.design import Design, DesignStore
    from sdgen.mapping.model import FieldInfo, MappingEntry, MappingSet, SourceSpec, TargetSpec

    store = DesignStore(tmp_path / "designs")
    mapping = MappingSet(
        name="camt",
        target=TargetSpec(name="API", kind="edmx", fields=[FieldInfo(path="Stmt/Id", required=True), FieldInfo(path="Stmt/Amount")]),
        sources=[SourceSpec(name="Bank A", file="a.xml", kind="xml", fields=[FieldInfo(path="Doc/Id"), FieldInfo(path="Doc/Amt")])],
        entries=[MappingEntry(target_path="Stmt/Id", source="Bank A", source_path="Doc/Id")],
    )
    store.save(Design(name="camt", template="demo", mapping=mapping))

    app = AppTest.from_file(str(APP), default_timeout=60).run()
    app.sidebar.radio[0].set_value("Mappings").run()
    assert not app.exception
    assert app.selectbox(key="mapping_design:demo").value == "camt"
    assert any("Source Bank A: 1 of 2 target fields mapped" in t.value for t in app.text)

    app.button(key="design:demo:camt:summary_to_brief").click().run()
    assert not app.exception
    design = app.session_state["design:demo:camt"]
    assert "Detailed field mapping: camt-mapping.xlsx" in design.brief.mapping_summary
    assert (tmp_path / "designs" / "camt" / "mapping" / "camt-mapping.xlsx").is_file()
    assert store.load("camt").brief.mapping_summary == design.brief.mapping_summary
