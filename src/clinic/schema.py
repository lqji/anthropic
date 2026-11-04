from pathlib import Path
from typing import Final

from psycopg import Connection
from psycopg.rows import TupleRow

# Relative to the working directory, like CLINIC_DOCS_DIR (the image copies db/ to /app/db).
DEFAULT_DB_DIR: Final = Path("db")


def sql_files(db_dir: Path, kind: str) -> list[Path]:
    """The `db/<kind>/NN_*.sql` files in the order they run. `kind` is "schema" or "seed".

    A missing folder is an error, not an empty list: a wrong DB_DIR must stop the start.
    """
    folder = db_dir / kind
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} does not exist (set DB_DIR to the folder holding db/)")
    return sorted(folder.glob("*.sql"))


def apply_schema(conn: Connection[TupleRow], db_dir: Path = DEFAULT_DB_DIR) -> None:
    """Run every db/schema file in order. Each file is idempotent, so this runs on every start."""
    for path in sql_files(db_dir, "schema"):
        conn.execute(path.read_text(encoding="utf-8"))
