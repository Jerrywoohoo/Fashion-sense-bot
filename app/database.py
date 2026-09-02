"""SQLite-backed storage for user profiles and wardrobe garments.

Uses Python's built-in ``sqlite3`` module only — no ORM. Every function opens
its own short-lived connection via the ``_connect`` context manager, which
wraps ``sqlite3.connect(...)`` and guarantees the connection is closed (a
plain ``with sqlite3.connect(...) as conn:`` commits/rolls back but does
*not* close the connection, so we wrap it ourselves).

The garment lifecycle has three stages, matching the HITL (human-in-the-loop)
verification flow in ``handlers.py``:

1. ``insert_raw_garment`` — right after a photo is received: just the
   image path and caption, ``is_verified=0``.
2. ``update_garment_extracted_data`` — once AWS Bedrock returns structured
   metadata for that photo.
3. ``mark_garment_verified`` — once the user taps "Confirm" on the result.

Call ``init_db()`` once at startup before using any other function here.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterator, Optional

from .models import ExtractedGarment, GarmentExtractionResult

logger = logging.getLogger(__name__)

_default_db_path = "../data/wardrobe.db" if Path("../data/wardrobe.db").exists() else "data/wardrobe.db"
DEFAULT_DB_PATH = _default_db_path

# Module-level path set by init_db(). All connections use this.
_db_path: str = DEFAULT_DB_PATH

_USER_FIELDS = (
    "gender_frame",
    "height_cm",
    "weight_kg",
    "body_build",
    "proportions",
    "favorite_silhouettes",
    "thermal_preference",
)

_FIRST_ITEM_NUMBER = 101

# Columns added after the table's initial release. Kept separate so
# init_db() can ALTER TABLE existing databases that predate them, instead
# of requiring users to delete data/wardrobe.db by hand.
_GARMENT_MIGRATIONS: dict[str, str] = {
    "accent_colors": "accent_colors TEXT",
    "fabric_weight": "fabric_weight TEXT",
    "layering_role": "layering_role TEXT",
    "source_type": (
        "source_type TEXT DEFAULT 'single_item' "
        "CHECK (source_type IN ('single_item', 'ootd'))"
    ),
    "capture_id": "capture_id TEXT",
    "in_laundry": "in_laundry INTEGER DEFAULT 0",
}

_USER_MIGRATIONS: dict[str, str] = {
    "gender_frame": "gender_frame TEXT",
    "weight_kg": "weight_kg INTEGER",
    "proportions": "proportions TEXT",
    "favorite_silhouettes": "favorite_silhouettes TEXT",
    "thermal_preference": "thermal_preference TEXT",
}


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with dict-like row access and FKs enforced.

    Wraps ``sqlite3.connect(_db_path)`` in a context manager that both
    commits/rolls back (sqlite3's default `with` behavior) *and* closes the
    connection afterwards, which the bare ``with sqlite3.connect(...)``
    idiom does not do on its own.
    """
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _ensure_columns(
    conn: sqlite3.Connection, table: str, column_definitions: dict[str, str]
) -> None:
    """Add any columns in ``column_definitions`` missing from ``table``.

    Lets a database created before a schema change pick up new columns
    automatically. SQLite's ``ALTER TABLE ADD COLUMN`` only supports adding
    nullable columns, which matches every additive change this app makes.
    """
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for column, ddl in column_definitions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
            logger.info("Migrated schema: added %s.%s", table, column)


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    global _db_path
    _db_path = db_path
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    

    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                gender_frame TEXT,
                height_cm INTEGER,
                weight_kg INTEGER,
                body_build TEXT,
                proportions TEXT,
                favorite_silhouettes TEXT,
                thermal_preference TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_outfits (
                outfit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                occasion TEXT NOT NULL,
                item_ids TEXT NOT NULL,
                aesthetic TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
            """
        )
        _ensure_columns(conn, "users", _USER_MIGRATIONS)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS garments (
                item_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                image_path TEXT NOT NULL,
                category TEXT,
                sub_category TEXT,
                brand TEXT,
                color TEXT,
                accent_colors TEXT,
                silhouette_fit TEXT,
                fabric_weight TEXT,
                formality_tier INTEGER,
                layering_role TEXT,
                tags TEXT,
                user_caption TEXT,
                is_verified INTEGER DEFAULT 0,
                source_type TEXT DEFAULT 'single_item',
                capture_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
            """
        )
        _ensure_columns(conn, "garments", _GARMENT_MIGRATIONS)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS garment_appearances (
                appearance_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                image_path TEXT NOT NULL,
                source_type TEXT NOT NULL,
                user_caption TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (item_id) REFERENCES garments (item_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wear_history (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                item_ids TEXT NOT NULL,
                occasion TEXT NOT NULL,
                action TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
            """
        )
    logger.info("Database initialized at %s", db_path)

def upsert_user_profile(user_id: str, profile_data: dict[str, Any]) -> None:
    """Insert a user profile or update only the supplied profile fields."""
    fields = {k: v for k, v in profile_data.items() if k in _USER_FIELDS}
    if not fields:
        return

    if isinstance(fields.get("favorite_silhouettes"), (list, tuple, set)):
        fields["favorite_silhouettes"] = json.dumps(list(fields["favorite_silhouettes"]))

    with _connect() as conn:
        existing = conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()

        if existing is None:
            columns = ["user_id", *fields.keys()]
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO users ({', '.join(columns)}) VALUES ({placeholders})",
                [user_id, *fields.values()],
            )
        else:
            assignments = ", ".join(f"{column} = ?" for column in fields)
            conn.execute(
                f"UPDATE users SET {assignments} WHERE user_id = ?",
                [*fields.values(), user_id],
            )

def get_user_profile(user_id: str) -> Optional[dict[str, Any]]:
    """Return a user's profile as a dict, or ``None`` if it doesn't exist."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def _ensure_user_exists(conn: sqlite3.Connection, user_id: str) -> None:
    """Make sure a (possibly blank) user row exists, to satisfy the FK."""
    conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))


# ---------------------------------------------------------------------------
# Garments
# ---------------------------------------------------------------------------


def _next_item_id(conn: sqlite3.Connection) -> str:
    """Generate the next sequential item ID, e.g. ``'item_101'``, ``'item_102'``.

    Based on the last-inserted row rather than a row count, so it stays
    correct even after deletions.
    """
    row = conn.execute(
        "SELECT item_id FROM garments ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return f"item_{_FIRST_ITEM_NUMBER}"
    last_number = int(row["item_id"].rsplit("_", 1)[-1])
    return f"item_{last_number + 1}"


def insert_raw_garment(
    user_id: str, image_path: str, user_caption: Optional[str] = None
) -> str:
    """Insert a minimal garment row right after a photo is received.

    Called before vision extraction runs — only ``image_path`` and
    ``user_caption`` are known at this point. Returns the generated
    ``item_id`` (e.g. ``'item_101'``). If the user doesn't have a profile
    row yet (e.g. they sent a photo before running ``/profile``), a blank
    one is created automatically to satisfy the foreign key.
    """
    fields: dict[str, Any] = {"user_id": user_id, "image_path": image_path}
    if user_caption:
        fields["user_caption"] = user_caption

    with _connect() as conn:
        _ensure_user_exists(conn, user_id)
        item_id = _next_item_id(conn)
        columns = ["item_id", *fields.keys()]
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO garments ({', '.join(columns)}) VALUES ({placeholders})",
            [item_id, *fields.values()],
        )

    return item_id


def update_garment_extracted_data(item_id: str, data: ExtractedGarment) -> None:
    """Write AI-extracted fields onto an existing garment row.

    Does not touch ``is_verified`` — that's set separately once the user
    confirms the extraction via ``mark_garment_verified``.
    """
    fields = data.to_db_dict()
    for field in ("tags", "accent_colors"):
        if isinstance(fields.get(field), (list, tuple)):
            fields[field] = json.dumps(list(fields[field]))

    assignments = ", ".join(f"{column} = ?" for column in fields)
    with _connect() as conn:
        conn.execute(
            f"UPDATE garments SET {assignments} WHERE item_id = ?",
            [*fields.values(), item_id],
        )


def insert_capture_garments(
    user_id: str,
    image_path: str,
    capture_id: str,
    extraction: GarmentExtractionResult,
    user_caption: Optional[str] = None,
) -> list[str]:
    """Create pending garment rows and their initial photo appearances.

    Every garment in an OOTD receives its own wardrobe item while sharing the
    same source image and capture ID. The transaction prevents a partially
    inserted capture if a later garment fails validation or insertion.
    """
    item_ids: list[str] = []
    source_type = extraction.photo_type.value

    with _connect() as conn:
        _ensure_user_exists(conn, user_id)
        for garment in extraction.garments:
            item_id = _next_item_id(conn)
            fields = garment.to_db_dict()
            fields.update(
                {
                    "user_id": user_id,
                    "image_path": image_path,
                    "source_type": source_type,
                    "capture_id": capture_id,
                    "is_verified": 0,
                }
            )
            if user_caption:
                fields["user_caption"] = user_caption
            for field in ("tags", "accent_colors"):
                if isinstance(fields.get(field), (list, tuple)):
                    fields[field] = json.dumps(list(fields[field]))

            columns = ["item_id", *fields.keys()]
            placeholders = ", ".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO garments ({', '.join(columns)}) VALUES ({placeholders})",
                [item_id, *fields.values()],
            )
            conn.execute(
                """
                INSERT INTO garment_appearances
                    (item_id, user_id, image_path, source_type, user_caption)
                VALUES (?, ?, ?, ?, ?)
                """,
                (item_id, user_id, image_path, source_type, user_caption),
            )
            item_ids.append(item_id)

    return item_ids


