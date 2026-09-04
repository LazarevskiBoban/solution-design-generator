from __future__ import annotations

from pydantic import BaseModel, Field

from sdgen.analyze import Analysis, analyze_deck, slugify
from sdgen.blueprint import Blueprint, derive_blueprint
from sdgen.content import Content, load_markdown, skeleton_markdown, validate_content as _validate
from sdgen.inventory import DeckInfo, inspect_deck
from sdgen.manifest import Manifest
from sdgen.render import MissingMode, RenderIssue, render


class InspectRequest(BaseModel):
    deck: str


class InspectResponse(BaseModel):
    deck: DeckInfo


def inspect_template(request: InspectRequest) -> InspectResponse:
    return InspectResponse(deck=inspect_deck(request.deck))


class AnalyzeRequest(BaseModel):
    deck: str
    name: str | None = None


class AnalyzeResponse(BaseModel):
    analysis: Analysis
    manifest: Manifest
    blueprint: Blueprint
    deck: DeckInfo


def analyze_template(request: AnalyzeRequest) -> AnalyzeResponse:
    deck = inspect_deck(request.deck)
    analysis = analyze_deck(deck)
    name = request.name or slugify(deck.path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].rsplit(".", 1)[0])
    manifest = analysis.to_manifest(name, source=deck.path)
    blueprint = derive_blueprint(deck, analysis, manifest, name)
    return AnalyzeResponse(analysis=analysis, manifest=manifest, blueprint=blueprint, deck=deck)


class OutlineRequest(BaseModel):
    deck: DeckInfo
    analysis: Analysis
    manifest: Manifest
    name: str


class OutlineResponse(BaseModel):
    blueprint: Blueprint


def outline_template(request: OutlineRequest) -> OutlineResponse:
    return OutlineResponse(blueprint=derive_blueprint(request.deck, request.analysis, request.manifest, request.name))


class SkeletonRequest(BaseModel):
    manifest: Manifest


class SkeletonResponse(BaseModel):
    markdown: str


def content_skeleton(request: SkeletonRequest) -> SkeletonResponse:
    return SkeletonResponse(markdown=skeleton_markdown(request.manifest))


class ValidateRequest(BaseModel):
    manifest: Manifest
    markdown: str
    base_dir: str | None = None


class ValidateResponse(BaseModel):
    content: Content
    warnings: list[str] = Field(default_factory=list)


def validate_content(request: ValidateRequest) -> ValidateResponse:
    content = load_markdown(request.markdown, request.manifest, base_dir=request.base_dir)
    return ValidateResponse(content=content, warnings=_validate(content, request.manifest))


class RenderRequest(BaseModel):
    template: str
    manifest: Manifest
    content: Content
    output: str
    missing: MissingMode = "placeholder"
    continue_on: list[int] | None = None


class RenderResponse(BaseModel):
    output: str
    slides: int
    issues: list[RenderIssue] = Field(default_factory=list)


def continuation_slides(blueprint: Blueprint | None) -> list[int] | None:
    if blueprint is None:
        return None
    return [s.slide for s in blueprint.sections if s.kind == "text"]


def render_document(request: RenderRequest) -> RenderResponse:
    result = render(request.template, request.manifest, request.content, request.output, request.missing, request.continue_on)
    return RenderResponse(output=result.output, slides=result.slides, issues=result.issues)
