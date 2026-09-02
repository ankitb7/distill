import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from distill.config import get_db_path, get_output_dir, load_config
from distill.db import Database

app = typer.Typer(name="distill", help="AI article curation & weekly podcast generator")
console = Console()

PROJECT_DIR = Path(__file__).parent.parent.parent
LAUNCHD_DIR = PROJECT_DIR / "launchd"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


def _get_db(config: dict | None = None) -> Database:
    config = config or load_config()
    return Database(get_db_path(config))


def _install_launchd():
    uv_path = shutil.which("uv") or "/usr/local/bin/uv"
    project_dir = str(PROJECT_DIR.resolve())
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    for plist in LAUNCHD_DIR.glob("*.plist"):
        content = plist.read_text()
        content = content.replace("__UV_PATH__", uv_path)
        content = content.replace("__PROJECT_DIR__", project_dir)
        dest = LAUNCH_AGENTS_DIR / plist.name
        dest.write_text(content)
        label = plist.stem
        subprocess.run(["launchctl", "unload", str(dest)], capture_output=True)
        subprocess.run(["launchctl", "load", str(dest)], check=True)
        console.print(f"  Installed [cyan]{label}[/cyan] → {dest}")


@app.command()
def init(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    install_launchd: Annotated[bool, typer.Option("--install-launchd")] = False,
):
    """Initialize database and verify config."""
    config = load_config(config_path)
    db = _get_db(config)
    db.init_schema()
    db.close()
    console.print("[green]Database initialized.[/green]")

    sources = config.get("sources", {})
    enabled = [s for s, c in sources.items() if c.get("enabled")]
    console.print(f"Enabled sources: {', '.join(enabled)}")

    if install_launchd:
        _install_launchd()


