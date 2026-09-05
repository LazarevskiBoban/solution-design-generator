from lxml import etree

from sdgen.drawio import to_drawio
from sdgen.flow import FlowEdge, FlowNode, FlowSpec

SPEC = FlowSpec(
    title="Lockbox",
    nodes=[
        FlowNode(id="bank", label="Bank", lane="source", kind="external"),
        FlowNode(id="btp", label="BTP", lane="middleware", icon="btp_integration_suite"),
        FlowNode(id="s4", label="S/4HANA", lane="target", kind="store"),
    ],
    edges=[FlowEdge(source="bank", target="btp", label="file", kind="file"), FlowEdge(source="btp", target="s4", label="SFTP")],
)


def test_drawio_structure_without_icons():
    root = etree.fromstring(to_drawio(SPEC, icons={}).encode("utf-8"))
    assert root.tag == "mxfile" and root.find("diagram").get("name") == "Lockbox"
    cells = root.findall(".//mxCell")
    lanes = [c for c in cells if c.get("style", "").startswith("swimlane")]
    nodes = [c for c in cells if c.get("vertex") == "1" and c not in lanes]
    edges = [c for c in cells if c.get("edge") == "1"]
    assert [c.get("value") for c in lanes] == ["Source", "Middleware", "Target"]
    assert [c.get("value") for c in nodes] == ["Bank", "BTP", "S/4HANA"] and all("shape=image" not in c.get("style") for c in nodes)
    assert [c.get("parent") for c in nodes] == ["lane_source", "lane_middleware", "lane_target"]
    assert "dashed=1" in edges[0].get("style") and "dashed=1" not in edges[1].get("style") and edges[1].get("source") == "n_btp"
    assert "cylinder" in nodes[2].get("style") and "dashed=1" in nodes[0].get("style")


def test_drawio_embeds_icon_files(tmp_path):
    svg = tmp_path / "suite.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10"/></svg>', encoding="utf-8")
    root = etree.fromstring(to_drawio(SPEC, icons={"btp_integration_suite": svg}).encode("utf-8"))
    btp = next(c for c in root.findall(".//mxCell") if c.get("id") == "n_btp")
    style = btp.get("style")
    assert "shape=image" in style and "image=data:image/svg+xml," in style and ";base64" not in style
