from urllib.error import HTTPError

import pytest

from sdgen.material import IMAGE_BYTES_MAX, PAGE_MAX, describe_image, fetch_link, html_to_text, load_material, material_corpus, material_text, mime_of, new_material, save_material, text_from_upload


def _items():
    return [
        new_material("text", title="Folder layout", tags=["flows"], text="reception/ emission/ tmp/"),
        new_material("link", title="ISO 20022", url="https://example.org/iso", text="Page text"),
        new_material("image", title="Landscape", file="ab12-landscape.png", tags=["overview"]),
        new_material("text", title="Notes", note="from the workshop", text="x" * 50),
    ]


def test_material_text_lists_tagged_first_and_caps():
    items = _items()
    block = material_text(items, tags={"flows"})
    heads = [line for line in block.splitlines() if line.startswith("## ")]
    assert heads[0] == "## Folder layout (text; for: flows)" and "Landscape" not in block
    assert heads[1] == "## ISO 20022 (link, https://example.org/iso)" and "Note: from the workshop" in block
    everything = material_text(items)
    assert "## Landscape (image, ab12-landscape.png; for: overview)\n(no text yet: nothing attached)" in everything
    assert material_text([]) == ""
    long = [new_material("text", title=f"T{i}", text="word " * 400) for i in range(4)]
    cut = material_text(long, per_item=100, total=350)
    assert "[... truncated, " in cut and cut.count("## T") == 2 and cut.endswith("[... 2 more item(s) omitted]")
    assert material_corpus(items).startswith("reception/ emission/ tmp/\nPage text")


def test_material_yaml_roundtrip_and_empty_file_removed(tmp_path):
    path = tmp_path / "material.yaml"
    items = _items()
    save_material(items, path)
    assert load_material(path) == items and path.read_text(encoding="utf-8").startswith("items:")
    save_material([], path)
    assert not path.exists() and load_material(path) == []


def test_text_from_upload_reads_drawio_csv_and_caps():
    drawio = b'<mxfile><diagram id="d" name="n"><mxGraphModel><root><mxCell id="1" value="SAP S/4HANA" vertex="1"/><mxCell id="2" value="&lt;b&gt;Cloud Connector&lt;/b&gt;" vertex="1"/><mxCell id="3" value="SAP S/4HANA" vertex="1"/></root></mxGraphModel></diagram></mxfile>'
    assert text_from_upload("landscape.drawio", drawio) == "SAP S/4HANA\nCloud Connector"
    assert text_from_upload("banks.csv", b"bank,lockbox\nBoA,yes\n") == "bank,lockbox\nBoA,yes\n"
    big = text_from_upload("notes.md", b"a" * 200_010)
    assert big.endswith("[... truncated, 10 more characters]") and len(big) < 200_100
    assert mime_of("x.PNG") == "image/png" and mime_of("y.jpg") == "image/jpeg" and mime_of("z.gif") == ""


def test_describe_image_sends_the_picture_and_the_hint():
    class Seeing:
        name = "fake"

        def complete(self, system, user, images=None):
            self.system, self.user, self.images = system, user, images
            return "  Boxes: A, B. Arrow A -> B: file.  "

        def complete_json(self, system, user, schema, name="result", images=None):
            return {}

    llm = Seeing()
    assert describe_image(llm, b"\x89PNG", "image/png", hint="current landscape") == "Boxes: A, B. Arrow A -> B: file."
    assert llm.images == [(b"\x89PNG", "image/png")] and "invent nothing" in llm.system
    assert "Context from the author: current landscape" in llm.user and "[unreadable]" in llm.user
    with pytest.raises(ValueError, match="PNG or JPEG"):
        describe_image(llm, b"gif", "image/gif")
    with pytest.raises(ValueError, match="MB"):
        describe_image(llm, b"0" * (IMAGE_BYTES_MAX + 1), "image/png")


def test_html_to_text_drops_scripts_and_keeps_blocks():
    page = "<html><head><title> ISO   20022 </title><style>p{}</style></head><body><script>var x=1;</script><h1>Heading</h1><p>First <b>para</b>.</p><ul><li>one</li><li>two</li></ul><table><tr><td>A</td><td>B</td></tr></table><noscript>no</noscript></body></html>"
    title, text = html_to_text(page)
    assert title == "ISO 20022" and text.splitlines() == ["Heading", "First para.", "one", "two", "A B"] and "var x" not in text


def _fake_response(body: bytes, content_type: str = "text/html; charset=utf-8", charset: str = "utf-8"):
    class Headers:
        def get(self, key, default=None):
            return content_type if key.lower() == "content-type" else default

        def get_content_charset(self):
            return charset

    class Response:
        headers = Headers()

        def read(self, n=-1):
            return body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return Response()


def test_fetch_link_extracts_readable_text_and_reports_failures():
    seen = {}

    def opener(request, timeout=0):
        seen.update(ua=request.get_header("User-agent"), url=request.full_url, timeout=timeout)
        return _fake_response(b"<html><head><title>Page</title></head><body><p>Hello 12345</p></body></html>")

    title, text, status = fetch_link(" https://example.org/x ", opener=opener)
    assert (title, text) == ("Page", "Hello 12345") and status.startswith("fetched 20") and seen == {"ua": "sdgen", "url": "https://example.org/x", "timeout": 30}
    plain = fetch_link("https://example.org/t", opener=lambda r, timeout: _fake_response(b"just text\n", "text/plain"))
    assert plain[:2] == ("", "just text")
    long = fetch_link("https://example.org/l", opener=lambda r, timeout: _fake_response(b"<p>" + b"a" * (PAGE_MAX + 5) + b"</p>"))
    assert long[1].endswith("[... truncated, 5 more characters]")
    assert fetch_link("ftp://example.org")[2] == "fetch failed (not an http(s) link): paste an excerpt"
    assert fetch_link("https://example.org/pdf", opener=lambda r, timeout: _fake_response(b"%PDF", "application/pdf"))[2] == "fetch failed (not a text page): paste an excerpt"

    def forbidden(request, timeout=0):
        raise HTTPError(request.full_url, 403, "Forbidden", None, None)

    assert fetch_link("https://me.sap.com/notes/1", opener=forbidden)[2] == "fetch failed (403): paste an excerpt"

    def slow(request, timeout=0):
        raise TimeoutError("timed out")

    assert fetch_link("https://example.org/slow", opener=slow)[2] == "fetch failed (timed out): paste an excerpt"
