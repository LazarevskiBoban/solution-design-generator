from __future__ import annotations

import base64
import html
from pathlib import Path

from lxml import etree

from sdgen.flow import LANES, FlowNode, FlowSpec, lane_is_sap, lane_title, node_is_sap
from sdgen.icons import icon_file
from sdgen.palette import EDGE, GREY_FILL, SAP_BLUE, SAP_DARK, SAP_FILL, SLATE, SUBTITLE, WHITE, css
from sdgen.references import lookup

MARGIN, LANE_GAP, PAD, HEADER = 40, 80, 20, 44
NODE_W, NODE_H, NODE_GAP = 200, 55, 26  # the gap holds a 10 px edge label between stacked nodes
LOGO_W, LOGO_H, ICON = 41, 21, 32
LANE_W = NODE_W + 2 * PAD
PAGE_W, PAGE_H = 1169, 827

BOX = "rounded=1;arcSize=24;absoluteArcSize=1;strokeWidth=1.5;whiteSpace=wrap;html=1;fontFamily=Helvetica;fontSize=12;"
CONTAINER = BOX + "container=1;collapsible=0;recursiveResize=0;"
NODE = BOX + "verticalAlign=middle;align=center;"
# mxLabel puts the image at `spacing` and lays the text over it, so the text is inset past the icon.
NODE_ICON = f"shape=label;spacing=6;imageWidth={ICON};imageHeight={ICON};imageAlign=left;imageVerticalAlign=middle;spacingLeft={ICON + 4};align=left;"
TITLE = "text;html=1;align=left;verticalAlign=middle;fontStyle=1;fontSize=12;fontFamily=Helvetica;whiteSpace=wrap;"
LOGO = "image;image=img/lib/sap/SAP_Logo.svg;imageAspect=0;"  # ships with draw.io
FOOTNOTE = f"text;html=1;align=left;verticalAlign=middle;fontSize=10;fontFamily=Helvetica;whiteSpace=wrap;fontColor={css(SLATE)};"
FOOTNOTE_H = 30
EDGE_STYLE = f"edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;strokeColor={css(EDGE)};strokeWidth=1.5;endArrow=block;endFill=1;fontSize=10;fontFamily=Helvetica;labelBackgroundColor={css(WHITE)};"


