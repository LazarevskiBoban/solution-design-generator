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

    loaded = registry.load("demo")
    assert loaded.manifest.field("need").bindings[0].slide == 1
    assert loaded.template_path == entry.template_path

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
