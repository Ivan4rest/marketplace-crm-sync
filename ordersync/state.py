"""Состояние синхронизации в SQLite: курсор, связи «заказ → сделка» и отправленные остатки."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS links (
    order_id TEXT PRIMARY KEY,
    deal_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS failures (
    order_id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    error TEXT NOT NULL,
    attempts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sent_stocks (sku TEXT PRIMARY KEY, quantity INTEGER NOT NULL);
"""


class State:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))

    def link(self, order_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM links WHERE order_id = ?", (order_id,)).fetchone()

    def save_link(self, order_id: str, deal_id: str, updated_at: str) -> None:
        with self.conn:
            self.conn.execute("INSERT OR REPLACE INTO links VALUES (?, ?, ?)", (order_id, deal_id, updated_at))
            self.conn.execute("DELETE FROM failures WHERE order_id = ?", (order_id,))

    def save_failure(self, order_id: str, updated_at: str, error: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO failures VALUES (?, ?, ?, 1) ON CONFLICT(order_id) DO UPDATE SET "
                "updated_at = excluded.updated_at, error = excluded.error, attempts = attempts + 1",
                (order_id, updated_at, error),
            )

    def failures(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM failures ORDER BY updated_at").fetchall()

    def sent_stocks(self) -> dict[str, int]:
        return {row["sku"]: row["quantity"] for row in self.conn.execute("SELECT * FROM sent_stocks")}

    def save_sent_stocks(self, stocks: dict[str, int]) -> None:
        with self.conn:
            self.conn.executemany("INSERT OR REPLACE INTO sent_stocks VALUES (?, ?)", stocks.items())
