"""Minimal SuperDocs API client (stdlib only), ported from the contract
proven in Task 2 (comparison-table generator + Writer sidebar):

- approve must send the explicit changes array (a bare {job_id, approved}
  is a silent no-op — SuperDocs bug B1)
- ops bill on APPLY only; uploads, exports, and polling are free
- export while awaiting_approval returns the PRE-edit document, so the
  order approve -> confirm completed -> export is mandatory
- style with background-color LONGHAND, never the background: shorthand
  (the exporter drops the shorthand and keeps color: -> white-on-white,
  SuperDocs bug B4)

The key is never logged and never stored by this module.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import config


class SuperDocsError(Exception):
    pass


def api_key() -> str:
    key = os.environ.get("SUPERDOCS_API_KEY")
    if key:
        return key
    cred = Path.home() / ".superdocs" / "agent_credentials.json"
    if cred.exists():
        return json.loads(cred.read_text(encoding="utf-8-sig"))["api_key"]
    raise SuperDocsError("no SuperDocs API key configured")


def has_key() -> bool:
    try:
        api_key()
        return True
    except SuperDocsError:
        return False


def _call(method: str, path: str, body: dict | None = None, retries: int = 1,
          timeout: int = 120):
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(config.SUPERDOCS_API + path, data=data,
                                     method=method)
        req.add_header("Authorization", "Bearer " + api_key())
        if data:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if "json" in r.headers.get("Content-Type", ""):
                    return json.loads(raw)
                return raw
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if isinstance(exc, urllib.error.HTTPError):
                last = f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}"
            else:
                last = str(exc)
            if attempt < retries:
                time.sleep(4)  # first call in a fresh session may flake warming up
    raise SuperDocsError(f"{method} {path} failed: {last}")


def upload_html(session_id: str, filename: str, html: str) -> None:
    _call("POST", "/v1/documents/upload-base64", {
        "filename": filename,
        "file_base64": base64.b64encode(html.encode("utf-8")).decode(),
        "session_id": session_id,
    })


def export(session_id: str, fmt: str) -> bytes:
    blob = _call("POST", "/v1/documents/export",
                 {"session_id": session_id, "format": fmt})
    if isinstance(blob, dict):
        blob = base64.b64decode(blob.get("file_base64") or blob.get("content_base64"))
    return blob
