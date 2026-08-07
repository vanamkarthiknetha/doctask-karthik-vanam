"""Format readers. Every reader returns whitespace-normalized text so that a
quote extracted from one rendering of a document anchors identically in
another (PDF line-wrapping, DOCX paragraph breaks, HTML whitespace).

Normalization rule: every run of whitespace collapses to a single space.
Fact anchors (char_start/char_end) always refer to this normalized text.
"""
import hashlib
import io
from html.parser import HTMLParser
from pathlib import Path


def normalize(text: str) -> str:
    return " ".join(text.split())


class _HTMLText(HTMLParser):
    SKIP = {"script", "style"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _read_html(data: bytes) -> str:
    p = _HTMLText()
    p.feed(data.decode("utf-8", errors="replace"))
    return " ".join(p.parts)


def _read_docx(data: bytes) -> str:
    import docx  # python-docx

    d = docx.Document(io.BytesIO(data))
    parts = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def _read_pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


READERS = {
    ".md": lambda b: b.decode("utf-8", errors="replace"),
    ".txt": lambda b: b.decode("utf-8", errors="replace"),
    ".html": _read_html,
    ".docx": _read_docx,
    ".pdf": _read_pdf,
}


def read_document(path: str | Path) -> dict:
    """Returns {filename, format, raw_text, sha256}. Raises ValueError for a
    format outside the declared set — the caller decides how to escalate."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext not in READERS:
        raise ValueError(f"format {ext!r} is outside the declared set")
    raw = READERS[ext](path.read_bytes())
    text = normalize(raw)
    return {
        "filename": path.name,
        "format": ext.lstrip("."),
        "raw_text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
