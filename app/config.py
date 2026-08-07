"""Central configuration. Everything is overridable via environment variables;
nothing here ever contains a secret value, only the names of the env vars."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://doctask:doctask@localhost:5433/doctask"
)

# LLM boundary. "auto" picks the first live backend with a key present
# (Anthropic, then Gemini), else the deterministic mock.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "auto")
# Per-provider default model when unset (Anthropic: claude-opus-5,
# Gemini: gemini-2.5-flash).
LLM_MODEL = os.environ.get("LLM_MODEL") or None

# $/MTok for the cost ledger (input, output), per published pricing.
MODEL_PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
}

SUPERDOCS_API = "https://api.superdocs.app"

WATCH_DIR = os.environ.get("WATCH_DIR", str(REPO_ROOT / "corpus" / "incoming"))
EXPORT_DIR = os.environ.get("EXPORT_DIR", str(REPO_ROOT / "exports"))
RULES_FILE = os.environ.get("RULES_FILE", str(REPO_ROOT / "rules" / "playbook.yaml"))
FIXTURES_DIR = Path(
    os.environ.get("FIXTURES_DIR", str(REPO_ROOT / "app" / "llm" / "fixtures"))
)

ACCEPTED_FORMATS = {".md", ".txt", ".html", ".docx", ".pdf"}
