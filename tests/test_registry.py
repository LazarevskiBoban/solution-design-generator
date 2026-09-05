import pytest

from sdgen.manifest import Binding, FieldSpec, Manifest, ShapeRef
from sdgen.registry import Registry


def test_add_load_and_list(tmp_path, sample_deck):
    registry = Registry(tmp_path / "templates")
    assert registry.names() == []
    manifest = Manifest(
        name="draft",
        source="whatever.pptx",
        fields=[FieldSpec(key="need", label="Need", bindings=[Binding(slide=1, shape=ShapeRef(id=3))])],
    )
    entry = registry.add("demo", sample_deck, manifest)
    assert entry.template_path.is_file()
    assert entry.manifest.name == "demo" and entry.manifest.source == "template.pptx"
    assert registry.names() == ["demo"]
    assert entry.original.fields["need"] == "First point\nSub point"

    loaded = registry.load("demo")
    assert loaded.manifest.field("need").bindings[0].slide == 1
    assert loaded.template_path == entry.template_path
    assert loaded.original.fields["need"] == entry.original.fields["need"]

    updated = loaded.manifest.model_copy(update={"fields": []})
    registry.save("demo", updated)
    assert registry.load("demo").manifest.fields == []


def test_names_are_sanitised(tmp_path, sample_deck):
    registry = Registry(tmp_path / "templates")
    entry = registry.add("NTT Solution Design (v2)", sample_deck, Manifest(name="x"))
    assert entry.name == "ntt-solution-design-v2"
    assert registry.names() == ["ntt-solution-design-v2"]
    with pytest.raises(ValueError):
        registry.add("***", sample_deck, Manifest(name="x"))


def test_missing_template_raises(tmp_path):
    registry = Registry(tmp_path / "templates")
    with pytest.raises(FileNotFoundError):
        registry.load("nope")
    with pytest.raises(FileNotFoundError):
        registry.save("nope", Manifest(name="nope"))


def test_reanalyze_keeps_section_and_field_edits(tmp_path, sample_deck):
    from sdgen.analyze import analyze_deck
    from sdgen.blueprint import derive_blueprint
    from sdgen.inventory import inspect_deck

    deck = inspect_deck(sample_deck)
    analysis = analyze_deck(deck)
    manifest = analysis.to_manifest("demo")
    blueprint = derive_blueprint(deck, analysis, manifest, "demo")
    registry = Registry(tmp_path / "templates")
    entry = registry.add("demo", sample_deck, manifest, blueprint=blueprint)
    assert entry.source_path.is_file() and entry.source_path.stat().st_size == sample_deck.stat().st_size

    edited = [f.model_copy(update={"key": "scope_rows", "label": "Scope rows"}) if f.key == "scope" else f for f in entry.manifest.fields]
    registry.save("demo", entry.manifest.model_copy(update={"fields": edited}))
    first = entry.blueprint.sections[0].model_copy(update={"title": "Overview", "key": "overview", "ask": "Say it short"})
    registry.save_blueprint("demo", entry.blueprint.model_copy(update={"sections": [first] + entry.blueprint.sections[1:]}))

    again = registry.reanalyze("demo")
    assert again.manifest.field("scope_rows").label == "Scope rows" and again.manifest.field("scope") is None
    section = again.blueprint.sections[0]
    assert (section.title, section.key, section.ask) == ("Overview", "overview", "Say it short") and "scope_rows" in section.fields
    assert registry.load("demo").blueprint.sections[0].key == "overview"

    entry.source_path.unlink()
    with pytest.raises(FileNotFoundError):
        registry.reanalyze("demo")


def test_carry_fields_tells_token_fields_on_one_shape_apart():
    from sdgen.registry import _carry_fields

    def token(key, name):
        return FieldSpec(key=key, label=key, bindings=[Binding(slide=5, shape=ShapeRef(id=24), mode="token", token="{{" + name + "}}")])

    stored = Manifest(name="t", fields=[token("internal_effort", "internal_effort"), token("external_effort", "external_effort")])
    fresh = Manifest(name="t", fields=[token("initiative_1", "internal_effort"), token("initiative_2", "external_effort")])
    assert [f.key for f in _carry_fields(fresh, stored).fields] == ["internal_effort", "external_effort"]


def test_remove_deletes_the_template_folder(tmp_path, sample_deck):
    registry = Registry(tmp_path / "templates")
    registry.add("demo", sample_deck, Manifest(name="demo"), tokenize=False)
    assert registry.names() == ["demo"]
    registry.remove("demo")
    assert registry.names() == [] and not (tmp_path / "templates" / "demo").exists()
    with pytest.raises(FileNotFoundError):
        registry.remove("demo")
