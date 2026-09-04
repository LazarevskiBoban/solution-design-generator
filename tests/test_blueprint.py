from pathlib import Path

import pytest
from click.testing import CliRunner

from sdgen.analyze import analyze_deck
from sdgen.blueprint import Blueprint, clean_title, derive_blueprint, format_outline
from sdgen.cli import main
from sdgen.inventory import inspect_deck
from sdgen.manifest import GlobalSpec
from sdgen.registry import Registry

CARRIER_DECK = Path("D:/NTT-architectures/NTT DATA Inc I IT I Solution Design I Carrier AP Invoice Integration.pptx")


def _derive(sample_deck):
    deck = inspect_deck(sample_deck)
    analysis = analyze_deck(deck)
    manifest = analysis.to_manifest("demo")
    manifest.globals.append(GlobalSpec(key="subject", replaces="Demo Integration"))
    return deck, analysis, manifest, derive_blueprint(deck, analysis, manifest, "demo")


def test_clean_title_removes_subject_and_separators():
    subject = "Carrier AP Invoice Integration"
    assert clean_title("Executive Overview: Carrier AP Invoice Integration", subject) == "Executive Overview"
    assert clean_title("Integration Architecture: Carrier AP Invoice Integration (Request Sundry)", subject) == "Integration Architecture (Request Sundry)"
    assert clean_title("Integration Architecture: Carrier AP Invoice Integration | Duplicate Checking", subject) == "Integration Architecture | Duplicate Checking"
    assert clean_title("Overview: {{subject}}", None) == "Overview"
    assert clean_title(None, subject) == ""


def test_fixture_sections(sample_deck):
    deck, analysis, manifest, blueprint = _derive(sample_deck)
    assert [s.slide for s in blueprint.sections] == [1, 2]
    first, second = blueprint.sections
    assert first.title == "Executive Overview" and first.kind == "composite"
    assert set(first.fields) == {"business_need", "scope", "first_point"}
    assert "Scope (table: Function, Countries)" in first.ask
    assert "Business Need: Something long enough" in first.example
    assert "Scope: Function | Countries" in first.example
    assert second.kind == "divider" and second.fields == []
    assert first.writable and not second.writable


def test_blueprint_roundtrip_and_registry(sample_deck, tmp_path):
    deck, analysis, manifest, blueprint = _derive(sample_deck)
    blueprint.save(tmp_path / "b.yaml")
    assert Blueprint.load(tmp_path / "b.yaml") == blueprint

    registry = Registry(tmp_path / "templates")
    entry = registry.add("demo", sample_deck, manifest, blueprint=blueprint)
    assert entry.blueprint is not None and entry.blueprint.name == "demo"
    loaded = registry.load("demo")
    assert loaded.blueprint.section("executive_overview").kind == "composite"
    registry.save_blueprint("demo", loaded.blueprint.model_copy(update={"sections": []}))
    assert registry.load("demo").blueprint.sections == []


def test_cli_outline_on_deck_and_template(sample_deck, tmp_path):
    result = CliRunner().invoke(main, ["outline", str(sample_deck)])
    assert result.exit_code == 0, result.output
    assert "composite  Executive Overview" in result.output

    deck, analysis, manifest, blueprint = _derive(sample_deck)
    Registry(tmp_path / "t").add("demo", sample_deck, manifest, blueprint=blueprint)
    result = CliRunner().invoke(main, ["outline", "demo", "--templates", str(tmp_path / "t")])
    assert result.exit_code == 0, result.output
    assert format_outline(blueprint).splitlines()[1] in result.output


@pytest.mark.skipif(not CARRIER_DECK.exists(), reason="example deck not available")
def test_carrier_outline():
    deck = inspect_deck(CARRIER_DECK)
    analysis = analyze_deck(deck)
    manifest = analysis.to_manifest("ntt")
    blueprint = derive_blueprint(deck, analysis, manifest, "ntt")
    by_slide = {s.slide: s for s in blueprint.sections}

    assert len(blueprint.sections) == 30 and 30 not in by_slide and 31 not in by_slide
    assert by_slide[1].kind == "cover"
    assert (by_slide[2].title, by_slide[2].kind) == ("Document Version Control", "table")
    assert (by_slide[3].title, by_slide[3].kind) == ("Contents", "static")
    assert (by_slide[5].title, by_slide[5].kind) == ("Executive Overview", "composite")
    assert "business_need" in by_slide[5].fields and "scope" in by_slide[5].fields
    assert (by_slide[6].kind, by_slide[8].kind, by_slide[8].title) == ("table", "diagram", "Level 2 System Flows")
    assert by_slide[11].kind == "divider" and by_slide[11].title == "Application Architecture"
    assert by_slide[15].kind == "divider" and by_slide[15].title == "Integration Architecture"
    assert by_slide[4].kind == "static"
    assert by_slide[12].kind == "diagram" and by_slide[12].images == 1
    assert by_slide[23].kind == "mapping"
    assert by_slide[28].kind == "references"
    assert by_slide[16].title.startswith("Integration Architecture (Request")
    assert "Internet Solutions offers Connectivity Services" in by_slide[5].example
