from __future__ import annotations

import traceback
from pathlib import Path
from typing import Optional

import typer

from lab_auto.archive import archive_work, unarchive_work
from lab_auto.browser import BrowserService, session_path
from lab_auto.config import (
    clear_saved_workspace,
    get_saved_workspace,
    set_saved_workspace,
    user_config_path,
)
from lab_auto.context import get_context, set_context
from lab_auto.convert import convert_docx_to_pdf, resolve_convert_paths
from lab_auto.review import mark_work_review
from lab_auto.session_crypto import SessionKeyError
from lab_auto.state import StateFileError, active_works, archived_works, load_state
from lab_auto.submit import SubmitService
from lab_auto.sync import SyncService

app = typer.Typer(
    no_args_is_help=True,
    help="Sync GUAP lab tasks, manage local folders, convert DOCX to PDF, and submit reports.",
)
auth_app = typer.Typer(
    no_args_is_help=True,
    help="Sign in to pro.guap.ru and manage the encrypted local browser session.",
)
workspace_app = typer.Typer(
    no_args_is_help=True,
    help="Choose where labs, state, and session files are stored on disk.",
)
app.add_typer(auth_app, name="auth")
app.add_typer(workspace_app, name="workspace")


@app.callback()
def main(
    ctx: typer.Context,
    root: Optional[Path] = typer.Option(
        None,
        "--root",
        help="Workspace directory (overrides saved default).",
        envvar="LAB_AUTO_ROOT",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show full tracebacks."),
) -> None:
    """GUAP lab workflow CLI (see subcommands)."""
    del ctx
    set_context(root, verbose=verbose)


def _root() -> Path:
    return get_context().root


def _fail(message: str, exc: Exception) -> None:
    typer.echo(message, err=True)
    if isinstance(exc, StateFileError):
        typer.echo(
            "Repair or remove state/works.yaml, then run `lab-auto sync` again.",
            err=True,
        )
    if isinstance(exc, SessionKeyError):
        typer.echo(
            "Run `lab-auto auth login` to recreate the session encryption key and session file.",
            err=True,
        )
    if get_context().verbose:
        typer.echo(traceback.format_exc(), err=True)
    raise typer.Exit(1) from exc


@workspace_app.command("show")
def workspace_show() -> None:
    """Print the active workspace path and saved default from config."""
    effective = _root()
    typer.echo(f"Active workspace: {effective}")
    try:
        saved = get_saved_workspace()
    except FileNotFoundError as exc:
        typer.echo(f"Saved default: (missing path — {exc})", err=True)
    else:
        if saved is None:
            typer.echo("Saved default: (none — using current directory when --root is omitted)")
        elif saved == effective:
            typer.echo(f"Saved default: {saved}")
        else:
            typer.echo(f"Saved default: {saved} (overridden by --root or LAB_AUTO_ROOT)")
    typer.echo(f"Config file: {user_config_path()}")


@workspace_app.command("set")
def workspace_set(
    path: Path = typer.Argument(help="Directory for labs/, state/, session/, and logs/."),
) -> None:
    """Save a workspace directory as the default (used when --root is omitted)."""
    resolved = path.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    saved = set_saved_workspace(resolved)
    set_context(resolved, verbose=get_context().verbose)
    typer.echo(f"Default workspace set to {saved}")


@workspace_app.command("unset")
def workspace_unset() -> None:
    """Clear the saved default workspace (fall back to current directory)."""
    clear_saved_workspace()
    set_context(None, verbose=get_context().verbose)
    typer.echo(f"Default workspace cleared. Active workspace is now {_root()}")


@auth_app.command("import-cookie")
def import_cookie(
    path: Path = typer.Argument(help="JSON file: Playwright storage state or cookie list."),
) -> None:
    """Import browser cookies and validate access to the GUAP task list."""
    try:
        browser = BrowserService(_root())
        target = browser.import_cookie_file(path)
        if not browser.validate_session()[0]:
            target.unlink(missing_ok=True)
            _fail(
                "Imported cookies did not produce a valid GUAP session. "
                "Check the cookie file and try again.",
                RuntimeError("session validation failed"),
            )
        typer.echo(f"Imported session to {target}")
    except Exception as exc:
        _fail(f"Import failed: {exc}", exc)


@auth_app.command("logout")
def logout() -> None:
    """Delete the local encrypted session file from the workspace."""
    deleted = BrowserService(_root()).logout()
    typer.echo("Local session deleted" if deleted else "No local session to delete")


@auth_app.command("migrate-session")
def migrate_session() -> None:
    """Upgrade a legacy session file to encrypted format without opening the browser."""
    root = _root()
    path = session_path(root)
    try:
        migrated = BrowserService(root).migrate_session_if_needed()
    except Exception as exc:
        _fail(f"Session migration failed: {exc}", exc)
    if migrated:
        typer.echo(f"Session file upgraded to encrypted format: {path}")
        raise typer.Exit(0)
    if path.exists():
        typer.echo("Session is already encrypted.")
        raise typer.Exit(0)
    typer.echo("No session file found. Run `lab-auto auth login`.", err=True)
    raise typer.Exit(1)


@auth_app.command("check")
def check() -> None:
    """Verify the saved session can open the GUAP student task list (headless)."""
    try:
        browser = BrowserService(_root())
        ok, migrated = browser.validate_session()
    except Exception as exc:
        _fail(f"Session check failed: {exc}", exc)
    if ok:
        typer.echo("Session is valid.")
        if migrated:
            typer.echo("Session file upgraded to encrypted format.")
        raise typer.Exit(0)
    typer.echo("Session is missing or expired. Run `lab-auto auth login`.", err=True)
    raise typer.Exit(1)


@auth_app.command("login")
def login() -> None:
    """Sign in via GUAP SSO in a visible browser and save an encrypted session."""
    username = typer.prompt("Username")
    password = typer.prompt("Password", hide_input=True)
    try:
        BrowserService(_root(), headless=False).login_interactive(username, password)
        typer.echo("Login succeeded and local session was saved.")
    except Exception as exc:
        _fail(f"Login failed: {exc}", exc)


@app.command("sync")
def sync(
    archive: bool = typer.Option(
        False,
        "--archive",
        help="Keep tasks removed from the website as archived instead of deleting them from state.",
    ),
) -> None:
    """Pull tasks from pro.guap.ru, update folders and works.yaml, download assignment PDFs."""
    try:
        result = SyncService(_root()).sync(archive_removed=archive)
        active = active_works(result.records)
        archived = archived_works(result.records)
        typer.echo(f"Sync completed: {len(active)} active works")
        if archive and archived:
            typer.echo(f"Archived {len(archived)} removed works")
        if result.empty_task_list:
            typer.echo(
                "Warning: parsed 0 tasks from website; state unchanged.",
                err=True,
            )
            if result.parse_warning:
                typer.echo(f"Hint: {result.parse_warning}", err=True)
        if result.dropped_from_site and not archive:
            typer.echo(
                "Dropped from state (no longer on website): "
                + ", ".join(result.dropped_from_site),
                err=True,
            )
    except Exception as exc:
        _fail(f"Sync failed: {exc}", exc)


@app.command("status")
def status(
    all_works: bool = typer.Option(
        False,
        "--all",
        help="Include archived works.",
    ),
) -> None:
    """List synced works with local status tags, grouped by subject."""
    works = load_state(_root())
    if all_works:
        visible = works
    else:
        visible = active_works(works)
    if not visible:
        typer.echo("No works synced yet." if not works else "No active works.")
        return
    current_subject = None
    for work in sorted(visible, key=lambda item: (item.subject, item.name)):
        if work.subject != current_subject:
            current_subject = work.subject
            typer.echo(f"\n{current_subject}")
        suffix = " [archived]" if work.archived else ""
        typer.echo(f"  {work.local_status.prefix} {work.name} ({work.work_id}){suffix}")
    if not all_works and archived_works(works):
        typer.echo(f"\n({len(archived_works(works))} archived — use --all to list)")


@app.command("archive")
def archive(
    work_ref: str = typer.Argument(help="Work ID (task-178541) or a unique name fragment."),
) -> None:
    """Mark a work as archived and hide it from default status listing."""
    try:
        work = archive_work(_root(), work_ref)
        typer.echo(f"{work.work_id} archived")
    except Exception as exc:
        _fail(f"Archive failed: {exc}", exc)


@app.command("unarchive")
def unarchive(
    work_ref: str = typer.Argument(help="Work ID (task-178541) or a unique name fragment."),
) -> None:
    """Restore an archived work to the active task list."""
    try:
        work = unarchive_work(_root(), work_ref)
        typer.echo(f"{work.work_id} unarchived")
    except Exception as exc:
        _fail(f"Unarchive failed: {exc}", exc)


@app.command("review")
def review(
    work_ref: str = typer.Argument(help="Work ID (task-178541) or a unique name fragment."),
) -> None:
    """Mark a work as [REVIEW] after you finished the report locally (ready for your check)."""
    try:
        work = mark_work_review(_root(), work_ref)
        typer.echo(f"{work.work_id} marked {work.local_status.prefix}")
    except Exception as exc:
        _fail(f"Review marking failed: {exc}", exc)


@app.command("convert")
def convert(
    work_ref: str = typer.Argument(help="Work ID (task-178541) or a unique name fragment."),
    docx: Path = typer.Option(..., "--docx", help="Source DOCX file to convert."),
    output: Optional[Path] = typer.Option(None, "--output", help="PDF output path (default: beside DOCX)."),
) -> None:
    """Convert a DOCX report to PDF (Word or LibreOffice, depending on platform)."""
    try:
        docx_path, pdf_path = resolve_convert_paths(_root(), work_ref, docx, output)
        pdf = convert_docx_to_pdf(docx_path, pdf_path)
        typer.echo(f"Converted {docx_path} -> {pdf}")
    except Exception as exc:
        _fail(f"Conversion failed: {exc}", exc)


@app.command("submit")
def submit(
    work_ref: str = typer.Argument(help="Work ID (task-178541) or a unique name fragment."),
    file: Path = typer.Option(..., "--file", help="PDF file to upload to GUAP."),
) -> None:
    """Upload a PDF report to the task page and set local status to [SENT] or [SENTFAILED]."""
    try:
        work = SubmitService(_root()).submit(work_ref, file)
        typer.echo(f"{work.work_id} marked {work.local_status.prefix}")
    except Exception as exc:
        _fail(f"Submit failed: {exc}", exc)
