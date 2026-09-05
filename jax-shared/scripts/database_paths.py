"""Shared database locations; relative environment paths are repository-relative."""
import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]

def database_path(variable, default):
    value = Path(os.environ.get(variable) or default).expanduser()
    return (value if value.is_absolute() else ROOT_DIR / value).resolve()

# These databases contain different schemas and must remain separate.
PROSPECT_HISTORY_DB = database_path("PROSPECT_HISTORY_DB", "data/scratch/prospect_history.db")
SQLITE_DB_PATH = database_path("SQLITE_DB_PATH", "jax-shared/data/prospects.db")