@app.command()
def collect(
    source: Annotated[str | None, typer.Option("--source")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
):
    """Fetch articles from configured sources."""
    config = load_config(config_path)
    db = _get_db(config)
    db.init_schema()

    from distill.processing.pipeline import collect_articles

    result = asyncio.run(collect_articles(db, config, source_filter=source))
    db.close()
    for source_result in result.sources:
        if source_result.error:
            console.print(f"  [red]{source_result.source}: {source_result.error}[/red]")
        else:
            console.print(
                f"  {source_result.source}: {source_result.found} found, "
                f"{source_result.inserted} new"
            )
    console.print(f"[green]Collected {result.inserted} new articles.[/green]")


@app.command()
def extract(
    limit: Annotated[int, typer.Option("--limit")] = 50,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
):
    """Extract article content from URLs."""
    config = load_config(config_path)
    db = _get_db(config)

    from distill.processing.extractor import extract_content

    count = asyncio.run(extract_content(db, limit=limit))
    db.close()
    console.print(f"[green]Extracted content for {count} articles.[/green]")


@app.command()
def dedup(
    embeddings: Annotated[bool, typer.Option("--embeddings")] = False,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
):
    """Run deduplication on articles."""
    config = load_config(config_path)
    db = _get_db(config)
    dedup_config = config.get("dedup", {})

    from distill.processing.dedup import run_title_dedup

    threshold = dedup_config.get("title_similarity_threshold", 0.85)
    count = run_title_dedup(db, threshold=threshold)
    console.print(f"  Title dedup: {count} duplicates")

    if embeddings:
        from distill.processing.dedup import run_embedding_dedup

        emb_model = dedup_config.get("embedding_model", "all-MiniLM-L6-v2")
        emb_threshold = dedup_config.get("embedding_similarity_threshold", 0.88)
        emb_count = run_embedding_dedup(db, model_name=emb_model, threshold=emb_threshold)
        console.print(f"  Embedding dedup: {emb_count} duplicates")
        count += emb_count

    db.close()
    console.print(f"[green]Total: {count} duplicate articles marked.[/green]")


@app.command()
def score(
    rescore: Annotated[bool, typer.Option("--rescore")] = False,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
):
    """Score articles using engagement metrics + Claude."""
    config = load_config(config_path)
    db = _get_db(config)
    db.init_schema()

    from distill.processing.scorer import score_articles

    def show_progress(completed: int, total: int) -> None:
        console.print(f"  Assessed {completed}/{total}")

    count = asyncio.run(score_articles(db, config, force=rescore, on_progress=show_progress))
    db.close()
    console.print(f"[green]Scored {count} articles.[/green]")


@app.command()
def run(config_path: Annotated[Path | None, typer.Option("--config")] = None):
    """Full pipeline: collect → extract → dedup → score."""
    config = load_config(config_path)
    from distill.processing.pipeline import PipelineStage, run_pipeline

    labels = {
        PipelineStage.COLLECT: "Collecting articles",
        PipelineStage.EXTRACT: "Extracting content",
        PipelineStage.DEDUP: "Deduplicating",
        PipelineStage.SCORE: "Scoring",
        PipelineStage.TRUNCATE: "Truncating stored content",
    }

    def show_stage(stage: PipelineStage) -> None:
        position = list(PipelineStage).index(stage) + 1
        console.print(f"\n[bold cyan]{position}/5 {labels[stage]}[/bold cyan]")

    console.print("[bold]Running full pipeline...[/bold]")
    result = asyncio.run(run_pipeline(config, on_stage=show_stage))
    console.print(f"  Collected: {result.collection.inserted}")
    console.print(f"  Extracted: {result.extracted}")
    console.print(f"  Duplicates found: {result.deduplicated}")
    console.print(f"  Scored: {result.scored}")
    console.print(f"  Truncated: {result.truncated} articles")
    console.print("\n[bold green]Pipeline complete![/bold green]")


@app.command()
def digest(
    week: Annotated[str | None, typer.Option("--week")] = None,
    top_n: Annotated[int, typer.Option("--top")] = 20,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
):
    """Generate a markdown digest of top articles."""
    config = load_config(config_path)
    db = _get_db(config)

    from distill.outputs.digest import generate_digest

    output_dir = get_output_dir(config)
    path = generate_digest(db, output_dir, week_label=week, top_n=top_n, config=config)
    db.close()
    console.print(f"[green]Digest written to {path}[/green]")


@app.command()
def stats(config_path: Annotated[Path | None, typer.Option("--config")] = None):
    """Show database statistics."""
    config = load_config(config_path)
    db = _get_db(config)

    s = db.get_stats()
    db.close()

    table = Table(title="Distill Stats")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Total articles", str(s["total_articles"]))
    table.add_row("Unique articles", str(s["unique_articles"]))
    table.add_row("With content", str(s["with_content"]))
    table.add_row("Scored", str(s["scored"]))
    table.add_row("Duplicates", str(s["duplicates"]))

    for source, count in s.get("by_source", {}).items():
        table.add_row(f"  {source}", str(count))

    console.print(table)


@app.command()
def serve(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
):
    """Start the web dashboard."""
    config = load_config(config_path)
    try:
        from distill.outputs.web import create_app

        web_config = config.get("web", {})
        host = web_config.get("host", "0.0.0.0")
        port = web_config.get("port", 8585)

        import uvicorn

        web_app = create_app(config)
        uvicorn.run(web_app, host=host, port=port)
    except ImportError:
        console.print("[red]Web deps not installed. Run: uv add fastapi uvicorn jinja2[/red]")


@app.command()
def podcast(
    articles: Annotated[
        str | None, typer.Option("--articles", help="Article IDs, comma-separated")
    ] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
):
    """Generate a podcast. Weekly (default) or on-demand with --articles."""
    config = load_config(config_path)
    try:
        from distill.outputs.podcast import generate_podcast

        output_dir = get_output_dir(config)
        db = _get_db(config)
        article_ids = None
        if articles:
            article_ids = [int(x.strip()) for x in articles.split(",")]
            console.print(f"On-demand podcast for article IDs: {article_ids}")
        path = asyncio.run(generate_podcast(db, config, output_dir, article_ids=article_ids))
        db.close()
        if path:
            console.print(f"[green]Podcast saved to {path}[/green]")
        else:
            console.print("[yellow]Podcast generation failed or no articles available.[/yellow]")
    except ImportError:
        console.print("[red]Podcast dependencies not installed.[/red]")


@app.command()
def qa(
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
    port: Annotated[int, typer.Option("--port")] = 0,
):
    """Visual QA + accessibility audit of the web dashboard using Rodney & Showboat."""
    config = load_config(config_path)
    web_config = config.get("web", {})
    qa_port = port or web_config.get("port", 8585)
    base_url = f"http://localhost:{qa_port}"

    output_dir = get_output_dir(config)
    qa_dir = output_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    report_path = qa_dir / "qa-report.md"

    for tool in ("rodney", "showboat"):
        if not shutil.which(tool):
            console.print(f"[red]{tool} not found. Install with: uv tool install {tool}[/red]")
            raise typer.Exit(1)

    # Check server is running
    import httpx

    try:
        resp = httpx.get(base_url, timeout=3)
        resp.raise_for_status()
    except Exception:
        console.print(
            f"[red]Dashboard not running at {base_url}. Start it with: distill serve[/red]"
        )
        raise typer.Exit(1)

    def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, check=check)

    def _rodney(*args: str, check: bool = True) -> subprocess.CompletedProcess:
        return _run(["rodney", *args], check=check)

    def _showboat(*args: str) -> subprocess.CompletedProcess:
        return _run(["showboat", *args])

    console.print("[bold]Starting visual QA...[/bold]\n")

    # Start headless Chrome
    _rodney("start")
    console.print("  Chrome started")

    try:
        if report_path.exists():
            report_path.unlink()
        _showboat("init", str(report_path), "Distill — Visual QA Report")

        pages = [
            ("/", "articles", "Articles"),
            ("/search", "search", "Search"),
            ("/add", "add", "Add Links"),
            ("/digests", "digests", "Digests"),
            ("/podcasts", "podcasts", "Podcasts"),
            ("/stats", "stats", "Stats"),
        ]

        # Screenshot every page
        console.print("\n[bold cyan]1/3 Screenshots[/bold cyan]")
        for path, slug, label in pages:
            _rodney("open", f"{base_url}{path}")
            _rodney("waitstable")
            img_path = str(qa_dir / f"{slug}.png")
            _rodney("screenshot", img_path)
            _showboat("note", str(report_path), f"## {label} ({path})")
            _showboat("image", str(report_path), img_path)
            console.print(f"    {label} ({path})")

        # Functional checks
        console.print("\n[bold cyan]2/3 Functional checks[/bold cyan]")
        _showboat("note", str(report_path), "## Functional Checks")

        # Articles page — check article count
        _rodney("open", base_url)
        _rodney("waitstable")
        result = _rodney("count", ".article-row", check=False)
        article_count = result.stdout.strip()
        _showboat(
            "note",
            str(report_path),
            f"- Articles page: **{article_count}** article rows rendered",
        )
        console.print(f"    Articles: {article_count} rows")

        # Check nav active state
        result = _rodney("exists", "nav a.active", check=False)
        nav_ok = result.returncode == 0
        _showboat(
            "note",
            str(report_path),
            f"- Nav active state: {'PASS' if nav_ok else 'FAIL'}",
        )
        console.print(f"    Nav active state: {'PASS' if nav_ok else 'FAIL'}")

        # Search — submit a query and verify results
        _rodney("open", f"{base_url}/search")
        _rodney("waitstable")
        _rodney("input", "input[name=query]", "Claude Code")
        _rodney("click", "button[type=submit]")
        _rodney("waitstable")
        _rodney("sleep", "2")
        result = _rodney("count", ".article-row", check=False)
        search_count = result.stdout.strip()
        search_img = str(qa_dir / "search-results.png")
        _rodney("screenshot", search_img)
        _showboat("note", str(report_path), "### Search test: 'Claude Code'")
        _showboat("image", str(report_path), search_img)
        _showboat(
            "note",
            str(report_path),
            f"- Search returned **{search_count}** results",
        )
        console.print(f"    Search 'Claude Code': {search_count} results")

        # Stats page — verify stat cards render
        _rodney("open", f"{base_url}/stats")
        _rodney("waitstable")
        result = _rodney("count", ".stat-card", check=False)
        stat_count = result.stdout.strip()
        _showboat(
            "note",
            str(report_path),
            f"- Stats page: **{stat_count}** stat cards",
        )
        console.print(f"    Stats: {stat_count} cards")

        # Podcasts page — verify generate buttons
        _rodney("open", f"{base_url}/podcasts")
        _rodney("waitstable")
        result = _rodney("count", "button[type=submit]", check=False)
        btn_count = result.stdout.strip()
        _showboat(
            "note",
            str(report_path),
            f"- Podcasts page: **{btn_count}** action buttons",
        )
        console.print(f"    Podcasts: {btn_count} buttons")

        # Accessibility audit
        console.print("\n[bold cyan]3/3 Accessibility audit[/bold cyan]")
        _showboat("note", str(report_path), "## Accessibility Audit")

        for path, slug, label in [("/", "articles", "Articles"), ("/search", "search", "Search")]:
            _rodney("open", f"{base_url}{path}")
            _rodney("waitstable")

            # Check links have accessible names
            result = _rodney("ax-find", "--role", "link", "--json", check=False)
            links = _parse_ax_results(result.stdout)
            unnamed_links = [lnk for lnk in links if not lnk.get("name")]
            link_status = (
                f"PASS ({len(links)} links, all named)"
                if not unnamed_links
                else f"WARN ({len(unnamed_links)}/{len(links)} links missing names)"
            )
            _showboat(
                "note",
                str(report_path),
                f"- **{label}** links: {link_status}",
            )
            console.print(f"    {label} links: {link_status}")

            # Check form inputs have labels
            result = _rodney("ax-find", "--role", "textbox", "--json", check=False)
            inputs = _parse_ax_results(result.stdout)
            result2 = _rodney("ax-find", "--role", "combobox", "--json", check=False)
            selects = _parse_ax_results(result2.stdout)
            all_fields = inputs + selects
            unnamed_fields = [f for f in all_fields if not f.get("name")]
            field_status = (
                f"PASS ({len(all_fields)} fields, all labeled)"
                if not unnamed_fields
                else f"WARN ({len(unnamed_fields)}/{len(all_fields)} fields missing labels)"
            )
            _showboat(
                "note",
                str(report_path),
                f"- **{label}** form fields: {field_status}",
            )
            console.print(f"    {label} form fields: {field_status}")

        # Check heading hierarchy on articles page
        _rodney("open", base_url)
        _rodney("waitstable")
        result = _rodney("ax-find", "--role", "heading", "--json", check=False)
        headings = _parse_ax_results(result.stdout)
        heading_names = [_ax_name(h) for h in headings]
        _showboat(
            "note",
            str(report_path),
            f"- **Articles** heading hierarchy: {', '.join(heading_names[:5])}",
        )
        console.print(f"    Heading hierarchy: {', '.join(heading_names[:5])}")

    finally:
        _rodney("stop", check=False)
        console.print("\n  Chrome stopped")

    console.print(f"\n[bold green]QA report: {report_path}[/bold green]")


