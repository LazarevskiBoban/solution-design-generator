from sdgen.llm import default_deployment


def test_default_deployment_prefers_a_reasoning_model():
    assert default_deployment(["gpt-4.1", "gpt-5", "gpt-4o-mini"]) == "gpt-5"
    assert default_deployment(["gpt-4.1", "o3-mini"]) == "o3-mini"
    assert default_deployment(["gpt-4.1", "gpt-4o-mini"]) == "gpt-4.1"
    assert default_deployment([]) == ""


def test_mock_draft_keeps_table_cells_short():
    from sdgen.llm import MOCK_CELL_CHARS, mock_draft

    long = ("one long line without a full stop " * 12).strip()
    section = {"key": "scope", "title": "Scope", "kind": "table", "fields": [{"key": "scope", "kind": "table", "columns": ["Function", "Countries"]}]}
    row = next(line for line in mock_draft({"brief": {"subject": "X", "about": long}, "sections": [section]}).splitlines() if line.startswith("| [Draft]"))
    cell = row.split("|")[1].strip()
    assert cell.startswith("[Draft] ") and cell.endswith("…") and len(cell) <= MOCK_CELL_CHARS + len("[Draft] ")