def _normalise_match_text(value: Optional[str]) -> str:
    """Normalise a human label enough for predictable fuzzy comparisons."""
    return " ".join((value or "").lower().replace("-", " ").replace("_", " ").split())


def _is_similar_label(left: Optional[str], right: Optional[str]) -> bool:
    """Return whether two colour/style labels are equal or meaningfully close."""
    left_normalised = _normalise_match_text(left)
    right_normalised = _normalise_match_text(right)
    if not left_normalised or not right_normalised:
        return False
    if left_normalised == right_normalised:
        return True
    left_words = set(left_normalised.split())
    right_words = set(right_normalised.split())
    filler = {"a", "an", "the", "and", "with", "in", "of", "for", "men", "women", "unisex"}
    meaningful_left = left_words - filler
    meaningful_right = right_words - filler
    if meaningful_left and meaningful_right and (meaningful_left & meaningful_right):
        return True
    return (
        SequenceMatcher(None, left_normalised, right_normalised).ratio() >= 0.65
    )


def find_potential_duplicates(
    user_id: str, garment: ExtractedGarment
) -> list[dict[str, Any]]:
    """Return candidate garments with matching color and style for duplicate review."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM garments
            WHERE user_id = ? AND is_verified = 1 AND category = ?
            ORDER BY created_at DESC
            """,
            (user_id, garment.category),
        ).fetchall()

    matches: list[dict[str, Any]] = []
    for row in rows:
        candidate = _row_to_dict(row)
        colour_match = _is_similar_label(garment.primary_color, candidate.get("color"))
        subcategory_match = _is_similar_label(
            garment.sub_category, candidate.get("sub_category")
        )
        if colour_match and subcategory_match:
            candidate["match_reason"] = "matching colour and style"
            matches.append(candidate)
    return matches


