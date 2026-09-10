"""Reference packs: one system's public reference architectures, fetched locally and offered to the planner when the brief is about that system."""

from __future__ import annotations

import base64
import fnmatch
import html
import json
import os
import re
import zlib
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

import yaml
from lxml import etree
from pydantic import BaseModel, Field

PACKS_DIR = Path(__file__).with_name("packs")
DEFAULT_REF_DIR = Path(__file__).resolve().parents[2] / "assets" / "refs"
GITHUB_TREE = "https://api.github.com/repos/{repo}/git/trees/{branch}?recursive=1"
GITHUB_RAW = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"
MAX_LABELS = 40
MAX_LABEL_WORDS = 6
STOP_WORDS = {
    "the", "and", "with", "for", "from", "into", "that", "this", "are", "was", "were", "via", "our", "your", "their", "its",
    "all", "any", "not", "but", "use", "using", "sap", "data", "system", "systems", "solution", "solutions", "business",
    "cloud", "service", "services", "application", "applications", "platform", "btp", "across", "between", "through",
    "how", "what", "when", "where", "which", "will", "can", "one", "two", "such", "them", "they", "then", "than",
}


class Reference(BaseModel):
    id: str
    title: str
    slug: str = ""
    tags: list[str] = Field(default_factory=list)
    summary: str = ""
    keywords: list[str] = Field(default_factory=list)


class Pack(BaseModel):
    system: str
    name: str
    site: str = ""
    repo: str = ""
    branch: str = "main"
    root: str = ""  # repository folder holding one subfolder per entry id
    keep: list[str] = Field(default_factory=lambda: ["*.drawio"])  # file names fetched from each entry folder
    detect: str = ""  # regex over the brief that turns the pack on
    entries: list[Reference] = Field(default_factory=list)

    def url(self, reference: Reference) -> str:
        return self.site + reference.slug

    def qualified(self, reference: Reference) -> str:
        return f"{self.system}:{reference.id}"

    def matches(self, text: str) -> bool:
        return bool(self.detect and re.search(self.detect, text, re.IGNORECASE))

    def find(self, ref_id: str) -> Reference | None:
        return next((r for r in self.entries if r.id == ref_id), None)


@lru_cache(maxsize=1)
def packs() -> dict[str, Pack]:
    found: dict[str, Pack] = {}
    for path in sorted(PACKS_DIR.glob("*.yaml")):
        pack = Pack.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        found[pack.system] = pack
    return found


def pack(system: str) -> Pack | None:
    return packs().get(system)


def matching(text: str) -> list[Pack]:
    """The packs whose system the text talks about."""
    return [p for p in packs().values() if p.matches(text)]


def lookup(qualified: str) -> tuple[Pack, Reference] | None:
    system, _, ref_id = str(qualified or "").partition(":")
    found = packs().get(system)
    reference = found.find(ref_id) if found is not None else None
    return (found, reference) if reference is not None else None


def ref_dir() -> Path:
    return Path(os.environ.get("SDGEN_REFS") or DEFAULT_REF_DIR)


def installed(system: str) -> set[str]:
    """Entry ids with at least one fetched diagram source."""
    found = pack(system)
    if found is None:
        return set()
    return {r.id for r in found.entries if any((ref_dir() / system / r.id).rglob("*.drawio"))}


def fetch(system: str, target: Path | None = None) -> list[Path]:
    """Downloads the diagram sources and pages of a pack from its repository into the reference folder."""
    from urllib.request import Request, urlopen

    found = pack(system)
    if found is None or not found.repo or not found.root:
        raise ValueError(f"no fetchable reference pack named '{system}'")
    folder = (target or ref_dir()) / system
    request = Request(GITHUB_TREE.format(repo=found.repo, branch=found.branch), headers={"User-Agent": "sdgen", "Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=60) as response:
        tree = json.loads(response.read().decode("utf-8")).get("tree") or []
    ids = {r.id for r in found.entries}
    fetched: list[Path] = []
    for item in tree:
        path = str(item.get("path") or "")
        if item.get("type") != "blob" or not path.startswith(found.root + "/"):
            continue
        ref_id, _, rest = path[len(found.root) + 1 :].partition("/")
        if ref_id not in ids or not rest or not any(fnmatch.fnmatch(Path(rest).name.lower(), pattern.lower()) for pattern in found.keep):
            continue
        local = folder / ref_id / Path(rest)
        local.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(Request(GITHUB_RAW.format(repo=found.repo, branch=found.branch, path=path), headers={"User-Agent": "sdgen"}), timeout=60) as response:
            local.write_bytes(response.read())
        fetched.append(local)
    return fetched


def labels(system: str, ref_id: str) -> list[str]:
    """The short texts on a fetched reference diagram, the vocabulary its author used for the blocks."""
    found: list[str] = []
    folder = ref_dir() / system / ref_id
    if not folder.is_dir():
        return found
    for path in sorted(folder.rglob("*.drawio")):
        for model in _models(path):
            for cell in model.iter("mxCell"):
                text = _plain(cell.get("value") or "")
                if text and len(text.split()) <= MAX_LABEL_WORDS and text not in found:
                    found.append(text)
                if len(found) >= MAX_LABELS:
                    return found
    return found


def candidates(found: Pack, text: str, limit: int = 3) -> list[Reference]:
    """The pack entries closest to a brief: keyword hits count double, words shared with the title and summary once."""
    lower = text.lower()
    words = _tokens(text)
    scored = []
    for reference in found.entries:
        score = 2 * sum(1 for keyword in reference.keywords if keyword.lower() in lower)
        score += len(words & _tokens(f"{reference.title} {reference.summary} {' '.join(reference.tags)}"))
        if score:
            scored.append((-score, "integration" not in reference.tags, reference.id, reference))
    scored.sort(key=lambda item: item[:3])
    return [item[3] for item in scored[:limit]]


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9/+.-]*", text.lower()) if len(w) > 2 and w not in STOP_WORDS}


def _plain(value: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value))
    return " ".join(text.split())


def _models(path: Path) -> list:
    """The mxGraphModel elements of a draw.io file, inflating compressed diagrams."""
    try:
        root = etree.fromstring(path.read_bytes())
    except (etree.XMLSyntaxError, OSError):
        return []
    models = []
    for diagram in root.iter("diagram"):
        model = diagram.find("mxGraphModel")
        if model is None and (diagram.text or "").strip():
            try:
                model = etree.fromstring(unquote(zlib.decompress(base64.b64decode(diagram.text.strip()), -15).decode("utf-8")))
            except (ValueError, zlib.error, etree.XMLSyntaxError):
                model = None
        if model is not None:
            models.append(model)
    return models
