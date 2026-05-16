"""
pitdb CLI — entry point for the pitdb command.

Commands:
    pitdb serve   — start the MCP server
    pitdb ingest  — ingest data for a ticker
    pitdb query   — run a point-in-time query from the terminal
"""

import click

from .connection import PitDB


@click.group()
def cli():
    """pitdb — point-in-time correct financial data for AI agents."""


@cli.command()
@click.option("--data-dir", default=None, help="Path to pitdb data directory (default: ~/.pitdb/data)")
def serve(data_dir):
    """Start the pitdb MCP server (stdio transport for Claude Desktop)."""
    db = PitDB.local(data_dir=data_dir)
    click.echo(f"pitdb MCP server starting (data: {db._data_dir})")
    from .mcp.server import serve as _serve
    _serve(db)


@cli.command()
@click.argument("ticker")
@click.option("--start", required=True, help="Start date (YYYY-MM-DD)")
@click.option("--end", default=None, help="End date (YYYY-MM-DD, default: today)")
@click.option("--data-dir", default=None, help="Path to pitdb data directory")
@click.option("--save/--no-save", default=True, show_default=True, help="Persist data to disk after ingest")
def ingest(ticker, start, end, data_dir, save):
    """Ingest prices, corporate actions, fundamentals, and earnings history for TICKER."""
    from .ingest import ingest as _ingest

    db = PitDB.local(data_dir=data_dir)
    click.echo(f"Ingesting {ticker} from {start}{' to ' + end if end else ''} ...")
    _ingest(db, ticker, start, end)
    if save:
        db.save()
        click.echo(f"Saved to {db._data_dir}")
    click.echo("Done.")


@cli.command()
@click.argument("ticker")
@click.option("--as-of", required=True, help="Knowledge date (YYYY-MM-DD)")
@click.option("--start", default=None, help="Price history start date")
@click.option("--end", default=None, help="Price history end date")
@click.option("--data-dir", default=None)
def query(ticker, as_of, start, end, data_dir):
    """Print a context pack for TICKER as known at AS_OF."""
    import json
    db = PitDB.local(data_dir=data_dir)
    if start and end:
        result = db.get_price_history(ticker, start, end, as_of)
        click.echo(result.to_string())
    else:
        result = db.get_context_pack(ticker, as_of)
        click.echo(json.dumps(result, indent=2, default=str))
