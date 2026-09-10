"""The colours both diagram outputs share: a neutral base plus the SAP accent, as the SAP Architecture Center draws them."""

from __future__ import annotations

from pptx.dml.color import RGBColor

# Neutral base for everything that is not SAP
SLATE = "475E75"
GREY_FILL = "F5F6F7"
EDGE = "475E74"
SUBTITLE = "595959"
TEXT = "202020"
WHITE = "FFFFFF"

# SAP accent
SAP_BLUE = "0070F2"
SAP_FILL = "EBF8FF"
SAP_DARK = "002A86"


def rgb(hex6: str) -> RGBColor:
    return RGBColor.from_string(hex6)


def css(hex6: str) -> str:
    return "#" + hex6