def confirm_capture(capture_id: str) -> None:
    """Mark every remaining item from an upload capture as verified."""
    with _connect() as conn:
        conn.execute(
            "UPDATE garments SET is_verified = 1 WHERE capture_id = ?",
            (capture_id,),
        )


def link_garment_to_existing(
    new_item_id: str,
    existing_item_id: str,
    user_id: str,
    image_path: str,
    caption: Optional[str],
) -> None:
    """Replace a pending duplicate with another appearance of a saved item."""
    with _connect() as conn:
        pending = conn.execute(
            """
            SELECT source_type FROM garments
            WHERE item_id = ? AND user_id = ? AND is_verified = 0
            """,
            (new_item_id, user_id),
        ).fetchone()
        existing = conn.execute(
            "SELECT 1 FROM garments WHERE item_id = ? AND user_id = ?",
            (existing_item_id, user_id),
        ).fetchone()
        if pending is None or existing is None:
            raise ValueError("Cannot link garments that are not owned by this user.")

        conn.execute(
            """
            INSERT INTO garment_appearances
                (item_id, user_id, image_path, source_type, user_caption)
            VALUES (?, ?, ?, ?, ?)
            """,
            (existing_item_id, user_id, image_path, pending["source_type"], caption),
        )
        # The pending item's initial appearance is deleted by the FK cascade.
        conn.execute("DELETE FROM garments WHERE item_id = ?", (new_item_id,))


