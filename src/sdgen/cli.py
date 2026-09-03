from __future__ import annotations

import sys
from pathlib import Path

import click

from sdgen.analyze import analyze_deck, format_analysis, slugify
from sdgen.inventory import format_inventory, inspect_deck


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
