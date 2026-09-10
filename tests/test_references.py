import pytest

from sdgen import references

SAP_SOURCES = references.DEFAULT_REF_DIR / "sap" / "RA0032"


def test_sap_pack_loads_with_every_reference_architecture():
    pack = references.pack("sap")
    assert pack is not None and pack.name == "SAP Architecture Center" and len(pack.entries) == 33
    ids = [r.id for r in pack.entries]
    assert ids[0] == "RA0001" and ids[-1] == "RA0033" and len(set(ids)) == 33
    a2a = pack.find("RA0021")
    assert pack.url(a2a) == "https://architecture.learning.sap.com/docs/ref-arch/6501d5" and pack.qualified(a2a) == "sap:RA0021"
    assert all(r.title and r.slug and r.summary and r.keywords for r in pack.entries)
    assert pack.find("RA0000") is None and references.pack("nope") is None


def test_packs_switch_on_by_the_brief_and_resolve_qualified_ids():
    assert [p.system for p in references.matching("lockbox file to S/4HANA via CPI")] == ["sap"]
    assert references.matching("Salesforce to Workday sync") == []
    pack, reference = references.lookup("sap:RA0022")
    assert pack.system == "sap" and reference.title == "API Managed Integration"
    assert references.lookup("sap:RA9999") is None and references.lookup("nope") is None and references.lookup("") is None


def test_candidates_rank_the_closest_pattern():
    pack = references.pack("sap")
    ranked = references.candidates(pack, "Lockbox files from the bank are posted into S/4HANA through Cloud Integration as IDocs")
    assert ranked and ranked[0].id == "RA0021" and len(ranked) <= 3
    assert references.candidates(pack, "Peppol e-invoices go to the tax authority")[0].id == "RA0015"
    assert references.candidates(pack, "Business partner master data is distributed to third-party systems")[0].id == "RA0017"
    assert references.candidates(pack, "nothing relevant whatsoever") == []


def test_labels_read_the_fetched_diagram_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("SDGEN_REFS", str(tmp_path))
    assert references.labels("sap", "RA0021") == [] and references.installed("sap") == set()
    folder = tmp_path / "sap" / "RA0021" / "drawio"
    folder.mkdir(parents=True)
    values = [
        "&lt;b&gt;SAP Cloud Connector&lt;/b&gt;&lt;br&gt;&lt;span style='font-size:10px'&gt;Outbound-only tunnel&lt;/span&gt;",
        "",
        "SAP Cloud Connector   Outbound-only tunnel",
        "one two three four five six seven eight",
        "Data Flow",
    ]
    cells = "".join(f'<mxCell id="c{i}" value="{v}" vertex="1"/>' for i, v in enumerate(values))
    (folder / "a.drawio").write_text(f'<mxfile><diagram id="d" name="n"><mxGraphModel><root><mxCell id="0"/>{cells}</root></mxGraphModel></diagram></mxfile>', encoding="utf-8")
    assert references.labels("sap", "RA0021") == ["SAP Cloud Connector Outbound-only tunnel", "Data Flow"]
    assert references.installed("sap") == {"RA0021"}
    many = "".join(f'<mxCell id="m{i}" value="Block {i}" vertex="1"/>' for i in range(60))
    (folder / "b.drawio").write_text(f'<mxfile><diagram id="d" name="n"><mxGraphModel><root>{many}</root></mxGraphModel></diagram></mxfile>', encoding="utf-8")
    assert len(references.labels("sap", "RA0021")) == references.MAX_LABELS
    (folder / "broken.drawio").write_text("<mxfile>", encoding="utf-8")
    assert len(references.labels("sap", "RA0021")) == references.MAX_LABELS


def test_cell_texts_returns_every_label(tmp_path):
    path = tmp_path / "a.drawio"
    path.write_text('<mxfile><diagram id="d" name="n"><mxGraphModel><root><mxCell id="1" value="&lt;b&gt;A&lt;/b&gt; long label with many words in it here" vertex="1"/><mxCell id="2" value="B"/><mxCell id="3" value="B"/><mxCell id="4" value=""/></root></mxGraphModel></diagram></mxfile>', encoding="utf-8")
    assert references.cell_texts(path) == ["A long label with many words in it here", "B"]
    assert references.cell_texts(path.read_bytes()) == references.cell_texts(path) and references.cell_texts(b"<mxfile>") == []


@pytest.mark.skipif(not SAP_SOURCES.is_dir(), reason="run sdgen refs --fetch to compare the palette with the SAP sources")
def test_palette_matches_the_fetched_sap_sources():
    from sdgen import palette

    text = "".join(path.read_text(encoding="utf-8") for path in SAP_SOURCES.rglob("*.drawio"))
    for token in (palette.SAP_BLUE, palette.SAP_FILL, palette.SLATE, palette.GREY_FILL, palette.EDGE, palette.SAP_DARK):
        assert f"#{token}" in text, token
    assert "arcSize=24" in text and "img/lib/sap/SAP_Logo.svg" in text
