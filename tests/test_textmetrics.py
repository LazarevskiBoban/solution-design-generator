import pytest

from sdgen import textmetrics
from sdgen.textmetrics import FontSpec, capacity_chars, line_chars, line_height_pt, load_font, text_width_pt, wrapped_lines

needs_fonts = pytest.mark.skipif(load_font("Arial") is None, reason="no Windows fonts on this machine")


def test_heuristics_apply_without_font_files(tmp_path, monkeypatch):
    monkeypatch.setattr(textmetrics, "FONT_DIR", tmp_path)
    spec = FontSpec("Arial", 10.0)
    assert load_font("Arial") is None
    assert text_width_pt("abcd", spec) == pytest.approx(4 * 10 * 0.55)
    assert line_height_pt(spec) == pytest.approx(13.0)
    assert wrapped_lines("word " * 20, 100.0, spec) == 7
    assert wrapped_lines("", 100.0, spec) == 1


@needs_fonts
def test_real_fonts_measure_width_lines_and_height():
    regular, bold = FontSpec("Arial", 10.0), FontSpec("Arial", 10.0, bold=True)
    assert text_width_pt("Hello world", bold) > text_width_pt("Hello world", regular) > 0
    assert line_height_pt(regular) == pytest.approx(11.5, abs=0.1)
    assert line_height_pt(regular, 150) == pytest.approx(17.25, abs=0.2)
    assert line_height_pt(FontSpec("Calibri", 10.0)) == pytest.approx(12.2, abs=0.1)
    text = "The lockbox file arrives daily from three banks and is posted automatically."
    assert wrapped_lines(text, 400.0, regular) == 1
    assert wrapped_lines(text, 100.0, regular) > wrapped_lines(text, 200.0, regular) > 1
    assert wrapped_lines("x" * 300, 100.0, regular) > 10
    assert 40 <= line_chars(200.0, regular) <= 50


def test_capacity_is_monotonic_and_prefix_aware():
    spec = FontSpec("Arial", 10.0)
    small = capacity_chars(144.0, 72.0, spec)
    assert small > 0 and small % 10 == 0
    assert capacity_chars(288.0, 72.0, spec) > small and capacity_chars(144.0, 144.0, spec) > small
    assert capacity_chars(144.0, 72.0, spec, spacing_pct=150) < small
    assert capacity_chars(144.0, 72.0, spec, prefix_len=30) == small - 30
    assert capacity_chars(0, 72.0, spec) == 0
