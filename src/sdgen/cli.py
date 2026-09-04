from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from sdgen.analyze import analyze_deck, format_analysis, slugify
from sdgen.blueprint import derive_blueprint, format_outline
from sdgen.brief import brief_skeleton, load_brief
from sdgen.inventory import format_inventory, inspect_deck
from sdgen.llm import LLMError, LLMNotConfigured, get_llm
from sdgen.mapping.extract import extract_fields
from sdgen.mapping.model import MappingSet, SourceSpec, TargetSpec
from sdgen.mapping.workbook import write_workbook
from sdgen.writer import draft_content
from sdgen.manifest import Manifest
from sdgen.preview import preview as run_preview
from sdgen.registry import MANIFEST_FILE, Registry, TemplateEntry
from sdgen.tools import (
    RenderRequest,
    SkeletonRequest,
    ValidateRequest,
    content_skeleton,
    continuation_slides,
    render_document,
    validate_content,
)

TEMPLATES_OPTION = click.option(
    "--templates",
    default="templates",
    show_default=True,
    type=click.Path(file_okay=False, path_type=Path),
    help="Template registry folder.",
)


@click.group()
def main() -> None:
    """Fill Office templates for solution-design documents."""
    # Windows consoles often default to a legacy code page; never crash on deck text.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")


@main.command()
@click.argument("deck", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--slide", "slides", type=int, multiple=True, help="Only these slide numbers (1-based).")
@click.option("--json", "as_json", is_flag=True, help="Emit the inventory as JSON.")
def inspect(deck: Path, slides: tuple[int, ...], as_json: bool) -> None:
    """List slides, shapes, tables and text of a deck."""
    info = inspect_deck(deck)
    if slides:
        info = info.model_copy(update={"slides": [s for s in info.slides if s.index in slides]})
    click.echo(info.model_dump_json(indent=2) if as_json else format_inventory(info))


@main.command()
@click.argument("deck", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path), help="Write the proposed manifest YAML here.")
@click.option("--name", default=None, help="Template name for the manifest (default: deck file name).")
@click.option("--all", "show_all", is_flag=True, help="Also list candidates left unticked.")
@click.option("--json", "as_json", is_flag=True, help="Emit the full analysis as JSON.")
def analyze(deck: Path, output: Path | None, name: str | None, show_all: bool, as_json: bool) -> None:
    """Propose fillable fields for a deck and optionally write a manifest."""
    analysis = analyze_deck(inspect_deck(deck))
    if as_json:
        click.echo(analysis.model_dump_json(indent=2))
        return
    click.echo(format_analysis(analysis, show_all))
    if output:
        manifest = analysis.to_manifest(name or slugify(deck.stem), source=deck.name)
        manifest.save(output)
        click.echo(f"\nManifest with {len(manifest.fields)} fields written to {output}")


@main.command()
@click.argument("target")
@TEMPLATES_OPTION
def outline(target: str, templates: Path) -> None:
    """Show the sections of a deck or of a registered template."""
    path = Path(target)
    if path.is_file() and path.suffix.lower() == ".pptx":
        deck = inspect_deck(path)
        analysis = analyze_deck(deck)
        manifest = analysis.to_manifest(slugify(path.stem), source=path.name)
        blueprint = derive_blueprint(deck, analysis, manifest, slugify(path.stem))
    else:
        entry = _resolve_template(target, templates)
        if entry.blueprint is None:
            click.echo(f"template '{entry.name}' has no outline; re-add it to create one")
            raise SystemExit(1)
        blueprint = entry.blueprint
    click.echo(format_outline(blueprint))


@main.command()
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path), help="Write the brief skeleton here instead of printing it.")
def brief(output: Path | None) -> None:
    """Emit an empty brief file (what the writer needs from you)."""
    text = brief_skeleton()
    if output:
        output.write_text(text, encoding="utf-8")
        click.echo(f"Brief skeleton written to {output}")
    else:
        click.echo(text)


