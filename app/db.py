import sqlite3
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY,
          sub TEXT NOT NULL UNIQUE,
          username TEXT NOT NULL UNIQUE,
          display_name TEXT NOT NULL DEFAULT '',
          avatar TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS guests (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL UNIQUE COLLATE NOCASE
        );
        CREATE TABLE IF NOT EXISTS items (
          id INTEGER PRIMARY KEY,
          owner_id INTEGER NOT NULL REFERENCES users(id),
          title TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '',
          price TEXT NOT NULL DEFAULT '',
          image_key TEXT,
          position INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS links (
          id INTEGER PRIMARY KEY,
          item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
          url TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reservations (
          item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
          guest_id INTEGER NOT NULL REFERENCES guests(id)
        );
        """
    )
    return db


def guest(db: sqlite3.Connection, name: str) -> sqlite3.Row:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValueError("empty name")
    db.execute("INSERT OR IGNORE INTO guests(name) VALUES (?)", (cleaned,))
    row = db.execute("SELECT id, name FROM guests WHERE name = ? COLLATE NOCASE", (cleaned,)).fetchone()
    db.commit()
    return row
