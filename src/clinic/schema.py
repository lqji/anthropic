from importlib.resources import files

from psycopg import Connection
from psycopg.rows import TupleRow


def schema_files() -> list[str]:
    """Names of the packaged schema files (src/clinic/sql/NN_*.sql) in the order they run."""
    return sorted(
        f.name for f in files("clinic").joinpath("sql").iterdir() if f.name.endswith(".sql")
    )


def apply_schema(conn: Connection[TupleRow]) -> None:
    """Run every schema file in order. Each file is idempotent, so this runs on every start."""
    sql_dir = files("clinic").joinpath("sql")
    for name in schema_files():
        conn.execute(sql_dir.joinpath(name).read_text(encoding="utf-8"))
