"""Compatibility command: export CRM JSON without publishing it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jax-shared" / "scripts"))
from crm_autosync import main

if __name__ == "__main__":
    main(sync_remote=False)
