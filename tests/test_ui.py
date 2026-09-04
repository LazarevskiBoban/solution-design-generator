from pathlib import Path

import pytest
from pptx import Presentation
from streamlit.testing.v1 import AppTest

from sdgen.analyze import analyze_deck
from sdgen.blueprint import derive_blueprint
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
    monkeypatch.delenv("SDGEN_LLM", raising=False)
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
