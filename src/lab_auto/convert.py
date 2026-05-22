from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from lab_auto.state import load_state_unlocked, locked_workspace
from lab_auto.workspace import find_work_in_list

WD_EXPORT_FORMAT_PDF = 17
_CONVERT_TIMEOUT_SECONDS = 120
_WORD_CONVERT_SCRIPT = f"""
$ErrorActionPreference = 'Stop'
$word = $null
try {{
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $doc = $word.Documents.Open($env:LAB_AUTO_DOCX)
    try {{
        $doc.ExportAsFixedFormat($env:LAB_AUTO_PDF, {WD_EXPORT_FORMAT_PDF})
    }} finally {{
        $doc.Close([ref]0)
    }}
}} finally {{
    if ($null -ne $word) {{
        $word.Quit()
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($word)
    }}
}}
"""


def default_pdf_path(docx_path: Path) -> Path:
    return docx_path.with_suffix(".pdf")


def find_libreoffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


def convert_with_libreoffice(docx_path: Path, output: Path, soffice: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(output.parent), str(docx_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=_CONVERT_TIMEOUT_SECONDS,
    )
    generated = docx_path.with_suffix(".pdf")
    if generated != output and generated.exists():
        generated.replace(output)


def convert_with_word(docx_path: Path, output: Path) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Microsoft Word conversion is only available on Windows.")

    output.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["LAB_AUTO_DOCX"] = str(docx_path.resolve())
    env["LAB_AUTO_PDF"] = str(output.resolve())
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WORD_CONVERT_SCRIPT],
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=_CONVERT_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        message = "Microsoft Word failed to convert DOCX to PDF."
        if detail:
            message = f"{message} {detail}"
        raise RuntimeError(message) from exc


def resolve_convert_paths(
    root: Path,
    work_ref: str,
    docx: Path,
    output_path: Path | None = None,
) -> tuple[Path, Path]:
    with locked_workspace(root):
        _, work = find_work_in_list(
            root,
            load_state_unlocked(root),
            work_ref,
        )
        folder = work.folder
    docx_path = docx if docx.is_absolute() else folder / docx
    if not docx_path.exists():
        raise FileNotFoundError(docx_path)
    output = output_path or folder / default_pdf_path(docx_path).name
    return docx_path, output


def convert_docx_to_pdf(docx_path: Path, output_path: Path | None = None) -> Path:
    if docx_path.suffix.lower() != ".docx":
        raise ValueError("Only DOCX files can be converted to PDF.")
    if not docx_path.exists():
        raise FileNotFoundError(docx_path)

    output = output_path or default_pdf_path(docx_path)
    soffice = find_libreoffice()
    errors: list[str] = []

    if soffice:
        try:
            convert_with_libreoffice(docx_path, output, soffice)
        except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
            errors.append(f"LibreOffice: {exc}")
            if sys.platform != "win32":
                raise RuntimeError(errors[-1]) from exc
        else:
            if output.exists():
                return output
            errors.append(f"LibreOffice: PDF was not created at {output}")

    if sys.platform == "win32":
        try:
            convert_with_word(docx_path, output)
        except RuntimeError as exc:
            errors.append(str(exc))
        else:
            if output.exists():
                return output
            errors.append(f"Microsoft Word: PDF was not created at {output}")

    if errors:
        raise RuntimeError("DOCX conversion failed. " + " | ".join(errors))
    raise RuntimeError(
        "DOCX conversion requires LibreOffice (`soffice`) on PATH, "
        "or Microsoft Word on Windows."
    )
