from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from sdgen.analyze import analyze_deck, format_analysis, slugify
from sdgen.inventory import format_inventory, inspect_deck
from sdgen.manifest import Manifest
from sdgen.preview import preview as run_preview
from sdgen.registry import MANIFEST_FILE, Registry, TemplateEntry
from sdgen.tools import (
    RenderRequest,
    SkeletonRequest,
    ValidateRequest,
    content_skeleton,
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
@click.option("--blank-missing", is_flag=True, help="Clear fields that have no value instead of keeping template text.")
@TEMPLATES_OPTION
def render(template: str, content: Path, output: Path, blank_missing: bool, templates: Path) -> None:
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
            blank_missing=blank_missing,
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
    manifest = Manifest.load(manifest_path) if manifest_path else analyze_deck(inspect_deck(deck)).to_manifest(name)
    entry = Registry(templates).add(name, deck, manifest, tokenize=not keep_content)
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


def _resolve_template(template: str, templates: Path) -> TemplateEntry:
    path = Path(template)
    if path.is_dir() and (path / MANIFEST_FILE).is_file():
        return Registry(path.parent).load(path.name)
    if path.is_file() and path.suffix.lower() in (".yaml", ".yml"):
        manifest = Manifest.load(path)
        return TemplateEntry(name=manifest.name, directory=path.parent, manifest=manifest)
    return Registry(templates).load(template)
