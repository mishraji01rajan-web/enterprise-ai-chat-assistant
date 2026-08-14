"""Container/first-run bootstrap: seed the SQL DB and ingest the knowledge
base only if they don't already exist, so a container restart doesn't wipe
data created after the initial seed (e.g. tickets created via the app).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings


def main() -> None:
    if not settings.sql_db_path.exists():
        print(f"[bootstrap] No SQL DB found at {settings.sql_db_path}, seeding...")
        from app.db.seed import seed

        seed()
    else:
        print(f"[bootstrap] SQL DB already exists at {settings.sql_db_path}, skipping seed.")

    if not settings.vector_db_path.exists() or not any(settings.vector_db_path.iterdir()):
        print(f"[bootstrap] No vector store found at {settings.vector_db_path}, ingesting knowledge base...")
        from app.rag.ingest import ingest_all

        count = ingest_all()
        print(f"[bootstrap] Ingested {count} chunks.")
    else:
        print(f"[bootstrap] Vector store already populated at {settings.vector_db_path}, skipping ingestion.")


if __name__ == "__main__":
    main()