def delete_capture(capture_id: str) -> None:
    """Delete all pending/confirmed garment records created by a capture."""
    with _connect() as conn:
        # Explicitly remove appearances for compatibility with DBs whose FK
        # enforcement was disabled before this application opened them.
        conn.execute(
            """
            DELETE FROM garment_appearances
            WHERE item_id IN (SELECT item_id FROM garments WHERE capture_id = ?)
            """,
            (capture_id,),
        )
        conn.execute("DELETE FROM garments WHERE capture_id = ?", (capture_id,))


def get_capture_garments(capture_id: str) -> list[dict[str, Any]]:
    """Return the garments belonging to a capture, used to authorise callbacks."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM garments WHERE capture_id = ? ORDER BY rowid", (capture_id,)
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def image_path_is_referenced(image_path: str) -> bool:
    """Whether an image is still used by a garment or any appearance record."""
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM garments WHERE image_path = ?
            UNION ALL
            SELECT 1 FROM garment_appearances WHERE image_path = ?
            LIMIT 1
            """,
            (image_path, image_path),
        ).fetchone()
    return row is not None


def mark_garment_verified(item_id: str, is_verified: bool = True) -> None:
    """Flip a garment's verification flag, e.g. after the user taps Confirm."""
    with _connect() as conn:
        conn.execute(
            "UPDATE garments SET is_verified = ? WHERE item_id = ?",
            (int(is_verified), item_id),
        )


def get_garment_by_id(item_id: str) -> Optional[dict[str, Any]]:
    """Return a single garment by ID, or ``None`` if it doesn't exist."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM garments WHERE item_id = ?", (item_id,)
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def get_user_garments(
    user_id: str,
    verified_only: bool = True,
    category: Optional[str] = None,
    exclude_laundry: bool = False,
) -> list[dict[str, Any]]:
    """Return a user's garments, most recent first.

    ``verified_only`` defaults to True, matching ``/wardrobe``'s behavior
    of only showing items the user has confirmed. Pass False to include
    pending/unconfirmed items (e.g. for a debug or "pending review" view).
    If ``exclude_laundry`` is True, items currently in laundry are omitted.
    """
    query = "SELECT * FROM garments WHERE user_id = ?"
    params: list[Any] = [user_id]
    if verified_only:
        query += " AND is_verified = 1"
    if exclude_laundry:
        query += " AND (in_laundry IS NULL OR in_laundry = 0)"
    if category is not None:
        query += " AND category = ?"
        params.append(category)
    query += " ORDER BY CAST(SUBSTR(item_id, 6) AS INTEGER) ASC, item_id ASC"

    with _connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def delete_garment(item_id: str) -> bool:
    """Delete a garment by ID. Returns True if a row was removed.

    Note: this does not check ownership by itself — callers that need to
    scope deletion to the requesting user (e.g. a Telegram callback) should
    verify ``get_garment_by_id(item_id)["user_id"]`` matches first.
    """
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM garments WHERE item_id = ?", (item_id,))
        deleted = cursor.rowcount > 0
    return deleted


def delete_all_user_garments(user_id: str) -> tuple[int, list[str]]:
    """Delete all garments, appearances, outfits, and wear history for a user.

    Returns:
        A tuple of (deleted_count, list_of_image_paths).
    """
    with _connect() as conn:
        garment_rows = conn.execute(
            "SELECT item_id, image_path FROM garments WHERE user_id = ?", (user_id,)
        ).fetchall()
        appearance_rows = conn.execute(
            "SELECT image_path FROM garment_appearances WHERE user_id = ?", (user_id,)
        ).fetchall()

        all_image_paths = {row["image_path"] for row in garment_rows if row["image_path"]}
        all_image_paths.update(row["image_path"] for row in appearance_rows if row["image_path"])

        count = len(garment_rows)

        conn.execute("DELETE FROM garment_appearances WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM wear_history WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_outfits WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM garments WHERE user_id = ?", (user_id,))

        return count, list(all_image_paths)


def save_user_outfit(
    user_id: str, occasion: str, item_ids: list[str], aesthetic: Optional[str] = None
) -> int:
    """Save an OOTD combo as a preferred user outfit demonstration."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO user_outfits (user_id, occasion, item_ids, aesthetic)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, occasion.strip().lower(), json.dumps(item_ids), aesthetic),
        )
        return cursor.lastrowid or 0


