import os
from pathlib import Path

import pytest

from sdgen import icons

POWERPOINT = Path("C:/Program Files/Microsoft Office/root/Office16/POWERPNT.EXE")


def test_catalogue_and_lookup(tmp_path, monkeypatch):
    keys = icons.icon_keys()
    assert "s4hana" in keys and "btp_integration_suite" in keys and "generic" in keys
    monkeypatch.setenv("SDGEN_ICONS", str(tmp_path))
    assert icons.icon_file("btp_integration_suite") is None and icons.icon_png("btp_integration_suite") is None
    svg = tmp_path / icons.catalogue()["btp_integration_suite"].file
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    assert icons.icon_file("btp_integration_suite") == svg and icons.icon_file("s4hana") is None
    assert icons.icon_png("btp_integration_suite") is None


@pytest.mark.skipif(
    not POWERPOINT.exists() or os.environ.get("SDGEN_COM_TESTS") != "1",
    reason="set SDGEN_COM_TESTS=1 on a machine with PowerPoint",
)
def test_build_pngs_through_powerpoint(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setenv("SDGEN_ICONS", str(tmp_path))
    svg = tmp_path / icons.catalogue()["cloud_connector"].file
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><circle cx="32" cy="32" r="30" fill="#0070f2"/></svg>', encoding="utf-8")
    built = icons.build_pngs(["cloud_connector"])
    assert built and built[0].is_file() and icons.icon_png("cloud_connector") == built[0]
    assert Image.open(built[0]).size[0] > 0
