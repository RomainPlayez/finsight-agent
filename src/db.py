"""
db.py : schema SQLite et fonctions d'acces pour stocker les donnees
financieres recuperees depuis l'API de la SEC EDGAR.
Deux tables :
- companies avec une ligne par entreprise (ticker, nom)
- financials avec une ligne par (entreprise, annee fiscale, metrique)
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "finance.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    cik     TEXT UNIQUE NOT NULL,
    ticker  TEXT UNIQUE NOT NULL,
    name    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS financials (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id   INTEGER NOT NULL REFERENCES companies(id),
    fiscal_year  INTEGER NOT NULL,
    metric       TEXT NOT NULL,
    value        REAL NOT NULL,
    unit         TEXT NOT NULL DEFAULT 'USD',
    UNIQUE(company_id, fiscal_year, metric)
);
"""


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_company(conn: sqlite3.Connection, cik: str, ticker: str, name: str) -> int:
    conn.execute(
        """
        INSERT INTO companies (cik, ticker, name)
        VALUES (?, ?, ?)
        ON CONFLICT(cik) DO UPDATE SET ticker=excluded.ticker, name=excluded.name
        """,
        (cik, ticker, name),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM companies WHERE cik = ?", (cik,)).fetchone()
    return row[0]


def upsert_financial(
    conn: sqlite3.Connection,
    company_id: int,
    fiscal_year: int,
    metric: str,
    value: float,
    unit: str = "USD",
) -> None:
    conn.execute(
        """
        INSERT INTO financials (company_id, fiscal_year, metric, value, unit)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(company_id, fiscal_year, metric)
        DO UPDATE SET value=excluded.value, unit=excluded.unit
        """,
        (company_id, fiscal_year, metric, value, unit),
    )
    conn.commit()


def get_metric(conn: sqlite3.Connection, ticker: str, fiscal_year: int, metric: str):
    row = conn.execute(
        """
        SELECT f.value, f.unit
        FROM financials f
        JOIN companies c ON c.id = f.company_id
        WHERE c.ticker = ? AND f.fiscal_year = ? AND f.metric = ?
        """,
        (ticker.upper(), fiscal_year, metric),
    ).fetchone()
    return row  # (value, unit) ou None


if __name__ == "__main__":
    conn = get_connection()
    init_db(conn)
    print(f"Base initialisee : {DB_PATH}")