def get_user_outfits(user_id: str, occasion_keyword: Optional[str] = None, limit: int = 3) -> list[dict[str, Any]]:
    """Retrieve the user's past outfits to use as prompt demonstrations."""
    with _connect() as conn:
        if occasion_keyword:
            rows = conn.execute(
                """
                SELECT * FROM user_outfits
                WHERE user_id = ? AND occasion LIKE ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (user_id, f"%{occasion_keyword.strip().lower()}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM user_outfits
                WHERE user_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

    results = []
    for r in rows:
        d = _row_to_dict(r)
        if isinstance(d.get("item_ids"), str):
            d["item_ids"] = json.loads(d["item_ids"])
        results.append(d)
    return results


def log_outfit_wear(
    user_id: str,
    item_ids: list[str],
    occasion: str,
    action: str = "worn",
) -> int:
    """Log an outfit wear or interaction into wear_history."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO wear_history (user_id, item_ids, occasion, action)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, json.dumps(item_ids), occasion.strip(), action),
        )
        return cursor.lastrowid or 0


def get_recently_worn_item_ids(user_id: str, days: int = 2) -> set[str]:
    """Get item IDs worn within the last `days` days."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT item_ids FROM wear_history
            WHERE user_id = ? 
              AND action IN ('worn', 'accepted_worn')
              AND created_at >= datetime('now', ?)
            """,
            (user_id, f"-{days} days"),
        ).fetchall()

    worn_ids: set[str] = set()
    for row in rows:
        raw = row["item_ids"]
        if isinstance(raw, str):
            try:
                items = json.loads(raw)
                if isinstance(items, list):
                    worn_ids.update(str(i) for i in items)
            except json.JSONDecodeError:
                pass
    return worn_ids


def get_recently_rejected_combos(user_id: str, occasion: str, hours: int = 12) -> list[set[str]]:
    """Get item ID sets of combos rejected recently for the same occasion."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT item_ids FROM wear_history
            WHERE user_id = ? 
              AND action = 'rejected'
              AND occasion LIKE ?
              AND created_at >= datetime('now', ?)
            """,
            (user_id, f"%{occasion.strip().lower()}%", f"-{hours} hours"),
        ).fetchall()

    rejected_combos: list[set[str]] = []
    for row in rows:
        raw = row["item_ids"]
        if isinstance(raw, str):
            try:
                items = json.loads(raw)
                if isinstance(items, list):
                    rejected_combos.append(set(str(i) for i in items))
            except json.JSONDecodeError:
                pass
    return rejected_combos


def set_garment_laundry_status(item_id: str, in_laundry: bool = True) -> None:
    """Set or clear the laundry status of an individual garment."""
    with _connect() as conn:
        conn.execute(
            "UPDATE garments SET in_laundry = ? WHERE item_id = ?",
            (1 if in_laundry else 0, item_id),
        )


def get_user_laundry_items(user_id: str) -> list[dict[str, Any]]:
    """Return all verified items currently marked in laundry for a user."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM garments
            WHERE user_id = ? AND is_verified = 1 AND in_laundry = 1
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def clear_user_laundry(user_id: str) -> int:
    """Reset all laundry flags for a user (e.g. after laundry day)."""
    with _connect() as conn:
        cursor = conn.execute(
            "UPDATE garments SET in_laundry = 0 WHERE user_id = ? AND in_laundry = 1",
            (user_id,),
        )
        return cursor.rowcount
