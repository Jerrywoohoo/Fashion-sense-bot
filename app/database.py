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
from .paths import resolve_image_path

logger = logging.getLogger(__name__)

_default_db_path = "../data/wardrobe.db" if Path("../data/wardrobe.db").exists() else "data/wardrobe.db"
DEFAULT_DB_PATH = _default_db_path

# Module-level path set by init_db(). All connections use this.
_db_path: str = DEFAULT_DB_PATH
POOL_USER_ID = "POOL_TEST_USER"

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

_USER_OUTFIT_MIGRATIONS: dict[str, str] = {
    "linked_item_ids": "linked_item_ids TEXT DEFAULT '[]'",
    "image_path": "image_path TEXT",
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
        _ensure_columns(conn, "user_outfits", _USER_OUTFIT_MIGRATIONS)
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
        backfill_confirmed_ootds(conn)
        deduplicate_user_outfits(conn)
    logger.info("Database initialized at %s", db_path)


def deduplicate_user_outfits(conn: sqlite3.Connection) -> None:
    """Consolidate duplicate user_outfits rows sharing the same items or images."""
    try:
        user_rows = conn.execute("SELECT DISTINCT user_id FROM user_outfits").fetchall()
        for u in user_rows:
            uid = u["user_id"]
            outfits = conn.execute(
                "SELECT outfit_id, item_ids, image_path, created_at FROM user_outfits WHERE user_id = ? ORDER BY outfit_id ASC",
                (uid,),
            ).fetchall()

            seen_items: dict[frozenset[str], dict[str, Any]] = {}
            seen_images: dict[str, dict[str, Any]] = {}
            to_delete: set[int] = set()

            for of in outfits:
                of_id = of["outfit_id"]
                img = of["image_path"]
                img_exists = bool(img and Path(img).is_file())
                try:
                    items = frozenset(json.loads(of["item_ids"]) if isinstance(of["item_ids"], str) else [])
                except Exception:
                    items = frozenset()

                # Check duplicate by identical item set
                if items and items in seen_items:
                    prev = seen_items[items]
                    prev_id = prev["outfit_id"]
                    prev_img_exists = prev["img_exists"]

                    if img_exists and not prev_img_exists:
                        # Current row has a valid image, previous didn't: keep current, delete previous
                        to_delete.add(prev_id)
                        seen_items[items] = {"outfit_id": of_id, "img_exists": img_exists}
                        if img:
                            seen_images[img] = {"outfit_id": of_id, "img_exists": img_exists}
                    else:
                        # Previous already has a valid image, or neither has one: delete current duplicate
                        to_delete.add(of_id)
                    continue

                # Check duplicate by identical image path
                if img and img in seen_images:
                    to_delete.add(of_id)
                    continue

                if items:
                    seen_items[items] = {"outfit_id": of_id, "img_exists": img_exists}
                if img:
                    seen_images[img] = {"outfit_id": of_id, "img_exists": img_exists}

            for del_id in to_delete:
                conn.execute("DELETE FROM user_outfits WHERE outfit_id = ?", (del_id,))
                logger.info("Deduplicated redundant user_outfit #%s for user %s", del_id, uid)
    except Exception:
        logger.exception("Error during deduplicate_user_outfits")


def backfill_confirmed_ootds(conn: sqlite3.Connection) -> None:
    """Ensure any verified OOTD captures without a user_outfits record are registered."""
    try:
        rows = conn.execute(
            """
            SELECT capture_id, user_id, MIN(image_path) as img, GROUP_CONCAT(item_id) as items
            FROM garments
            WHERE is_verified = 1 AND source_type = 'ootd' AND capture_id IS NOT NULL
            GROUP BY capture_id, user_id
            """
        ).fetchall()
        for r in rows:
            cid = r["capture_id"]
            uid = r["user_id"]
            img = r["img"]
            item_list = [x.strip() for x in r["items"].split(",") if x.strip()]
            if not item_list:
                continue

            # Resolve valid image path if primary img is missing on disk
            if img and not Path(img).is_file():
                for i_id in item_list:
                    grow = conn.execute(
                        "SELECT image_path FROM garments WHERE item_id = ? AND image_path IS NOT NULL LIMIT 1",
                        (i_id,),
                    ).fetchone()
                    if grow and grow["image_path"] and Path(grow["image_path"]).is_file():
                        img = grow["image_path"]
                        break

            # Check if already in user_outfits
            existing = conn.execute(
                "SELECT outfit_id, item_ids, image_path FROM user_outfits WHERE user_id = ?",
                (uid,),
            ).fetchall()
            already_saved = False
            for er in existing:
                try:
                    saved_ids = json.loads(er["item_ids"]) if isinstance(er["item_ids"], str) else []
                    if set(saved_ids) == set(item_list) or (img and er["image_path"] == img):
                        already_saved = True
                        if img and Path(img).is_file() and (not er["image_path"] or not Path(er["image_path"]).is_file()):
                            conn.execute("UPDATE user_outfits SET image_path = ? WHERE outfit_id = ?", (img, er["outfit_id"]))
                        break
                except Exception:
                    pass

            if not already_saved:
                # Check for any user caption to derive a better occasion if available
                cap_row = conn.execute(
                    "SELECT user_caption FROM garments WHERE capture_id = ? AND user_caption IS NOT NULL LIMIT 1",
                    (cid,),
                ).fetchone()
                occasion = "ootd"
                if cap_row and cap_row["user_caption"]:
                    occasion = cap_row["user_caption"].strip().lower()

                conn.execute(
                    """
                    INSERT INTO user_outfits (user_id, occasion, item_ids, aesthetic, linked_item_ids, image_path)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (uid, occasion, json.dumps(item_list), "casual", "[]", img),
                )
                logger.info("Backfilled missing user_outfit for verified OOTD capture %s (user %s)", cid, uid)
    except Exception:
        logger.exception("Error during backfill_confirmed_ootds")



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


def update_garment_extracted_data(
    item_id: str,
    data: ExtractedGarment,
    user_caption: Optional[str] = None,
) -> None:
    """Write AI-extracted fields onto an existing garment row.

    Does not touch ``is_verified`` — that's set separately once the user
    confirms the extraction via ``mark_garment_verified``.
    """
    fields = data.to_db_dict()
    for field in ("tags", "accent_colors"):
        if isinstance(fields.get(field), (list, tuple)):
            fields[field] = json.dumps(list(fields[field]))

    if user_caption is not None:
        fields["user_caption"] = user_caption

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
    user_id: Optional[str] = None,
    image_path: Optional[str] = None,
    caption: Optional[str] = None,
    user_caption: Optional[str] = None,
) -> bool:
    """Consolidate an item into an existing verified garment.

    Transfers any appearances (e.g. OOTDs) from new_item_id to existing_item_id,
    records the new appearance, updates any user_outfits referencing new_item_id,
    and removes the redundant new_item_id.
    """
    with _connect() as conn:
        item_to_link = conn.execute(
            """
            SELECT source_type, is_verified, image_path, user_caption, user_id FROM garments
            WHERE item_id = ?
            """,
            (new_item_id,),
        ).fetchone()
        existing = conn.execute(
            "SELECT user_id, source_type, image_path FROM garments WHERE item_id = ?",
            (existing_item_id,),
        ).fetchone()
        if item_to_link is None or existing is None:
            raise ValueError(f"Cannot link: {new_item_id} or {existing_item_id} does not exist.")

        effective_uid = existing["user_id"] or item_to_link["user_id"] or user_id
        if user_id is not None:
            allowed = {user_id, POOL_USER_ID, item_to_link["user_id"], existing["user_id"]}
            if item_to_link["user_id"] not in allowed and existing["user_id"] not in allowed:
                raise ValueError("Cannot link garments that are not owned by this user.")

        resolved_img = image_path or item_to_link["image_path"]
        cap_val = caption if caption is not None else user_caption
        resolved_cap = cap_val if cap_val is not None else item_to_link["user_caption"]

        # Re-point any existing appearances of new_item_id to existing_item_id
        conn.execute(
            """
            UPDATE garment_appearances
            SET item_id = ?
            WHERE item_id = ?
            """,
            (existing_item_id, new_item_id),
        )

        # Record this appearance for existing_item_id if image is available and not already present
        if resolved_img:
            already = conn.execute(
                "SELECT 1 FROM garment_appearances WHERE item_id = ? AND image_path = ?",
                (existing_item_id, resolved_img),
            ).fetchone()
            if not already:
                conn.execute(
                    """
                    INSERT INTO garment_appearances
                        (item_id, user_id, image_path, source_type, user_caption)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (existing_item_id, effective_uid, resolved_img, item_to_link["source_type"] or "single_item", resolved_cap),
                )

        # Update any user_outfits referencing new_item_id across all relevant user_ids
        outfit_rows = conn.execute(
            "SELECT outfit_id, item_ids, linked_item_ids FROM user_outfits WHERE user_id = ? OR user_id = ? OR user_id = ?",
            (effective_uid, item_to_link["user_id"], user_id),
        ).fetchall()
        for ofr in outfit_rows:
            try:
                of_items = json.loads(ofr["item_ids"]) if isinstance(ofr["item_ids"], str) else (ofr["item_ids"] or [])
                of_linked = json.loads(ofr["linked_item_ids"]) if ofr["linked_item_ids"] else []
                if new_item_id in of_items:
                    new_of_items = []
                    for i in of_items:
                        target = existing_item_id if i == new_item_id else i
                        if target not in new_of_items:
                            new_of_items.append(target)
                    of_items = new_of_items

                    if existing_item_id not in of_linked:
                        of_linked.append(existing_item_id)
                    conn.execute(
                        "UPDATE user_outfits SET item_ids = ?, linked_item_ids = ? WHERE outfit_id = ?",
                        (json.dumps(of_items), json.dumps(of_linked), ofr["outfit_id"]),
                    )
            except Exception:
                logger.exception("Failed updating user_outfit %s during link", ofr["outfit_id"])

        # Delete new_item_id from garments
        conn.execute("DELETE FROM garments WHERE item_id = ?", (new_item_id,))
        return True


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
    """Delete a garment by ID, cleaning its appearances and updating outfit records."""
    with _connect() as conn:
        conn.execute("DELETE FROM garment_appearances WHERE item_id = ?", (item_id,))
        cursor = conn.execute("DELETE FROM garments WHERE item_id = ?", (item_id,))
        deleted = cursor.rowcount > 0

        # Also remove item_id from any user_outfits referencing it
        rows = conn.execute(
            "SELECT outfit_id, item_ids, linked_item_ids FROM user_outfits WHERE item_ids LIKE ?",
            (f'%"{item_id}"%',),
        ).fetchall()
        for r in rows:
            of_id = r["outfit_id"]
            raw_items = r["item_ids"]
            raw_linked = r["linked_item_ids"]
            items = json.loads(raw_items) if isinstance(raw_items, str) else (raw_items or [])
            linked = json.loads(raw_linked) if isinstance(raw_linked, str) else (raw_linked or [])

            new_items = [x for x in items if x != item_id]
            new_linked = [x for x in linked if x != item_id]

            if new_items:
                conn.execute(
                    "UPDATE user_outfits SET item_ids = ?, linked_item_ids = ? WHERE outfit_id = ?",
                    (json.dumps(new_items), json.dumps(new_linked), of_id),
                )
            else:
                conn.execute("DELETE FROM user_outfits WHERE outfit_id = ?", (of_id,))

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

        total_outfits = conn.execute("SELECT COUNT(*) FROM user_outfits").fetchone()[0]
        if total_outfits == 0:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'user_outfits'")

        return count, list(all_image_paths)


def save_user_outfit(
    user_id: str,
    occasion: str,
    item_ids: list[str],
    aesthetic: Optional[str] = None,
    linked_item_ids: Optional[list[str]] = None,
    image_path: Optional[str] = None,
) -> int:
    """Save an OOTD combo as a preferred user outfit demonstration."""
    with _connect() as conn:
        _ensure_user_exists(conn, user_id)
        resolved_img = image_path
        if not resolved_img and item_ids:
            for i_id in item_ids:
                row = conn.execute(
                    "SELECT image_path FROM garment_appearances WHERE item_id = ? AND source_type = 'ootd' ORDER BY appearance_id DESC LIMIT 1",
                    (i_id,),
                ).fetchone()
                if row and row["image_path"]:
                    resolved_img = row["image_path"]
                    break
            if not resolved_img:
                for i_id in item_ids:
                    row = conn.execute(
                        "SELECT image_path FROM garments WHERE item_id = ? AND source_type = 'ootd' LIMIT 1",
                        (i_id,),
                    ).fetchone()
                    if row and row["image_path"]:
                        resolved_img = row["image_path"]
                        break

        # Check for existing outfit for this user to avoid duplicate entries
        existing_rows = conn.execute(
            "SELECT outfit_id, item_ids, occasion, image_path, created_at FROM user_outfits WHERE user_id = ? ORDER BY outfit_id ASC",
            (user_id,),
        ).fetchall()

        matched_outfit_id: Optional[int] = None
        target_item_set = set(item_ids)

        for er in existing_rows:
            try:
                er_items = json.loads(er["item_ids"]) if isinstance(er["item_ids"], str) else []
            except Exception:
                er_items = []

            # Condition 1: Identical image path
            if resolved_img and er["image_path"] == resolved_img:
                matched_outfit_id = er["outfit_id"]
                break

            # Condition 2: Identical set of items
            if er_items and set(er_items) == target_item_set:
                matched_outfit_id = er["outfit_id"]
                break

        if matched_outfit_id is not None:
            conn.execute(
                """
                UPDATE user_outfits
                SET occasion = ?, item_ids = ?, aesthetic = COALESCE(?, aesthetic),
                    linked_item_ids = ?, image_path = COALESCE(?, image_path)
                WHERE outfit_id = ?
                """,
                (
                    occasion.strip().lower(),
                    json.dumps(item_ids),
                    aesthetic,
                    json.dumps(linked_item_ids or []),
                    resolved_img,
                    matched_outfit_id,
                ),
            )
            return matched_outfit_id

        cursor = conn.execute(
            """
            INSERT INTO user_outfits (user_id, occasion, item_ids, aesthetic, linked_item_ids, image_path)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                occasion.strip().lower(),
                json.dumps(item_ids),
                aesthetic,
                json.dumps(linked_item_ids or []),
                resolved_img,
            ),
        )
        return cursor.lastrowid or 0


def get_user_outfits(user_id: str, occasion_keyword: Optional[str] = None, limit: int = 3) -> list[dict[str, Any]]:
    """Retrieve the user's past outfits to use as prompt demonstrations or wardrobe views."""
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
                try:
                    d["item_ids"] = json.loads(d["item_ids"])
                except Exception:
                    d["item_ids"] = []
            if isinstance(d.get("linked_item_ids"), str):
                try:
                    d["linked_item_ids"] = json.loads(d["linked_item_ids"])
                except Exception:
                    d["linked_item_ids"] = []
            elif d.get("linked_item_ids") is None:
                d["linked_item_ids"] = []

            if not d.get("image_path") and d.get("item_ids"):
                for i_id in d["item_ids"]:
                    app_row = conn.execute(
                        "SELECT image_path FROM garment_appearances WHERE item_id = ? AND source_type = 'ootd' ORDER BY appearance_id DESC LIMIT 1",
                        (i_id,),
                    ).fetchone()
                    if app_row and app_row["image_path"]:
                        d["image_path"] = app_row["image_path"]
                        break
            results.append(d)
        return results


def get_user_outfit_by_id(outfit_id: int, user_id: str) -> Optional[dict[str, Any]]:
    """Retrieve a single outfit by ID."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_outfits WHERE outfit_id = ? AND user_id = ?",
            (outfit_id, user_id),
        ).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        if isinstance(d.get("item_ids"), str):
            try:
                d["item_ids"] = json.loads(d["item_ids"])
            except Exception:
                d["item_ids"] = []
        if isinstance(d.get("linked_item_ids"), str):
            try:
                d["linked_item_ids"] = json.loads(d["linked_item_ids"])
            except Exception:
                d["linked_item_ids"] = []
        elif d.get("linked_item_ids") is None:
            d["linked_item_ids"] = []

        if not d.get("image_path") and d.get("item_ids"):
            for i_id in d["item_ids"]:
                app_row = conn.execute(
                    "SELECT image_path FROM garment_appearances WHERE item_id = ? AND source_type = 'ootd' ORDER BY appearance_id DESC LIMIT 1",
                    (i_id,),
                ).fetchone()
                if app_row and app_row["image_path"]:
                    d["image_path"] = app_row["image_path"]
                    break
        return d


def delete_user_outfit(
    outfit_id: int,
    user_id: str,
    allowed_user_ids: Optional[set[str]] = None,
    clean_unlinked_items: bool = True,
) -> bool:
    """Delete an OOTD record from user_outfits.

    When clean_unlinked_items is True, newly created garments belonging to this
    OOTD that are NOT linked to existing wardrobe pieces are removed from the
    wardrobe (unless referenced by another saved outfit). Previously existing /
    linked garments are preserved, but their appearance record in this OOTD is cleaned up.
    Renumbers remaining outfits so numbering remains contiguous starting from 1.
    """
    valid_uids = (allowed_user_ids or {user_id}) | {user_id}
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_outfits WHERE outfit_id = ?",
            (outfit_id,),
        ).fetchone()
        if not row:
            return False
        if row["user_id"] not in valid_uids:
            return False

        raw_items = row["item_ids"]
        item_ids = json.loads(raw_items) if isinstance(raw_items, str) else (raw_items or [])
        raw_linked = row["linked_item_ids"]
        linked_item_ids = set(json.loads(raw_linked) if isinstance(raw_linked, str) else (raw_linked or []))
        ootd_image = row["image_path"]

        images_to_check: set[str] = set()
        if ootd_image:
            images_to_check.add(ootd_image)

        if clean_unlinked_items and item_ids:
            for i_id in item_ids:
                if i_id in linked_item_ids:
                    # Linked garment was previously existing: keep in wardrobe, remove appearance for this OOTD image
                    if ootd_image:
                        conn.execute(
                            "DELETE FROM garment_appearances WHERE item_id = ? AND image_path = ? AND source_type = 'ootd'",
                            (i_id, ootd_image),
                        )
                else:
                    # Unlinked item: check if used in any other outfit
                    other_outfit = conn.execute(
                        "SELECT 1 FROM user_outfits WHERE outfit_id != ? AND (user_id = ? OR user_id = 'POOL_TEST_USER') AND item_ids LIKE ?",
                        (outfit_id, user_id, f'%"{i_id}"%'),
                    ).fetchone()
                    if not other_outfit:
                        g_row = conn.execute(
                            "SELECT image_path FROM garments WHERE item_id = ?", (i_id,)
                        ).fetchone()
                        if g_row and g_row["image_path"]:
                            images_to_check.add(g_row["image_path"])

                        conn.execute("DELETE FROM garment_appearances WHERE item_id = ?", (i_id,))
                        conn.execute("DELETE FROM garments WHERE item_id = ?", (i_id,))

        cursor = conn.execute(
            "DELETE FROM user_outfits WHERE outfit_id = ?",
            (outfit_id,),
        )
        deleted = cursor.rowcount > 0

        # Renumber remaining outfits chronologically and compact IDs
        rows = conn.execute(
            "SELECT outfit_id FROM user_outfits ORDER BY created_at ASC, outfit_id ASC"
        ).fetchall()
        if rows:
            for new_id, (old_id,) in enumerate(rows, 1):
                conn.execute("UPDATE user_outfits SET outfit_id = ? WHERE outfit_id = ?", (-new_id, old_id))
            conn.execute("UPDATE user_outfits SET outfit_id = -outfit_id WHERE outfit_id < 0")
            conn.execute("UPDATE sqlite_sequence SET seq = ? WHERE name = 'user_outfits'", (len(rows),))
        else:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = 'user_outfits'")

        # Clean up any unreferenced image files
        for img_path in images_to_check:
            ref = conn.execute(
                """
                SELECT 1 FROM garments WHERE image_path = ?
                UNION ALL
                SELECT 1 FROM garment_appearances WHERE image_path = ?
                UNION ALL
                SELECT 1 FROM user_outfits WHERE image_path = ?
                LIMIT 1
                """,
                (img_path, img_path, img_path),
            ).fetchone()
            if not ref:
                try:
                    resolved = resolve_image_path(img_path)
                    if resolved:
                        resolved.unlink(missing_ok=True)
                except OSError:
                    pass

        return deleted


def cleanup_stale_unverified_captures(max_age_hours: int = 24) -> int:
    """Delete unverified garments and appearances from abandoned captures older than max_age_hours.

    If max_age_hours is 0, deletes all currently unverified captures.
    """
    with _connect() as conn:
        if max_age_hours > 0:
            rows = conn.execute(
                """
                SELECT DISTINCT capture_id FROM garments
                WHERE is_verified = 0 AND capture_id IS NOT NULL
                  AND created_at <= datetime('now', ?)
                """,
                (f"-{max_age_hours} hours",),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT capture_id FROM garments
                WHERE is_verified = 0 AND capture_id IS NOT NULL
                """
            ).fetchall()

        cleaned_count = 0
        for r in rows:
            cap_id = r["capture_id"]
            if cap_id:
                img_rows = conn.execute(
                    "SELECT image_path FROM garments WHERE capture_id = ?", (cap_id,)
                ).fetchall()
                imgs = [row["image_path"] for row in img_rows if row["image_path"]]

                conn.execute(
                    "DELETE FROM garment_appearances WHERE item_id IN (SELECT item_id FROM garments WHERE capture_id = ?)",
                    (cap_id,),
                )
                del_c = conn.execute("DELETE FROM garments WHERE capture_id = ?", (cap_id,)).rowcount
                cleaned_count += del_c

                for img in imgs:
                    ref = conn.execute(
                        """
                        SELECT 1 FROM garments WHERE image_path = ?
                        UNION ALL
                        SELECT 1 FROM garment_appearances WHERE image_path = ?
                        UNION ALL
                        SELECT 1 FROM user_outfits WHERE image_path = ?
                        LIMIT 1
                        """,
                        (img, img, img),
                    ).fetchone()
                    if not ref:
                        try:
                            resolved = resolve_image_path(img)
                            if resolved:
                                resolved.unlink(missing_ok=True)
                        except OSError:
                            pass

        return cleaned_count


def update_user_outfit_occasion(
    outfit_id: int, occasion: str, user_id: Optional[str] = None
) -> bool:
    """Update the occasion label for an existing outfit."""
    with _connect() as conn:
        if user_id:
            cursor = conn.execute(
                "UPDATE user_outfits SET occasion = ? WHERE outfit_id = ? AND user_id = ?",
                (occasion.strip().lower(), outfit_id, user_id),
            )
        else:
            cursor = conn.execute(
                "UPDATE user_outfits SET occasion = ? WHERE outfit_id = ?",
                (occasion.strip().lower(), outfit_id),
            )
        return cursor.rowcount > 0



def log_outfit_wear(
    user_id: str,
    item_ids: list[str],
    occasion: str,
    action: str = "worn",
) -> int:
    """Log an outfit wear or interaction into wear_history."""
    with _connect() as conn:
        _ensure_user_exists(conn, user_id)
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
