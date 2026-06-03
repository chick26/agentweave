from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PdfDocument:
    source: str
    page: int
    text: str


def load_pdf_documents(paths: list[Path]) -> list[PdfDocument]:
    documents: list[PdfDocument] = []
    for path in paths:
        loaded = [item for item in _load_with_pypdf(path) if item.text.strip()]
        if not loaded:
            loaded = [item for item in _load_with_pdftotext(path) if item.text.strip()]
        documents.extend(loaded)
    return [item for item in documents if item.text.strip()]


def _load_with_pypdf(path: Path) -> list[PdfDocument]:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return []
    try:
        reader = PdfReader(str(path))
        return [
            PdfDocument(
                source=str(path),
                page=index + 1,
                text=(page.extract_text() or "").strip(),
            )
            for index, page in enumerate(reader.pages)
        ]
    except Exception:
        return []


def _load_with_pdftotext(path: Path) -> list[PdfDocument]:
    try:
        completed = subprocess.run(
            ["pdftotext", str(path), "-"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return []
    text = completed.stdout.strip()
    return [PdfDocument(source=str(path), page=0, text=text)] if text else []
