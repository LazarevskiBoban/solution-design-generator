from sdgen.material import load_material, material_corpus, material_text, mime_of, new_material, save_material, text_from_upload


def _items():
    return [
        new_material("text", title="Folder layout", tags=["flows"], text="reception/ emission/ tmp/"),
        new_material("link", title="ISO 20022", url="https://example.org/iso", text="Page text"),
        new_material("image", title="Landscape", file="ab12-landscape.png", tags=["overview"]),
        new_material("text", title="Notes", note="from the workshop", text="x" * 50),
    ]


def test_material_text_lists_tagged_first_and_caps():
    items = _items()
    block = material_text(items, tags={"flows"})
    heads = [line for line in block.splitlines() if line.startswith("## ")]
    assert heads[0] == "## Folder layout (text; for: flows)" and "Landscape" not in block
    assert heads[1] == "## ISO 20022 (link, https://example.org/iso)" and "Note: from the workshop" in block
    everything = material_text(items)
    assert "## Landscape (image, ab12-landscape.png; for: overview)\n(no text yet: nothing attached)" in everything
    assert material_text([]) == ""
    long = [new_material("text", title=f"T{i}", text="word " * 400) for i in range(4)]
    cut = material_text(long, per_item=100, total=350)
    assert "[... truncated, " in cut and cut.count("## T") == 2 and cut.endswith("[... 2 more item(s) omitted]")
    assert material_corpus(items).startswith("reception/ emission/ tmp/\nPage text")


def test_material_yaml_roundtrip_and_empty_file_removed(tmp_path):
    path = tmp_path / "material.yaml"
    items = _items()
    save_material(items, path)
    assert load_material(path) == items and path.read_text(encoding="utf-8").startswith("items:")
    save_material([], path)
    assert not path.exists() and load_material(path) == []


def test_text_from_upload_reads_drawio_csv_and_caps():
    drawio = b'<mxfile><diagram id="d" name="n"><mxGraphModel><root><mxCell id="1" value="SAP S/4HANA" vertex="1"/><mxCell id="2" value="&lt;b&gt;Cloud Connector&lt;/b&gt;" vertex="1"/><mxCell id="3" value="SAP S/4HANA" vertex="1"/></root></mxGraphModel></diagram></mxfile>'
    assert text_from_upload("landscape.drawio", drawio) == "SAP S/4HANA\nCloud Connector"
    assert text_from_upload("banks.csv", b"bank,lockbox\nBoA,yes\n") == "bank,lockbox\nBoA,yes\n"
    big = text_from_upload("notes.md", b"a" * 200_010)
    assert big.endswith("[... truncated, 10 more characters]") and len(big) < 200_100
    assert mime_of("x.PNG") == "image/png" and mime_of("y.jpg") == "image/jpeg" and mime_of("z.gif") == ""