@main.command()
@click.argument("template")
@click.argument("brief_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Content file to write.")
@click.option("--llm", "llm_name", default=None, help="Provider: mock (default), azure or openai; or SDGEN_LLM.")
@click.option("--model", default=None, help="Model, or the deployment name on Azure.")
@TEMPLATES_OPTION
def draft(template: str, brief_file: Path, output: Path, llm_name: str | None, model: str | None, templates: Path) -> None:
    """Write the sections of a template from a brief."""
    entry = _resolve_template(template, templates)
    if entry.blueprint is None:
        click.echo(f"template '{entry.name}' has no outline; re-add it to create one")
        raise SystemExit(1)
    try:
        llm = get_llm(llm_name, model=model)
        result = draft_content(load_brief(brief_file.read_text(encoding="utf-8")), entry.blueprint, entry.manifest, llm)
    except (LLMNotConfigured, LLMError) as exc:
        click.echo(str(exc))
        raise SystemExit(1)
    output.write_text(result.markdown, encoding="utf-8")
    for warning in result.warnings:
        click.echo(f"warning: {warning}")
    click.echo(f"Draft from '{result.llm}' with {len(result.content.fields)} sections written to {output}")


@main.command()
@click.argument("template")
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path), help="Write the skeleton here instead of printing it.")
@TEMPLATES_OPTION
def skeleton(template: str, output: Path | None, templates: Path) -> None:
    """Emit an empty Markdown content file for a template."""
    entry = _resolve_template(template, templates)
    markdown = content_skeleton(SkeletonRequest(manifest=entry.manifest)).markdown
    if output:
        output.write_text(markdown, encoding="utf-8")
        click.echo(f"Skeleton with {len(entry.manifest.fields)} fields written to {output}")
    else:
        click.echo(markdown)


@main.command()
@click.argument("template")
@click.argument("content", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Output document path.")
@click.option(
    "--missing",
    type=click.Choice(["placeholder", "keep", "blank"]),
    default="placeholder",
    show_default=True,
    help="What to show for sections without content.",
)
@TEMPLATES_OPTION
def render(template: str, content: Path, output: Path, missing: str, templates: Path) -> None:
    """Fill a template with a Markdown content file."""
    entry = _resolve_template(template, templates)
    validation = validate_content(
        ValidateRequest(manifest=entry.manifest, markdown=content.read_text(encoding="utf-8"), base_dir=str(content.parent))
    )
    for warning in validation.warnings:
        click.echo(f"warning: {warning}")
    result = render_document(
        RenderRequest(
            template=str(entry.template_path),
            manifest=entry.manifest,
            content=validation.content,
            output=str(output),
            missing=missing,
            continue_on=continuation_slides(entry.blueprint),
        )
    )
    for issue in result.issues:
        click.echo(str(issue))
    click.echo(f"Wrote {result.output} ({result.slides} slides)")
    if any(i.level == "error" for i in result.issues):
        raise SystemExit(1)


@main.command()
@click.argument("name")
@click.argument("deck", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--manifest", "manifest_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Confirmed manifest; defaults to the analyzer's proposal.")
@click.option("--keep-content", is_flag=True, help="Store the deck as uploaded instead of replacing fields with placeholders.")
@TEMPLATES_OPTION
def add(name: str, deck: Path, manifest_path: Path | None, keep_content: bool, templates: Path) -> None:
    """Register a deck as a template."""
    deck_info = inspect_deck(deck)
    analysis = analyze_deck(deck_info)
    manifest = Manifest.load(manifest_path) if manifest_path else analysis.to_manifest(name)
    blueprint = derive_blueprint(deck_info, analysis, manifest, name)
    entry = Registry(templates).add(name, deck, manifest, tokenize=not keep_content, blueprint=blueprint)
    click.echo(f"Template '{entry.name}' saved with {len(entry.manifest.fields)} fields in {entry.directory}")


@main.command()
@click.argument("document", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--pdf", type=click.Path(dir_okay=False, path_type=Path), help="Also export a PDF to this path.")
def preview(document: Path, pdf: Path | None) -> None:
    """Open a generated deck in PowerPoint to check it, optionally exporting a PDF."""
    result = run_preview(document, pdf)
    if not result.opened:
        click.echo(f"PowerPoint could not open {document}: {result.message}")
        raise SystemExit(1)
    click.echo(f"{document}: {result.slides} slides, {result.message}")
    if result.pdf:
        click.echo(f"PDF written to {result.pdf}")


@main.command()
def ui() -> None:
    """Start the browser UI."""
    app = Path(__file__).resolve().parents[2] / "ui" / "app.py"
    if not app.is_file():
        click.echo(f"UI script not found at {app}")
        raise SystemExit(1)
    raise SystemExit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)]))