def _parse_ax_results(json_str: str) -> list[dict]:
    """Parse Rodney accessibility JSON output."""
    import json as json_mod

    try:
        data = json_mod.loads(json_str)
        if isinstance(data, list):
            return data
        return []
    except (json_mod.JSONDecodeError, TypeError):
        return []


def _ax_name(node: dict) -> str:
    """Extract human-readable name from an accessibility node."""
    name = node.get("name", "")
    if isinstance(name, dict):
        return name.get("value", str(name))
    return str(name) if name else "?"


@app.command()
def ingest(
    file: Annotated[
        Path, typer.Argument(help="JSON file with CollectedArticle list (use - for stdin)")
    ],
    db: Annotated[Path | None, typer.Option("--db", help="Path to SQLite database")] = None,
    config_path: Annotated[Path | None, typer.Option("--config")] = None,
) -> None:
    """Ingest articles from a JSON file into the database. Use with Slack MCP backend."""
    import json as _json
    import sys

    from distill.models import CollectedArticle
    from distill.processing.intake import ingest_articles

    config = load_config(config_path)
    db_path = db if db is not None else get_db_path(config)
    database = Database(db_path)
    database.init_schema()
    try:
        if str(file) == "-":
            data = _json.load(sys.stdin)
        else:
            data = _json.loads(Path(file).read_text())

        if not isinstance(data, list):
            typer.echo("Error: JSON must be a list of articles", err=True)
            raise typer.Exit(1)

        articles = [CollectedArticle(**item) for item in data]
        results = ingest_articles(database, articles)
        count = sum(result.article_id is not None for result in results)

        typer.echo(f"Ingested {count} new articles ({len(data) - count} duplicates skipped)")
    except _json.JSONDecodeError as e:
        typer.echo(f"Error: invalid JSON — {e}", err=True)
        raise typer.Exit(1)
    finally:
        database.close()


