from __future__ import annotations

import base64
import html
from pathlib import Path

from lxml import etree
from pptx.util import Inches

from sdgen.flow import FlowNode, FlowSpec, layout_for, node_is_sap
from sdgen.flowlayout import Rect
from sdgen.icons import icon_file
from sdgen.palette import EDGE, GREY_FILL, SAP_BLUE, SAP_DARK, SAP_FILL, SLATE, SUBTITLE, WHITE, css
from sdgen.references import lookup

MARGIN, PAD = 40, 20
LOGO_W, LOGO_H, ICON = 41, 21, 32
PAGE_W, PAGE_H = 1169, 827
DRAWING_BOX = (0, 0, Inches(11.0), Inches(6.5))  # the area the layout fills, at 100 px per inch on the page
PX_PER_EMU = 100 / 914400

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
    """A draw.io file in the SAP Architecture Center style: one rounded container per lane band, nodes inside, icons where the catalogue has one."""
    mxfile = etree.Element("mxfile", host="sdgen")
    diagram = etree.SubElement(mxfile, "diagram", id="flow", name=spec.title or "Flow")
    layout = layout_for(spec, DRAWING_BOX)
    reference = lookup(spec.reference)
    bottom = max((band.rect.bottom for band in layout.lanes), default=0)
    width = _px(layout.canvas.width) + 2 * MARGIN
    height = 2 * MARGIN + _px(bottom) + (FOOTNOTE_H + PAD if reference else 0)
    model = etree.SubElement(diagram, "mxGraphModel", dx="1200", dy="800", grid="1", gridSize="10", guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1", pageWidth=str(max(width, PAGE_W)), pageHeight=str(max(height, PAGE_H)))
    root = etree.SubElement(model, "root")
    etree.SubElement(root, "mxCell", id="0")
    etree.SubElement(root, "mxCell", id="1", parent="0")

    bands: dict[tuple[str, int], tuple[str, Rect]] = {}
    for band in layout.lanes:
        lane_id = f"lane_{band.lane}" + (f"_r{band.row}" if band.row else "")
        bands[(band.lane, band.row)] = (lane_id, band.rect)
        w, h = _px(band.rect.width), _px(band.rect.height)
        _vertex(root, lane_id, "", CONTAINER + _colours(band.sap, container=True), MARGIN + _px(band.rect.left), MARGIN + _px(band.rect.top), w, h, "1")
        title_x = PAD
        if band.sap:
            _vertex(root, f"{lane_id}_logo", "", LOGO, PAD, 10, LOGO_W, LOGO_H, lane_id)
            title_x += LOGO_W + 8
        _vertex(root, f"{lane_id}_title", html.escape(band.title), TITLE + f"fontColor={css(SAP_DARK if band.sap else SLATE)};", title_x, 8, w - title_x - PAD, 24, lane_id)
    for node in spec.nodes:
        placed = layout.nodes.get(node.id)
        if placed is None:
            continue
        lane_id, band_rect = bands[(placed.lane, placed.row)]
        style = NODE + _colours(node_is_sap(node), container=False)
        image = _image(node.icon, icons)
        if image:
            style += NODE_ICON + f"image={image};"
        _vertex(root, f"n_{node.id}", _value(node), style, _px(placed.rect.left - band_rect.left), _px(placed.rect.top - band_rect.top), _px(placed.rect.width), _px(placed.rect.height), lane_id)

    for path in layout.edges:
        edge = spec.edges[path.number - 1]
        a, b = layout.nodes[path.source].rect, layout.nodes[path.target].rect
        style = EDGE_STYLE + _ports(a, path.points[0], b, path.points[-1]) + ("dashed=1;" if path.kind != "sync" else "")
        cell = etree.SubElement(root, "mxCell", id=f"e_{path.number}", value=html.escape(edge.label), style=style, edge="1", parent="1", source=f"n_{path.source}", target=f"n_{path.target}")
        geometry = etree.SubElement(cell, "mxGeometry", relative="1", **{"as": "geometry"})
        if len(path.points) > 2:
            points = etree.SubElement(geometry, "Array", **{"as": "points"})
            for x, y in path.points[1:-1]:
                etree.SubElement(points, "mxPoint", x=str(MARGIN + _px(x)), y=str(MARGIN + _px(y)))
    if reference is not None:
        found, entry = reference
        text = f"Reference: {found.name}, {entry.title} ({found.url(entry)})"
        _vertex(root, "reference", html.escape(text), FOOTNOTE, MARGIN, MARGIN + _px(bottom) + PAD, max(width - 2 * MARGIN, 600), FOOTNOTE_H, "1")
    return etree.tostring(mxfile, encoding="unicode", pretty_print=True)


def _px(emu: int) -> int:
    return round(emu * PX_PER_EMU)


def _ports(a: Rect, start: tuple[int, int], b: Rect, end: tuple[int, int]) -> str:
    """Where the edge leaves and enters, as fractions of the node boxes, taken from the routed points."""
    exit_x, exit_y = _fraction(start[0], a.left, a.width), _fraction(start[1], a.top, a.height)
    entry_x, entry_y = _fraction(end[0], b.left, b.width), _fraction(end[1], b.top, b.height)
    return f"exitX={exit_x};exitY={exit_y};exitDx=0;exitDy=0;entryX={entry_x};entryY={entry_y};entryDx=0;entryDy=0;"


def _fraction(value: int, start: int, size: int) -> str:
    share = (value - start) / size if size else 0.0
    return f"{min(1.0, max(0.0, share)):.2f}"


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
