import sqlite3
import unicodedata
from pathlib import Path


def normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def connect(path: Path, initialize: bool = True) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.create_collation("UNICODE_NOCASE", lambda a, b: (normalize(a) > normalize(b)) - (normalize(a) < normalize(b)))
    db.execute("PRAGMA busy_timeout = 5000")
    db.execute("PRAGMA foreign_keys = ON")
    if not initialize:
        return db
    db.execute("PRAGMA journal_mode = WAL")
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
        CREATE TABLE IF NOT EXISTS item_images (
          id TEXT PRIMARY KEY,
          item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
          image_key TEXT NOT NULL UNIQUE,
          position INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS item_images_order ON item_images(item_id, position, id);
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
    # Keep SQLite's built-in NOCASE unchanged: changing an existing collation
    # without rebuilding its indexes silently corrupts lookups on old databases.
    with db:
        if "creation_key" not in {row["name"] for row in db.execute("PRAGMA table_info(items)")}:
            db.execute("ALTER TABLE items ADD COLUMN creation_key TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS items_creation_key ON items(owner_id, creation_key)")
        # Preserve existing photos without copying objects or resurrecting deleted images.
        db.execute("""INSERT INTO item_images(id, item_id, image_key, position)
            SELECT 'legacy-' || id, id, image_key, 0 FROM items
            WHERE image_key IS NOT NULL AND NOT EXISTS
              (SELECT 1 FROM item_images WHERE item_id = items.id)""")
        seen = {}
        survivors = []
        for row in db.execute("SELECT id, name FROM guests ORDER BY id").fetchall():
            cleaned = " ".join(row["name"].split())
            key = normalize(cleaned)
            if key in seen:
                db.execute("UPDATE reservations SET guest_id = ? WHERE guest_id = ?", (seen[key], row["id"]))
                db.execute("DELETE FROM guests WHERE id = ?", (row["id"],))
            else:
                seen[key] = row["id"]
                survivors.append((cleaned, row["id"]))
        for cleaned, guest_id in survivors:
            db.execute("UPDATE guests SET name = ? WHERE id = ?", (cleaned, guest_id))
        try:
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS users_username_unicode ON users(username COLLATE UNICODE_NOCASE)")
        except sqlite3.IntegrityError as exc:
            raise RuntimeError("Conflicting usernames: resolve case-insensitive duplicates before starting Wishlist") from exc
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS guests_name_unicode ON guests(name COLLATE UNICODE_NOCASE)")
        db.execute("CREATE INDEX IF NOT EXISTS items_owner_position ON items(owner_id, position, id)")
        db.execute("CREATE INDEX IF NOT EXISTS links_item ON links(item_id, id)")
    return db


def guest(db: sqlite3.Connection, name: str) -> sqlite3.Row:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValueError("empty name")
    db.execute("INSERT OR IGNORE INTO guests(name) VALUES (?)", (cleaned,))
    row = db.execute("SELECT id, name FROM guests WHERE name = ? COLLATE UNICODE_NOCASE", (cleaned,)).fetchone()
    return row
