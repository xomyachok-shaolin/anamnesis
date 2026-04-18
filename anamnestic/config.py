"""Single source of truth for paths, models, and collection names.

All modules should import from here rather than hardcoding.
"""
import os
from pathlib import Path


def _expand_path(value: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(value)))


# --- Data roots ---
DATA_DIR = Path(_expand_path(os.environ.get("ANAMNESTIC_DATA_DIR", "~/.claude-mem")))
DB_PATH = str(DATA_DIR / "claude-mem.db")
CHROMA_DIR = str(DATA_DIR / "semantic-chroma")
FASTEMBED_CACHE = str(DATA_DIR / "fastembed-models")
HEALTH_FILE = str(DATA_DIR / "health.json")

# --- Source roots ---
CC_ROOT = _expand_path(os.environ.get("ANAMNESTIC_CC_ROOT", "~/.claude/projects"))
CODEX_ROOT = _expand_path(os.environ.get("ANAMNESTIC_CODEX_ROOT", "~/.codex/sessions"))
VSCODE_WORKSPACE_ROOT = _expand_path(
    os.environ.get(
        "ANAMNESTIC_VSCODE_ROOT",
        "~/.config/Code/User/workspaceStorage",
    )
)
# Opt-in to VS Code Copilot chat ingest (disabled by default — workspace-specific).
INGEST_VSCODE_COPILOT = os.environ.get("ANAMNESTIC_INGEST_VSCODE_COPILOT", "0") == "1"
PROJECT_PREFIXES = tuple(
    _expand_path(part.strip())
    for part in os.environ.get("ANAMNESTIC_PROJECT_PREFIXES", "").split(os.pathsep)
    if part.strip()
)

# --- Backups ---
BACKUP_ROOT = _expand_path(os.environ.get("ANAMNESTIC_BACKUP_ROOT", "~/anamnestic-backups"))
BACKUP_KEEP_LAST = int(os.environ.get("ANAMNESTIC_BACKUP_KEEP_LAST", "10"))

# --- Embedding model ---
# Version token is baked into the collection name so multiple models can coexist.
EMBED_MODEL = os.environ.get(
    "ANAMNESTIC_EMBED_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)
EMBED_DIM = 384
MODEL_TAG = os.environ.get("ANAMNESTIC_MODEL_TAG", "mml12")
CHROMA_COLLECTION = os.environ.get("ANAMNESTIC_CHROMA_COLLECTION", "history_turns")

# --- RRF / search defaults ---
RRF_K = 60
DEFAULT_TOP_K = 10
DEFAULT_POOL = 50

# --- Importance scoring ---
IMPORTANCE_WEIGHT = float(os.environ.get("ANAMNESTIC_IMPORTANCE_WEIGHT", "0.3"))

# --- Cross-encoder reranking ---
RERANK_ENABLED = os.environ.get("ANAMNESTIC_RERANK", "1") == "1"
RERANK_MODEL = os.environ.get(
    "ANAMNESTIC_RERANK_MODEL",
    "Xenova/ms-marco-MiniLM-L-6-v2",
)
RERANK_TOP_N = int(os.environ.get("ANAMNESTIC_RERANK_TOP_N", "20"))

# --- Temporal retrieval ---
TEMPORAL_WEIGHT = float(os.environ.get("ANAMNESTIC_TEMPORAL_WEIGHT", "1.0"))

# --- Decay / consolidation ---
DECAY_ENABLED = os.environ.get("ANAMNESTIC_DECAY", "1") == "1"
DECAY_HALF_LIFE_DAYS = int(os.environ.get("ANAMNESTIC_DECAY_HALF_LIFE", "90"))
ARCHIVE_ENABLED = os.environ.get("ANAMNESTIC_ARCHIVE", "0") == "1"
ARCHIVE_AGE_DAYS = int(os.environ.get("ANAMNESTIC_ARCHIVE_AGE", "365"))

# --- Entity graph ---
GRAPH_WEIGHT = float(os.environ.get("ANAMNESTIC_GRAPH_WEIGHT", "0.5"))
GRAPH_MAX_HOPS = int(os.environ.get("ANAMNESTIC_GRAPH_MAX_HOPS", "2"))

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
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return False
    cache_dir = Path(FASTEMBED_CACHE)
    return any(cache_dir.rglob("model_optimized.onnx"))
