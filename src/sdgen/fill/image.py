from __future__ import annotations

from pathlib import Path


def replace_picture(slide, picture, path: str | Path, fit: str = "contain"):
    if hasattr(picture, "insert_picture"):
        return picture.insert_picture(str(path))

    left, top, box_w, box_h = picture.left, picture.top, picture.width, picture.height
    old_element = picture._element
    old_rid = old_element.blip_rId
    name = picture.name

    new = slide.shapes.add_picture(str(path), left, top)
    image_w, image_h = new.width, new.height
    if fit == "cover":
        new.width, new.height = box_w, box_h
        _crop_to_aspect(new, image_w / image_h, box_w / box_h)
    else:
        scale = min(box_w / image_w, box_h / image_h)
        new.width = int(image_w * scale)
        new.height = int(image_h * scale)
        new.left = left + (box_w - new.width) // 2
        new.top = top + (box_h - new.height) // 2

    new.name = name
    old_element.addprevious(new._element)
    old_element.getparent().remove(old_element)
    if old_rid:
        slide.part.drop_rel(old_rid)
    return new


def _crop_to_aspect(picture, image_aspect: float, box_aspect: float) -> None:
    if image_aspect > box_aspect:
        visible = box_aspect / image_aspect
        picture.crop_left = picture.crop_right = (1 - visible) / 2
    elif image_aspect < box_aspect:
        visible = image_aspect / box_aspect
        picture.crop_top = picture.crop_bottom = (1 - visible) / 2
