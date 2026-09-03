import os
from pathlib import Path

import pytest

from sdgen.preview import preview

POWERPOINT = Path("C:/Program Files/Microsoft Office/root/Office16/POWERPNT.EXE")


@pytest.mark.skipif(
    not POWERPOINT.exists() or os.environ.get("SDGEN_COM_TESTS") != "1",
    reason="set SDGEN_COM_TESTS=1 on a machine with PowerPoint",
)
def test_preview_opens_deck_and_exports_pdf(sample_deck, tmp_path):
    result = preview(sample_deck, tmp_path / "sample.pdf")
    assert result.opened, result.message
    assert result.slides == 2
    assert Path(result.pdf).is_file()


def test_preview_reports_unreadable_file(tmp_path):
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a deck")
    result = preview(bad)
    assert not result.opened
    assert result.message
