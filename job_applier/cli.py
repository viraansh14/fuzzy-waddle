"""CLI entry point for the job application automation tool."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import click

from job_applier.config import load_config
from job_applier.utils.logging import setup_logging


@click.group()
def cli():
    """fuzzy-waddle — Automated job application tool."""


@cli.command()
@click.option(
    "--config", "-c", default="config.yaml", show_default=True,
    type=click.Path(), help="Path to config file."
)
@click.option(
    "--cv", "-r", type=click.Path(exists=True),
    help="Path to CV file (overrides config.cv.path)."
)
@click.option(
    "--platform", "-p",
    type=click.Choice(["linkedin", "indeed", "glassdoor", "generic"]),
    help="Run only this platform."
)
@click.option("--dry-run", is_flag=True, help="Fill forms but do NOT submit.")
@click.option("--limit", "-n", type=int, help="Override max_applications_per_run.")
def apply(config, cv, platform, dry_run, limit):
    """Search for jobs and apply automatically."""
    cfg = load_config(config)

    if cv:
        cfg.cv.path = cv
    if dry_run:
        cfg.behavior.dry_run = True
    if limit:
        cfg.search.max_applications_per_run = limit

    # Disable all platforms except the chosen one
    if platform:
        cfg.platforms.linkedin.enabled = platform == "linkedin"
        cfg.platforms.indeed.enabled = platform == "indeed"
        cfg.platforms.glassdoor.enabled = platform == "glassdoor"
        cfg.platforms.generic_urls.enabled = platform == "generic"

    setup_logging(cfg.logging.level, cfg.logging.log_file)

    _check_api_key(cfg)

    from job_applier.orchestrator import Orchestrator
    asyncio.run(Orchestrator(cfg).run())


@cli.command()
@click.option(
    "--config", "-c", default="config.yaml", show_default=True,
    type=click.Path(), help="Path to config file."
)
@click.argument(
    "platform",
    type=click.Choice(["linkedin", "indeed", "glassdoor"])
)
def login(config, platform):
    """Open a browser window for manual login and save session cookies.

    Run this once per platform before using 'apply'.
    """
    cfg = load_config(config)
    setup_logging(cfg.logging.level)

    from job_applier.browser.auth import AuthManager
    from job_applier.browser.session import BrowserSession

    async def _do_login():
        # Force headed mode for manual login
        cfg.browser.headless = False
        async with BrowserSession(cfg.browser, cfg.behavior) as session:
            auth = AuthManager(cfg.auth.cookie_dir)
            # Delete existing cookies to force re-login
            cookie_path = Path(cfg.auth.cookie_dir) / f"{platform}.json"
            if cookie_path.exists():
                cookie_path.unlink()
            await auth.ensure_logged_in(platform, session)
            click.echo(f"Login for {platform} successful. Cookies saved.")

    asyncio.run(_do_login())


@cli.command("parse-cv")
@click.argument("cv_path", type=click.Path(exists=True))
@click.option("--api-key", envvar="ANTHROPIC_API_KEY", help="Anthropic API key.")
@click.option(
    "--model", default="claude-opus-4-6", show_default=True,
    help="Claude model to use for extraction."
)
def parse_cv(cv_path, api_key, model):
    """Parse a CV file and print extracted structured data.

    Useful for verifying the tool understands your CV before running 'apply'.
    """
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise click.ClickException(
            "Anthropic API key required. Set ANTHROPIC_API_KEY or pass --api-key."
        )

    import anthropic as _anthropic
    from job_applier.cv.extractor import CVExtractor
    from job_applier.cv.parser import extract_text

    raw = extract_text(cv_path)
    click.echo(f"Extracted {len(raw)} characters from CV.\n")

    client = _anthropic.Anthropic(api_key=api_key)
    extractor = CVExtractor(client, model)
    profile = extractor.extract(raw)

    click.echo(profile.to_context_string())


@cli.command()
@click.option(
    "--config", "-c", default="config.yaml", show_default=True,
    type=click.Path(), help="Path to config file."
)
def status(config):
    """Show a summary of past applications."""
    cfg = load_config(config)

    state_path = Path(cfg.behavior.state_file)
    log_path = Path(cfg.logging.log_file)

    if state_path.exists():
        with open(state_path) as f:
            state = json.load(f)
        click.echo(f"Total jobs applied to (all time): {len(state.get('applied_ids', []))}")
    else:
        click.echo("No applications recorded yet.")

    if log_path.exists():
        stats: dict[str, int] = {}
        with open(log_path) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    s = entry.get("status", "unknown")
                    stats[s] = stats.get(s, 0) + 1
                except Exception:
                    continue
        click.echo("\nBreakdown by status:")
        for status_name, count in sorted(stats.items()):
            click.echo(f"  {status_name}: {count}")


def _check_api_key(cfg) -> None:
    key = cfg.anthropic.api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise click.ClickException(
            "Anthropic API key not found.\n"
            "Set ANTHROPIC_API_KEY environment variable or add it to config.yaml."
        )
    cfg.anthropic.api_key = key


if __name__ == "__main__":
    cli()
