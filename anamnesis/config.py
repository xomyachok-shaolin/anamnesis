"""Single source of truth for paths, models, and collection names.

All modules should import from here rather than hardcoding.
"""
import os
from pathlib import Path


def _expand_path(value: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(value)))


# --- Data roots ---
DATA_DIR = Path(_expand_path(os.environ.get("ANAMNESIS_DATA_DIR", "~/.claude-mem")))
DB_PATH = str(DATA_DIR / "claude-mem.db")
CHROMA_DIR = str(DATA_DIR / "semantic-chroma")
FASTEMBED_CACHE = str(DATA_DIR / "fastembed-models")
HEALTH_FILE = str(DATA_DIR / "health.json")

# --- Source roots ---
CC_ROOT = _expand_path(os.environ.get("ANAMNESIS_CC_ROOT", "~/.claude/projects"))
CODEX_ROOT = _expand_path(os.environ.get("ANAMNESIS_CODEX_ROOT", "~/.codex/sessions"))
PROJECT_PREFIXES = tuple(
    _expand_path(part.strip())
    for part in os.environ.get("ANAMNESIS_PROJECT_PREFIXES", "").split(os.pathsep)
    if part.strip()
)

# --- Backups ---
BACKUP_ROOT = _expand_path(os.environ.get("ANAMNESIS_BACKUP_ROOT", "~/anamnesis-backups"))
BACKUP_KEEP_LAST = int(os.environ.get("ANAMNESIS_BACKUP_KEEP_LAST", "10"))

# --- Embedding model ---
# Version token is baked into the collection name so multiple models can coexist.
EMBED_MODEL = os.environ.get(
    "ANAMNESIS_EMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
EMBED_DIM = 384
MODEL_TAG = os.environ.get("ANAMNESIS_MODEL_TAG", "mml12")
CHROMA_COLLECTION = os.environ.get("ANAMNESIS_CHROMA_COLLECTION", "history_turns")

# --- RRF / search defaults ---
RRF_K = 60
DEFAULT_TOP_K = 10
DEFAULT_POOL = 50

# --- Paths convenience ---
REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


def is_project_in_scope(project: str | None) -> bool:
    if not PROJECT_PREFIXES:
        return True
    if not project:
        return False
    project = _expand_path(project)
    return any(
        project == prefix or project.startswith(prefix + os.sep)
        for prefix in PROJECT_PREFIXES
    )


def local_embed_model_ready() -> bool:
    cache_dir = Path(FASTEMBED_CACHE)
    return any(cache_dir.rglob("model_optimized.onnx"))
