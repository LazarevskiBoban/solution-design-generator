from pptx.util import Inches

from sdgen.flowlayout import BAND_MIN_NODE_W, EdgeIn, LaneInfo, NodeIn, layout_flow, rank_columns

LANES = [LaneInfo("source", "Source"), LaneInfo("middleware", "Middleware"), LaneInfo("target", "Target")]


def _chain(count: int, lane: str = "middleware"):
    nodes = [NodeIn(f"n{i}", lane) for i in range(1, count + 1)]
    edges = [EdgeIn(i, f"n{i}", f"n{i + 1}", f"e{i}") for i in range(1, count)]
    return nodes, edges


def test_rank_follows_the_edges_and_ignores_back_edges():
    nodes = [NodeIn("a", "source"), NodeIn("b", "middleware"), NodeIn("c", "middleware"), NodeIn("d", "middleware"), NodeIn("e", "target")]
    edges = [EdgeIn(1, "a", "b"), EdgeIn(2, "b", "c"), EdgeIn(3, "c", "b"), EdgeIn(4, "c", "e"), EdgeIn(5, "e", "a")]
    assert rank_columns(nodes, edges) == {"a": 0, "b": 1, "c": 2, "d": 3, "e": 3}


def test_nodes_shrink_before_the_diagram_wraps():
    box = (0, 0, Inches(10), Inches(5.5))
    six = layout_flow(*_chain(6), LANES[1:2], box)
    widths = [n.rect.width for n in six.nodes.values()]
    assert six.rows == 1 and six.per_row == 6 and max(widths) < Inches(3.0) and min(widths) >= BAND_MIN_NODE_W - 1
    nine = layout_flow(*_chain(9), LANES[1:2], box)
    assert nine.rows == 2 and nine.per_row >= 5 and all(n.rect.width >= BAND_MIN_NODE_W - 1 for n in nine.nodes.values())
    assert nine.nodes["n1"].row == 0 and nine.nodes["n9"].row == 1 and nine.nodes["n9"].column == 8 - nine.per_row
    assert [band.row for band in nine.lanes] == [0, 1] and nine.lanes[1].rect.top > nine.lanes[0].rect.bottom


def test_far_lanes_use_the_right_channel_and_rows_link_through_the_corridor():
    nodes = [NodeIn("s", "source"), NodeIn("m", "middleware"), NodeIn("t", "target")]
    edges = [EdgeIn(1, "s", "m"), EdgeIn(2, "m", "t"), EdgeIn(3, "s", "t", "far")]
    layout = layout_flow(nodes, edges, LANES, (0, 0, Inches(12), Inches(5.5)))
    far = next(p for p in layout.edges if p.number == 3)
    assert far.sites is None and max(x for x, _ in far.points) > max(b.rect.right for b in layout.lanes)
    assert far.label is not None and far.label.text == "far"
    assert next(p for p in layout.edges if p.number == 1).sites is None  # adjacent bands: a routed path through the seam
    nine = layout_flow(*_chain(9), LANES[1:2], (0, 0, Inches(6), Inches(5.5)))
    link = next(p for p in nine.edges if nine.nodes[p.source].row != nine.nodes[p.target].row)
    xs = [x for x, _ in link.points]
    assert min(xs) < min(b.rect.left for b in nine.lanes) and max(xs) > max(b.rect.right for b in nine.lanes)


def test_sequence_layout_places_participants_on_top_and_numbers_the_arrows():
    nodes = [NodeIn("adapter", "middleware", "BTP sFTP adapter"), NodeIn("door", "source", "sFTP door")]
    edges = [EdgeIn(i, "adapter" if i % 2 else "door", "door" if i % 2 else "adapter", f"step {i}") for i in range(1, 6)]
    layout = layout_flow(nodes, edges, LANES, (0, 0, Inches(12), Inches(6)), mode="sequence")
    adapter, door = layout.nodes["adapter"].rect, layout.nodes["door"].rect
    assert layout.mode == "sequence" and layout.lanes == [] and adapter.top == door.top and adapter.left < door.left
    assert [(line[0], line[1]) for line in layout.lifelines] == [("adapter", adapter.cx), ("door", door.cx)] and all(line[2] == adapter.bottom for line in layout.lifelines)
    ys = [p.points[0][1] for p in layout.edges]
    assert ys == sorted(ys) and len(set(ys)) == 5 and all(p.points[0][1] == p.points[-1][1] for p in layout.edges)
    assert [p.points[0][0] for p in layout.edges] == [adapter.cx, door.cx, adapter.cx, door.cx, adapter.cx]
    assert [p.label.text for p in layout.edges] == [f"{i}. step {i}" for i in range(1, 6)] and all(p.label.rect.bottom <= p.points[0][1] for p in layout.edges)
    assert all(p.points[0][1] < Inches(6) and p.label.rect.top >= adapter.bottom for p in layout.edges)


def test_flat_box_falls_back_to_columns_and_the_layout_is_deterministic():
    nodes, edges = _chain(4)
    assert layout_flow(nodes, edges, LANES, (0, 0, Inches(6), Inches(1.0))).mode == "columns"
    box = (0, 0, Inches(12), Inches(5.5))
    assert layout_flow(nodes, edges, LANES[1:2], box) == layout_flow(nodes, edges, LANES[1:2], box)
    assert layout_flow([], [], LANES, box).nodes == {}