@app.command()
def archive(config_path: Annotated[Path | None, typer.Option("--config")] = None):
    """Sunday job: generate digest + podcast from current week, then clear old articles."""
    config = load_config(config_path)
    db = _get_db(config)

    from distill.outputs.digest import generate_digest, get_week_range

    console.print("[bold]Archiving current week...[/bold]")

    label, week_start, week_end = get_week_range()
    top_n = config.get("podcast", {}).get("top_n", 10)

    # Generate digest
    output_dir = get_output_dir(config)
    path = generate_digest(db, output_dir, top_n=top_n, config=config)
    console.print(f"  Digest: {path}")

    # Generate podcast
    try:
        from distill.outputs.podcast import generate_podcast

        pod_path = asyncio.run(generate_podcast(db, config, output_dir))
        if pod_path:
            console.print(f"  Podcast: {pod_path}")
    except ImportError:
        console.print("  [yellow]Skipping podcast (dependencies not installed)[/yellow]")

    # Cache this week's top articles to prevent repeats next week
    from distill.processing.recommendation import ReadingSlateRequest, select_reading_slate

    top_articles = select_reading_slate(
        db,
        config,
        ReadingSlateRequest(
            limit=top_n,
            week_start=week_start,
            week_end=week_end,
            exclude_last_week=False,
        ),
    )
    if top_articles:
        db.save_weekly_cache(label, [a for a, _ in top_articles])
        console.print(f"  Cached {len(top_articles)} articles for cross-week dedup")

    # Delete articles older than current week start
    deleted = db.delete_old_articles(before=week_start)
    console.print(f"  Cleared {deleted} old articles")

    db.close()
    console.print("[green]Archive complete.[/green]")
