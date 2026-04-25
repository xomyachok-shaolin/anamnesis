"""Shared Chroma client helpers."""

from anamnestic.config import CHROMA_DIR


def persistent_client():
    import chromadb

    try:
        from chromadb.config import Settings

        return chromadb.PersistentClient(
            path=CHROMA_DIR,
            settings=Settings(anonymized_telemetry=False),
        )
    except TypeError:
        return chromadb.PersistentClient(path=CHROMA_DIR)
