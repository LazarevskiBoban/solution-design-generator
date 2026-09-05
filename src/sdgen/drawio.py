from __future__ import annotations

import base64
from pathlib import Path

from lxml import etree

from sdgen.flow import LANE_TITLES, LANES, FlowSpec
from sdgen.icons import icon_file

LANE_X, LANE_Y, LANE_WIDTH, LANE_HEIGHT, LANE_GAP = 40, 40, 1080, 180, 20
LANE_LABEL = 40
NODE_WIDTH, NODE_HEIGHT, NODE_GAP, NODE_TOP = 170, 60, 40, 55
ICON_SIZE = 64
FILLS = {"system": "#DCE8F7", "step": "#EEEEEE", "store": "#DFF0DF", "external": "#FFFFFF"}
SHAPES = {"system": "rounded=1;", "step": "rounded=0;", "store": "shape=cylinder3;boundedLbl=1;", "external": "rounded=1;dashed=1;"}


def to_drawio(spec: FlowSpec, icons: dict[str, Path] | None = None) -> str:
    """A draw.io file: one horizontal swimlane per lane, nodes left to right, icons where the catalogue has one."""
    mxfile = etree.Element("mxfile", host="sdgen")
    diagram = etree.SubElement(mxfile, "diagram", id="flow", name=spec.title or "Flow")
    model = etree.SubElement(diagram, "mxGraphModel", dx="1200", dy="800", grid="1", gridSize="10", guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1", pageWidth="1169", pageHeight="827")
    root = etree.SubElement(model, "root")
    etree.SubElement(root, "mxCell", id="0")
    etree.SubElement(root, "mxCell", id="1", parent="0")

    y = LANE_Y
    for lane in LANES:
        members = [n for n in spec.nodes if n.lane == lane]
        if not members:
            continue
        lane_id = f"lane_{lane}"
        cell = etree.SubElement(root, "mxCell", id=lane_id, value=LANE_TITLES[lane], style=f"swimlane;horizontal=0;startSize={LANE_LABEL};fillColor=#F7F7F7;strokeColor=#BFBFBF;fontStyle=1;", vertex="1", parent="1")
        etree.SubElement(cell, "mxGeometry", x=str(LANE_X), y=str(y), width=str(LANE_WIDTH), height=str(LANE_HEIGHT), **{"as": "geometry"})
        x = LANE_LABEL + NODE_GAP
        for node in members:
            image = _image(node.icon, icons)
            if image:
                style = f"shape=image;verticalLabelPosition=bottom;verticalAlign=top;aspect=fixed;imageAspect=0;image={image};"
                width, height, top = ICON_SIZE, ICON_SIZE, NODE_TOP - 10
            else:
                style = f"{SHAPES.get(node.kind, SHAPES['system'])}whiteSpace=wrap;html=1;fillColor={FILLS.get(node.kind, FILLS['system'])};strokeColor=#404040;"
                width, height, top = NODE_WIDTH, NODE_HEIGHT, NODE_TOP
            cell = etree.SubElement(root, "mxCell", id=f"n_{node.id}", value=node.label, style=style, vertex="1", parent=lane_id)
            etree.SubElement(cell, "mxGeometry", x=str(x), y=str(top), width=str(width), height=str(height), **{"as": "geometry"})
            x += NODE_WIDTH + NODE_GAP
        y += LANE_HEIGHT + LANE_GAP

    for number, edge in enumerate(spec.edges, 1):
        style = "edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;strokeColor=#404040;"
        if edge.kind != "sync":
            style += "dashed=1;"
        cell = etree.SubElement(root, "mxCell", id=f"e_{number}", value=edge.label, style=style, edge="1", parent="1", source=f"n_{edge.source}", target=f"n_{edge.target}")
        etree.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
    return etree.tostring(mxfile, encoding="unicode", pretty_print=True)


def _image(key: str, icons: dict[str, Path] | None) -> str:
    if not key:
        return ""
    path = (icons or {}).get(key) if icons is not None else icon_file(key)
    if path is None or not Path(path).is_file():
        return ""
    # draw.io keeps the data URI inside the style string, so the base64 form without ";base64" is used.
    return "data:image/svg+xml," + base64.b64encode(Path(path).read_bytes()).decode("ascii")
