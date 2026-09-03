from pathlib import Path

import pytest
from pptx import Presentation
from streamlit.testing.v1 import AppTest

from sdgen.analyze import analyze_deck
from sdgen.inventory import inspect_deck
from sdgen.manifest import GlobalSpec
from sdgen.registry import Registry

APP = Path(__file__).resolve().parent.parent / "ui" / "app.py"


@pytest.fixture
def registry_with_demo(sample_deck, tmp_path, monkeypatch):
    manifest = analyze_deck(inspect_deck(sample_deck)).to_manifest("demo")
    manifest.globals.append(GlobalSpec(key="subject", label="Subject", replaces="Demo Integration"))
    root = tmp_path / "templates"
    Registry(root).add("demo", sample_deck, manifest)
    monkeypatch.setenv("SDGEN_TEMPLATES", str(root))
    return root


def test_templates_page_without_upload_shows_guidance(tmp_path, monkeypatch):
    monkeypatch.setenv("SDGEN_TEMPLATES", str(tmp_path / "empty"))
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "Templates"
    assert any("Upload a deck" in i.value for i in app.info)


def test_generate_page_produces_a_document(registry_with_demo):
    app = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "Generate"
    assert app.selectbox(key="template_choice").value == "demo"

    app.text_input(key="demo:0:g:subject").input("Lockbox Integration")
    app.text_area(key="demo:0:f:business_need").input("Statements arrive daily.\n- CAMT.053")
    app.text_area(key="demo:0:f:first_point").input("- alpha\n  - beta")
    app.run()
    app.button(key="demo:generate").click().run()
    assert not app.exception
    assert any("Generated" in s.value for s in app.success)

    output = app.session_state["demo:output"]
    file_name, data, issues, slides = output
    assert file_name == "lockbox_integration.pptx" and slides == 2
    saved = Path(registry_with_demo) / "generated.pptx"
    saved.write_bytes(data)
    slide = Presentation(str(saved)).slides[0]
    assert slide.shapes.title.text == "Executive Overview: Lockbox Integration"
    need = next(s for s in slide.shapes if s.name == "Business Need Box").text_frame.text
    assert need == "Business Need: Statements arrive daily.\nCAMT.053"
    assert any("scope" in i for i in issues)
