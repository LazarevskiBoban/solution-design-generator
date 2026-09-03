from __future__ import annotations

import sys
from pathlib import Path

import click

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