def to_drawio(spec: FlowSpec, icons: dict[str, Path] | None = None) -> str:
    """A draw.io file in the SAP Architecture Center style: one rounded container per lane, side by side, nodes stacked inside, icons where the catalogue has one."""
    mxfile = etree.Element("mxfile", host="sdgen")
    diagram = etree.SubElement(mxfile, "diagram", id="flow", name=spec.title or "Flow")
    lanes = [lane for lane in LANES if any(n.lane == lane for n in spec.nodes)]
    tallest = max((sum(1 for n in spec.nodes if n.lane == lane) for lane in lanes), default=0)
    reference = lookup(spec.reference)
    width = 2 * MARGIN + len(lanes) * LANE_W + max(0, len(lanes) - 1) * LANE_GAP
    height = 2 * MARGIN + _lane_height(tallest) + (FOOTNOTE_H + PAD if reference else 0)
    model = etree.SubElement(diagram, "mxGraphModel", dx="1200", dy="800", grid="1", gridSize="10", guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1", pageWidth=str(max(width, PAGE_W)), pageHeight=str(max(height, PAGE_H)))
    root = etree.SubElement(model, "root")
    etree.SubElement(root, "mxCell", id="0")
    etree.SubElement(root, "mxCell", id="1", parent="0")

    placed: dict[str, tuple[int, int]] = {}
    for index, lane in enumerate(lanes):
        members = [n for n in spec.nodes if n.lane == lane]
        sap = lane_is_sap(spec, lane)
        lane_id = f"lane_{lane}"
        _vertex(root, lane_id, "", CONTAINER + _colours(sap, container=True), MARGIN + index * (LANE_W + LANE_GAP), MARGIN, LANE_W, _lane_height(tallest), "1")
        title_x = PAD
        if sap:
            _vertex(root, f"{lane_id}_logo", "", LOGO, PAD, 10, LOGO_W, LOGO_H, lane_id)
            title_x += LOGO_W + 8
        _vertex(root, f"{lane_id}_title", html.escape(lane_title(spec, lane)), TITLE + f"fontColor={css(SAP_DARK if sap else SLATE)};", title_x, 8, LANE_W - title_x - PAD, 24, lane_id)
        for position, node in enumerate(members):
            style = NODE + _colours(node_is_sap(node), container=False)
            image = _image(node.icon, icons)
            if image:
                style += NODE_ICON + f"image={image};"
            _vertex(root, f"n_{node.id}", _value(node), style, PAD, HEADER + position * (NODE_H + NODE_GAP), NODE_W, NODE_H, lane_id)
            placed[node.id] = (index, position)

    for number, edge in enumerate(spec.edges, 1):
        if edge.source not in placed or edge.target not in placed:
            continue
        style = EDGE_STYLE + _ports(placed[edge.source], placed[edge.target]) + ("dashed=1;" if edge.kind != "sync" else "")
        cell = etree.SubElement(root, "mxCell", id=f"e_{number}", value=html.escape(edge.label), style=style, edge="1", parent="1", source=f"n_{edge.source}", target=f"n_{edge.target}")
        etree.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
    if reference is not None:
        found, entry = reference
        text = f"Reference: {found.name}, {entry.title} ({found.url(entry)})"
        _vertex(root, "reference", html.escape(text), FOOTNOTE, MARGIN, MARGIN + _lane_height(tallest) + PAD, max(width - 2 * MARGIN, 600), FOOTNOTE_H, "1")
    return etree.tostring(mxfile, encoding="unicode", pretty_print=True)


def _lane_height(count: int) -> int:
    return HEADER + count * NODE_H + max(0, count - 1) * NODE_GAP + PAD


def _ports(start: tuple[int, int], end: tuple[int, int]) -> str:
    """Where an edge leaves and enters: down between stacked neighbours, around the right side past a node in between, sideways between lanes (forward edges a little higher than return edges)."""
    (lane_a, pos_a), (lane_b, pos_b) = start, end
    if lane_a == lane_b:
        if abs(pos_a - pos_b) > 1:
            exit_, entry = (1, 0.5), (1, 0.5)
        elif pos_b > pos_a:
            exit_, entry = (0.5, 1), (0.5, 0)
        else:
            exit_, entry = (0.5, 0), (0.5, 1)
    elif lane_b > lane_a:
        exit_, entry = (1, 0.4), (0, 0.4)
    else:
        exit_, entry = (0, 0.65), (1, 0.65)
    return f"exitX={exit_[0]};exitY={exit_[1]};exitDx=0;exitDy=0;entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"


def _colours(sap: bool, container: bool) -> str:
    if sap:
        return f"strokeColor={css(SAP_BLUE)};fillColor={css(SAP_FILL if container else WHITE)};"
    return f"strokeColor={css(SLATE)};fillColor={css(GREY_FILL)};" + ("" if container else "dashed=1;dashPattern=1 2;")


def _value(node: FlowNode) -> str:
    text = f"<b>{html.escape(node.label)}</b>"
    if node.subtitle:
        text += f"<br/><span style='font-size:10px;color:{css(SUBTITLE)}'>{html.escape(node.subtitle)}</span>"
    return text


def _vertex(root, cell_id: str, value: str, style: str, x: int, y: int, width: int, height: int, parent: str) -> None:
    cell = etree.SubElement(root, "mxCell", id=cell_id, value=value, style=style, vertex="1", parent=parent)
    etree.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(width), height=str(height), **{"as": "geometry"})


def _image(key: str, icons: dict[str, Path] | None) -> str:
    if not key:
        return ""
    path = (icons or {}).get(key) if icons is not None else icon_file(key)
    if path is None or not Path(path).is_file():
        return ""
    # draw.io keeps the data URI inside the style string, so the base64 form without ";base64" is used.
    return "data:image/svg+xml," + base64.b64encode(Path(path).read_bytes()).decode("ascii")
