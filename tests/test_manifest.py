import pytest

from sdgen.manifest import Binding, FieldSpec, GlobalSpec, Manifest, ShapeRef, SlideRules


def _manifest() -> Manifest:
    return Manifest(
        name="demo",
        globals=[GlobalSpec(key="subject", replaces="Demo Integration")],
        fields=[
            FieldSpec(
                key="business_need",
                label="Business Need",
                bindings=[Binding(slide=5, shape=ShapeRef(id=3, name="Box"), keep_prefix="Business Need: ", max_chars=900)],
            ),
            FieldSpec(
                key="scope",
                label="Scope",
                kind="table",
                columns=["Function", "Countries"],
                bindings=[Binding(slide=5, shape=ShapeRef(id=34), keep_last_row_if="Total")],
            ),
            FieldSpec(
                key="effort",
                label="Internal effort",
                bindings=[Binding(slide=5, shape=ShapeRef(id=24), mode="token", token="<Internal effort>")],
            ),
        ],
        slides=SlideRules(exclude=[30, 31], prototypes={"body_text": 13}),
    )


def test_yaml_roundtrip(tmp_path):
    path = tmp_path / "manifest.yaml"
    original = _manifest()
    original.save(path)
    text = path.read_text(encoding="utf-8")
    assert "mode: replace" not in text
    assert "keep_prefix: 'Business Need: '" in text
    assert Manifest.load(path) == original


def test_keep_prefix_is_normalised_to_end_with_a_space():
    assert Binding(slide=1, shape=ShapeRef(id=1), keep_prefix="Business Need:").keep_prefix == "Business Need: "
    assert Binding(slide=1, shape=ShapeRef(id=1), keep_prefix="Note: ").keep_prefix == "Note: "
    assert Binding(slide=1, shape=ShapeRef(id=1), keep_prefix="  ").keep_prefix is None


def test_lookup_helpers():
    manifest = _manifest()
    assert manifest.keys == ["subject", "business_need", "scope", "effort"]
    assert manifest.field("scope").columns == ["Function", "Countries"]
    assert manifest.field("missing") is None


def test_duplicate_keys_rejected():
    with pytest.raises(ValueError, match="duplicate keys"):
        Manifest(name="x", fields=[FieldSpec(key="a", label="A"), FieldSpec(key="a", label="B")])