@main.group()
def mapping() -> None:
    """Field extraction and mapping workbooks."""


@mapping.command("extract")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--kind", type=click.Choice(["auto", "xml", "json", "csv", "xsd", "edmx"]), default="auto", show_default=True)
def mapping_extract(file: Path, kind: str) -> None:
    """List the fields found in a sample file or schema."""
    detected, fields = extract_fields(file, kind)
    click.echo(f"{file.name}: {detected}, {len(fields)} fields")
    for f in fields:
        flags = []
        if f.required:
            flags.append("required")
        if f.repeating:
            flags.append("repeating")
        detail = " ".join(x for x in (f.type, f"x{f.occurs}" if f.occurs > 1 else "", " ".join(flags)) if x)
        example = f"  e.g. {f.example[:40]}" if f.example else ""
        click.echo(f"  {f.path}  [{detail}]{example}")


@mapping.command("new")
@click.argument("name")
@click.option("--target", "target_file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Target API definition or sample payload.")
@click.option("--source", "sources", multiple=True, help="Source sample as name=file; repeat for several sources.")
@click.option("-o", "--output", required=True, type=click.Path(dir_okay=False, path_type=Path), help="mappings.yaml to write.")
def mapping_new(name: str, target_file: Path, sources: tuple[str, ...], output: Path) -> None:
    """Create a mapping set from a target definition and source samples."""
    kind, fields = extract_fields(target_file)
    mapping_set = MappingSet(name=name, target=TargetSpec(name=target_file.stem, file=target_file.name, kind=kind, fields=fields))
    for item in sources:
        source_name, _, source_file = item.partition("=")
        if not source_file:
            source_name, source_file = Path(item).stem, item
        source_kind, source_fields = extract_fields(Path(source_file))
        mapping_set.sources.append(SourceSpec(name=source_name, file=Path(source_file).name, kind=source_kind, fields=source_fields))
    mapping_set.save(output)
    click.echo(f"Mapping '{name}' with {len(fields)} target fields and {len(mapping_set.sources)} source(s) written to {output}")


@mapping.command("workbook")
@click.argument("mappings", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("-o", "--output", required=True, type=click.Path(dir_okay=False, path_type=Path), help="Excel file to write.")
def mapping_workbook(mappings: Path, output: Path) -> None:
    """Write the Excel mapping workbook for a mapping set."""
    mapping_set = MappingSet.load(mappings)
    write_workbook(mapping_set, output)
    click.echo(mapping_set.summary_text())
    click.echo(f"Workbook written to {output}")


def _resolve_template(template: str, templates: Path) -> TemplateEntry:
    path = Path(template)
    if path.is_dir() and (path / MANIFEST_FILE).is_file():
        return Registry(path.parent).load(path.name)
    if path.is_file() and path.suffix.lower() in (".yaml", ".yml"):
        manifest = Manifest.load(path)
        return TemplateEntry(name=manifest.name, directory=path.parent, manifest=manifest)
    return Registry(templates).load(template)
