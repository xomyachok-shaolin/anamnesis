"""Restore claude-mem data from a backup tarball.

Usage:
    mem-ext restore <path/to/claude-mem-YYYYMMDD-HHMMSS.tar.gz>

Safety:
- Refuses to overwrite unless --force is passed.
- Stages into a temp dir then atomic-swaps DB and Chroma dir.
- Keeps the previous DB/Chroma as `<name>.pre-restore-<stamp>` until manually deleted.
"""
import os
import shutil
import tarfile
import tempfile
import time
from pathlib import Path

from mem_ext.config import CHROMA_DIR, DB_PATH, DATA_DIR


def run(tarball: str, force: bool = False) -> dict:
    tarball = os.path.expanduser(tarball)
    if not os.path.isfile(tarball):
        raise FileNotFoundError(tarball)

    data_dir = DATA_DIR
    stamp = time.strftime("%Y%m%d-%H%M%S")

    with tempfile.TemporaryDirectory(dir=str(data_dir)) as staging:
        staging = Path(staging)
        with tarfile.open(tarball, "r:gz") as tf:
            tf.extractall(staging)

        src_db = staging / "claude-mem.db"
        src_chroma = staging / "semantic-chroma"
        if not src_db.exists() or not src_chroma.exists():
            raise RuntimeError(
                f"Tarball missing expected members (claude-mem.db, semantic-chroma). "
                f"Found: {[p.name for p in staging.iterdir()]}"
            )

        # Safety: back up current state before swap
        db_target = Path(DB_PATH)
        chroma_target = Path(CHROMA_DIR)

        db_pre = db_target.with_suffix(f".db.pre-restore-{stamp}")
        chroma_pre = chroma_target.parent / f"{chroma_target.name}.pre-restore-{stamp}"

        if db_target.exists():
            if not force and db_pre.exists():
                raise RuntimeError(f"refuse to overwrite existing {db_pre}")
            shutil.move(str(db_target), str(db_pre))
        if chroma_target.exists():
            shutil.move(str(chroma_target), str(chroma_pre))

        # SQLite WAL/SHM sidecars are stale after restore — remove if present
        for sidecar in (db_target.with_suffix(".db-wal"), db_target.with_suffix(".db-shm")):
            if sidecar.exists():
                sidecar.unlink()

        shutil.move(str(src_db), str(db_target))
        shutil.move(str(src_chroma), str(chroma_target))

        return {
            "restored_from": tarball,
            "db": str(db_target),
            "chroma": str(chroma_target),
            "prev_db_saved_as": str(db_pre) if db_pre.exists() else None,
            "prev_chroma_saved_as": str(chroma_pre) if chroma_pre.exists() else None,
        }


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    info = run(sys.argv[1], force="--force" in sys.argv)
    print(json.dumps(info, indent=2, ensure_ascii=False))
