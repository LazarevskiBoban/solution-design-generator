import os
from pathlib import Path

import pytest

from sdgen import icons

POWERPOINT = Path("C:/Program Files/Microsoft Office/root/Office16/POWERPNT.EXE")


def test_every_key_has_a_file_from_the_sap_repository():
    for icon in icons.catalogue().values():
        assert icon.file.endswith("_sd.svg") or (icon.file.startswith("generic-") and icon.file.endswith(("-sap.svg", "-nonsap.svg"))), icon.key


def test_sap_icons_are_told_apart_by_file_name():
    entries = icons.catalogue()
    assert entries["s4hana"].sap and entries["btp_integration_suite"].sap and not entries["bank"].sap
    assert not icons.Icon(key="x", label="X", file="generic-cloud-nonsap.svg").sap and not icons.Icon(key="y", label="Y").sap


def test_library_svgs_are_named_after_the_catalogue():
    import base64
    import json

    svg = base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'/>").decode("ascii")
    items = [
        {"title": "Cloud SAP Size M", "data": "data:image/svg+xml;base64," + svg},
        {"title": "Cloud Non-SAP Size M", "data": "data:image/svg+xml;base64," + svg},
        {"title": "Paper-Plane Highlight Size M", "data": "data:image/svg+xml;base64," + svg},
        {"title": "Legend", "xml": "..."},
    ]
    found = icons.library_svgs("<mxlibrary>" + json.dumps(items) + "</mxlibrary>")
    assert set(found) == {"generic-cloud-sap.svg", "generic-cloud-nonsap.svg", "generic-paper-plane-highlight.svg"}
    assert found["generic-cloud-sap.svg"].startswith(b"<svg")
    assert icons.library_svgs("no library here") == {}


def test_catalogue_and_lookup(tmp_path, monkeypatch):
    keys = icons.icon_keys()
    assert "s4hana" in keys and "btp_integration_suite" in keys and "generic" in keys
    monkeypatch.setenv("SDGEN_ICONS", str(tmp_path))
    assert icons.icon_file("btp_integration_suite") is None and icons.icon_png("btp_integration_suite") is None
    svg = tmp_path / icons.catalogue()["btp_integration_suite"].file
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    assert icons.icon_file("btp_integration_suite") == svg and icons.icon_file("s4hana") is None
    assert icons.catalogue()["s4hana"].file.startswith("generic-") and icons.catalogue()["bank"].file.endswith("-nonsap.svg")
    assert icons.icon_png("btp_integration_suite") is None and icons.installed_keys() == []
    (tmp_path / "png").mkdir()
    (tmp_path / "png" / (svg.stem + ".png")).write_bytes(b"png")
    assert icons.installed_keys() == ["btp_integration_suite"]


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
