"""CLI fallback for fixing Claude ↔ NotebookLM connection issues."""

import typer

from notebooklm_tools.cli.utils import make_console

console = make_console()
app = typer.Typer(
    name="fix-connection",
    help="Diagnose and repair connection issues with NotebookLM",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def fix_connection(
    ctx: typer.Context,
    error: str = typer.Option(
        "",
        "--error",
        "-e",
        help="Error message from the failed operation (improves diagnosis)",
    ),
    http_status: int = typer.Option(
        0,
        "--status",
        "-s",
        help="HTTP status code from the failure (e.g. 401, 403, 400)",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help="Profile to repair (uses default if not specified)",
    ),
    verify_only: bool = typer.Option(
        False,
        "--verify",
        help="Only verify the current connection without attempting repair",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Show detailed output for each recovery layer",
    ),
) -> None:
    """
    Diagnose and automatically repair connection issues with NotebookLM.

    Runs up to three recovery layers:

    \b
      1. Refresh CSRF token + session from live NotebookLM page (~1-2s)
      2. Reload saved credentials from disk
      3. Re-authenticate via headless Chrome (requires saved profile)

    Use --verify to only check if the current connection works.

    Examples:

    \b
        nlm fix-connection
        nlm fix-connection --error "401 Unauthorized" --status 401
        nlm fix-connection --profile work
        nlm fix-connection --verify
    """
    if ctx.invoked_subcommand is not None:
        return

    from notebooklm_tools.services.connection import (
        diagnose_connection_error,
        repair_connection,
        verify_connection,
    )

    # Verify-only mode
    if verify_only:
        console.print("[bold]Verifying connection...[/bold]")
        result = verify_connection()
        if result["success"]:
            console.print(f"[green]✓[/green] {result['message']}")
        else:
            console.print(f"[red]✗[/red] {result['message']}")
            console.print(f"  [dim]{result.get('error', '')}[/dim]")
            console.print("\n[yellow]→[/yellow] Run [cyan]nlm fix-connection[/cyan] to attempt repair.")
            raise typer.Exit(1)
        return

    console.print("[bold]NotebookLM Connection Repair[/bold]\n")

    # Step 1: Diagnose
    console.print("[bold]Step 1:[/bold] Diagnosing...")
    diagnosis = diagnose_connection_error(error_message=error, http_status=http_status)
    console.print(f"  Issue:  [yellow]{diagnosis['title']}[/yellow]")
    if verbose:
        console.print(f"  Code:   [dim]{diagnosis['code']}[/dim]")
        console.print(f"  Detail: [dim]{diagnosis['description']}[/dim]")

    if not diagnosis["recoverable"]:
        console.print(f"\n[red]✗ Cannot repair automatically.[/red]")
        console.print(f"\n[yellow]Action required:[/yellow]")
        console.print(f"  {diagnosis['action']}")
        raise typer.Exit(1)

    console.print(f"  Plan:   {diagnosis['action']}\n")

    # Step 2: Repair
    console.print("[bold]Step 2:[/bold] Attempting repair...")
    repair = repair_connection(profile=profile)

    if not repair["success"]:
        console.print(f"[red]✗[/red] All recovery layers failed.")
        if verbose:
            console.print(f"  [dim]{repair['message']}[/dim]")
        console.print(f"\n[yellow]Next step:[/yellow]")
        console.print(f"  {repair.get('next_step', 'Run nlm login to re-authenticate.')}")
        raise typer.Exit(1)

    layer_label = {1: "CSRF refresh", 2: "disk reload", 3: "headless Chrome"}.get(
        repair["layer"], f"layer {repair['layer']}"
    )
    console.print(f"  [green]✓[/green] Repaired via {layer_label}")
    if verbose:
        console.print(f"  [dim]{repair['message']}[/dim]")

    # Step 3: Verify
    console.print("\n[bold]Step 3:[/bold] Verifying...")
    verification = verify_connection()

    if verification["success"]:
        console.print(f"  [green]✓[/green] {verification['message']}")
        console.print("\n[green]Connection restored.[/green] You can now retry your operation.")
    else:
        console.print(f"  [yellow]⚠[/yellow]  Tokens refreshed but verification failed.")
        console.print(f"  [dim]{verification.get('error', '')}[/dim]")
        console.print("\n  Try the original operation; if it still fails run [cyan]nlm login[/cyan].")
        raise typer.Exit(2)
