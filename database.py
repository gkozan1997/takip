"""SQLite persistence layer for İade Takip."""

from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from config import (
    DEFAULT_DB_PATH,
    TZ_ISTANBUL,
    VALID_CONDITIONS,
    VALID_STATUSES,
)


def now_iso() -> str:
    """Return current Istanbul timestamp in ISO format (seconds precision)."""
    return datetime.now(TZ_ISTANBUL).strftime("%Y-%m-%d %H:%M:%S")


def generate_return_code() -> str:
    """Generate a unique human-readable return tracking code."""
    date_part = datetime.now(TZ_ISTANBUL).strftime("%Y%m%d")
    suffix = secrets.token_hex(2).upper()
    return f"IAD-{date_part}-{suffix}"


class ReturnDatabase:
    """Encapsulates all SQLite database operations for returns, history, and evidence."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Yield a configured SQLite connection with foreign keys and busy timeout enabled."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Create required tables and indexes if they do not exist."""
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS returns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    return_code TEXT NOT NULL UNIQUE,
                    order_no TEXT DEFAULT '',
                    tracking_no TEXT DEFAULT '',
                    barcode TEXT DEFAULT '',
                    cargo_company TEXT DEFAULT '',
                    marketplace TEXT DEFAULT 'Diğer',
                    customer_name TEXT DEFAULT '',
                    customer_phone TEXT DEFAULT '',
                    product_name TEXT NOT NULL,
                    sku TEXT DEFAULT '',
                    quantity INTEGER NOT NULL DEFAULT 1,
                    refund_amount REAL NOT NULL DEFAULT 0,
                    return_reason TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'beklemede',
                    condition_grade TEXT NOT NULL DEFAULT 'belirtilmedi',
                    notes TEXT DEFAULT '',
                    return_date TEXT DEFAULT '',
                    received_at TEXT DEFAULT '',
                    resolved_at TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_returns_status ON returns(status);
                CREATE INDEX IF NOT EXISTS idx_returns_marketplace ON returns(marketplace);
                CREATE INDEX IF NOT EXISTS idx_returns_tracking ON returns(tracking_no);
                CREATE INDEX IF NOT EXISTS idx_returns_barcode ON returns(barcode);
                CREATE INDEX IF NOT EXISTS idx_returns_order_no ON returns(order_no);

                CREATE TABLE IF NOT EXISTS return_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    return_id INTEGER NOT NULL,
                    old_status TEXT DEFAULT '',
                    new_status TEXT NOT NULL,
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (return_id) REFERENCES returns(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_history_return_id ON return_history(return_id);

                CREATE TABLE IF NOT EXISTS return_evidence (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    return_id INTEGER NOT NULL,
                    file_name TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    mime_type TEXT DEFAULT 'application/octet-stream',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    caption TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (return_id) REFERENCES returns(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_evidence_return_id ON return_evidence(return_id);
                """
            )

    @staticmethod
    def _clean_text(val: Any, default: str = "") -> str:
        if val is None:
            return default
        return str(val).strip()

    @staticmethod
    def _clean_int(val: Any, default: int = 1) -> int:
        try:
            parsed = int(float(val))
            return max(1, parsed)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _clean_float(val: Any, default: float = 0.0) -> float:
        if val is None or val == "":
            return default
        if isinstance(val, str):
            val = val.replace("₺", "").replace("TL", "").replace(" ", "").strip()
            if "," in val and "." in val:
                val = val.replace(".", "").replace(",", ".")
            elif "," in val:
                val = val.replace(",", ".")
        try:
            return round(max(0.0, float(val)), 2)
        except (TypeError, ValueError):
            return default

    def _normalize_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        status = self._clean_text(data.get("status"), "beklemede").lower()
        if status not in VALID_STATUSES:
            status = "beklemede"

        condition = self._clean_text(data.get("condition_grade"), "belirtilmedi").lower()
        if condition not in VALID_CONDITIONS:
            condition = "belirtilmedi"

        return {
            "order_no": self._clean_text(data.get("order_no")),
            "tracking_no": self._clean_text(data.get("tracking_no")),
            "barcode": self._clean_text(data.get("barcode")),
            "cargo_company": self._clean_text(data.get("cargo_company")),
            "marketplace": self._clean_text(data.get("marketplace"), "Diğer") or "Diğer",
            "customer_name": self._clean_text(data.get("customer_name")),
            "customer_phone": self._clean_text(data.get("customer_phone")),
            "product_name": self._clean_text(data.get("product_name")),
            "sku": self._clean_text(data.get("sku")),
            "quantity": self._clean_int(data.get("quantity"), 1),
            "refund_amount": self._clean_float(data.get("refund_amount"), 0.0),
            "return_reason": self._clean_text(data.get("return_reason")),
            "status": status,
            "condition_grade": condition,
            "notes": self._clean_text(data.get("notes")),
            "return_date": self._clean_text(data.get("return_date")) or now_iso()[:10],
        }

    def create_return(self, data: dict[str, Any], history_note: str = "Kayıt oluşturuldu") -> dict[str, Any]:
        """Insert a new return record and initial audit entry."""
        fields = self._normalize_payload(data)
        if not fields["product_name"]:
            raise ValueError("Ürün adı zorunludur.")

        timestamp = now_iso()
        return_code = self._clean_text(data.get("return_code")) or generate_return_code()
        received_at = timestamp if fields["status"] in {"teslim_alindi", "inceleniyor", "servise_teslim", "onaylandi", "reddedildi", "iade_tamamlandi"} else ""
        resolved_at = timestamp if fields["status"] in {"onaylandi", "reddedildi", "iade_tamamlandi"} else ""

        with self.connect() as conn:
            # Ensure unique return_code in case of same-second collision
            while conn.execute("SELECT 1 FROM returns WHERE return_code = ?", (return_code,)).fetchone():
                return_code = generate_return_code()

            cursor = conn.execute(
                """
                INSERT INTO returns (
                    return_code, order_no, tracking_no, barcode, cargo_company,
                    marketplace, customer_name, customer_phone, product_name, sku,
                    quantity, refund_amount, return_reason, status, condition_grade,
                    notes, return_date, received_at, resolved_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    return_code,
                    fields["order_no"],
                    fields["tracking_no"],
                    fields["barcode"],
                    fields["cargo_company"],
                    fields["marketplace"],
                    fields["customer_name"],
                    fields["customer_phone"],
                    fields["product_name"],
                    fields["sku"],
                    fields["quantity"],
                    fields["refund_amount"],
                    fields["return_reason"],
                    fields["status"],
                    fields["condition_grade"],
                    fields["notes"],
                    fields["return_date"],
                    received_at,
                    resolved_at,
                    timestamp,
                    timestamp,
                ),
            )
            return_id = cursor.lastrowid
            conn.execute(
                """
                INSERT INTO return_history (return_id, old_status, new_status, note, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (return_id, "", fields["status"], history_note, timestamp),
            )

        return self.get_return(return_id)  # type: ignore[return-value]

    def update_return(self, return_id: int, data: dict[str, Any], history_note: str = "") -> dict[str, Any] | None:
        """Update an existing return record and log status change if status changed."""
        existing = self.get_return(return_id)
        if not existing:
            return None

        merged = {**existing, **data}
        fields = self._normalize_payload(merged)
        if not fields["product_name"]:
            raise ValueError("Ürün adı zorunludur.")

        timestamp = now_iso()
        old_status = existing["status"]
        new_status = fields["status"]

        received_at = existing["received_at"]
        if not received_at and new_status in {"teslim_alindi", "inceleniyor", "servise_teslim", "onaylandi", "reddedildi", "iade_tamamlandi"}:
            received_at = timestamp

        resolved_at = existing["resolved_at"]
        if new_status in {"onaylandi", "reddedildi", "iade_tamamlandi"} and not resolved_at:
            resolved_at = timestamp
        elif new_status in {"beklemede", "kargoda", "teslim_alindi", "inceleniyor", "servise_teslim"}:
            resolved_at = ""

        with self.connect() as conn:
            conn.execute(
                """
                UPDATE returns SET
                    order_no = ?, tracking_no = ?, barcode = ?, cargo_company = ?,
                    marketplace = ?, customer_name = ?, customer_phone = ?, product_name = ?,
                    sku = ?, quantity = ?, refund_amount = ?, return_reason = ?,
                    status = ?, condition_grade = ?, notes = ?, return_date = ?,
                    received_at = ?, resolved_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    fields["order_no"],
                    fields["tracking_no"],
                    fields["barcode"],
                    fields["cargo_company"],
                    fields["marketplace"],
                    fields["customer_name"],
                    fields["customer_phone"],
                    fields["product_name"],
                    fields["sku"],
                    fields["quantity"],
                    fields["refund_amount"],
                    fields["return_reason"],
                    new_status,
                    fields["condition_grade"],
                    fields["notes"],
                    fields["return_date"],
                    received_at,
                    resolved_at,
                    timestamp,
                    return_id,
                ),
            )
            if old_status != new_status or history_note:
                conn.execute(
                    """
                    INSERT INTO return_history (return_id, old_status, new_status, note, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        return_id,
                        old_status,
                        new_status,
                        history_note or "Durum güncellendi",
                        timestamp,
                    ),
                )

        return self.get_return(return_id)

    def bulk_update_status(self, ids: list[int], new_status: str, note: str = "") -> int:
        """Update status for multiple return records at once."""
        if new_status not in VALID_STATUSES or not ids:
            return 0
        updated = 0
        for rid in ids:
            res = self.update_return(rid, {"status": new_status}, history_note=note or "Toplu durum güncellemesi")
            if res:
                updated += 1
        return updated

    def delete_return(self, return_id: int) -> list[str]:
        """Delete a return record and return stored evidence filenames for disk cleanup."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT stored_name FROM return_evidence WHERE return_id = ?",
                (return_id,),
            ).fetchall()
            stored_files = [r["stored_name"] for r in rows]
            conn.execute("DELETE FROM returns WHERE id = ?", (return_id,))
        return stored_files

    def get_return(self, return_id: int) -> dict[str, Any] | None:
        """Fetch a single return record with its history and evidence list."""
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT r.*,
                       (SELECT COUNT(*) FROM return_evidence e WHERE e.return_id = r.id) AS evidence_count
                FROM returns r
                WHERE r.id = ?
                """,
                (return_id,),
            ).fetchone()
            if not row:
                return None

            item = dict(row)
            history_rows = conn.execute(
                "SELECT * FROM return_history WHERE return_id = ? ORDER BY id DESC",
                (return_id,),
            ).fetchall()
            evidence_rows = conn.execute(
                "SELECT * FROM return_evidence WHERE return_id = ? ORDER BY id DESC",
                (return_id,),
            ).fetchall()

            item["history"] = [dict(h) for h in history_rows]
            item["evidence"] = [dict(e) for e in evidence_rows]
            return item

    def list_returns(
        self,
        search: str = "",
        status: str = "",
        marketplace: str = "",
        reason: str = "",
        condition: str = "",
        date_from: str = "",
        date_to: str = "",
        has_evidence: str = "",
    ) -> list[dict[str, Any]]:
        """Query return records with flexible filters."""
        clauses: list[str] = ["1=1"]
        params: list[Any] = []

        if search:
            q = f"%{search.strip()}%"
            clauses.append(
                """(
                    r.return_code LIKE ? OR r.order_no LIKE ? OR r.tracking_no LIKE ?
                    OR r.barcode LIKE ? OR r.customer_name LIKE ? OR r.customer_phone LIKE ?
                    OR r.product_name LIKE ? OR r.sku LIKE ? OR r.notes LIKE ?
                )"""
            )
            params.extend([q] * 9)

        if status and status != "all":
            clauses.append("r.status = ?")
            params.append(status)

        if marketplace and marketplace != "all":
            clauses.append("r.marketplace = ?")
            params.append(marketplace)

        if reason and reason != "all":
            clauses.append("r.return_reason = ?")
            params.append(reason)

        if condition and condition != "all":
            clauses.append("r.condition_grade = ?")
            params.append(condition)

        if date_from:
            clauses.append("r.return_date >= ?")
            params.append(date_from)

        if date_to:
            clauses.append("r.return_date <= ?")
            params.append(date_to)

        if has_evidence == "yes":
            clauses.append("EXISTS (SELECT 1 FROM return_evidence e WHERE e.return_id = r.id)")
        elif has_evidence == "no":
            clauses.append("NOT EXISTS (SELECT 1 FROM return_evidence e WHERE e.return_id = r.id)")

        sql = f"""
            SELECT r.*,
                   (SELECT COUNT(*) FROM return_evidence e WHERE e.return_id = r.id) AS evidence_count
            FROM returns r
            WHERE {' AND '.join(clauses)}
            ORDER BY r.id DESC
        """
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def lookup_by_scan(self, code: str) -> list[dict[str, Any]]:
        """Exact-first then partial lookup by return_code, tracking_no, barcode, order_no, or sku."""
        cleaned = code.strip()
        if not cleaned:
            return []

        with self.connect() as conn:
            exact = conn.execute(
                """
                SELECT r.*,
                       (SELECT COUNT(*) FROM return_evidence e WHERE e.return_id = r.id) AS evidence_count
                FROM returns r
                WHERE LOWER(r.return_code) = LOWER(?)
                   OR LOWER(r.tracking_no) = LOWER(?)
                   OR LOWER(r.barcode) = LOWER(?)
                   OR LOWER(r.order_no) = LOWER(?)
                   OR LOWER(r.sku) = LOWER(?)
                ORDER BY r.id DESC
                """,
                (cleaned, cleaned, cleaned, cleaned, cleaned),
            ).fetchall()
            if exact:
                return [dict(r) for r in exact]

            like_q = f"%{cleaned}%"
            partial = conn.execute(
                """
                SELECT r.*,
                       (SELECT COUNT(*) FROM return_evidence e WHERE e.return_id = r.id) AS evidence_count
                FROM returns r
                WHERE r.return_code LIKE ? OR r.tracking_no LIKE ? OR r.barcode LIKE ?
                   OR r.order_no LIKE ? OR r.sku LIKE ?
                ORDER BY r.id DESC
                LIMIT 20
                """,
                (like_q, like_q, like_q, like_q, like_q),
            ).fetchall()
            return [dict(r) for r in partial]

    def find_existing_for_import(self, order_no: str, tracking_no: str) -> dict[str, Any] | None:
        """Check whether an imported row matches an existing order_no + tracking_no."""
        order_no = order_no.strip()
        tracking_no = tracking_no.strip()
        if not order_no and not tracking_no:
            return None

        with self.connect() as conn:
            if tracking_no:
                row = conn.execute(
                    "SELECT * FROM returns WHERE tracking_no = ? LIMIT 1",
                    (tracking_no,),
                ).fetchone()
                if row:
                    return dict(row)
            if order_no:
                row = conn.execute(
                    "SELECT * FROM returns WHERE order_no = ? LIMIT 1",
                    (order_no,),
                ).fetchone()
                if row:
                    return dict(row)
        return None

    def add_evidence(
        self,
        return_id: int,
        file_name: str,
        stored_name: str,
        mime_type: str,
        file_size: int,
        caption: str = "",
    ) -> dict[str, Any]:
        """Attach an evidence record to a return."""
        timestamp = now_iso()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO return_evidence (return_id, file_name, stored_name, mime_type, file_size, caption, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (return_id, file_name, stored_name, mime_type, file_size, caption.strip(), timestamp),
            )
            ev_id = cursor.lastrowid
            conn.execute(
                """
                INSERT INTO return_history (return_id, old_status, new_status, note, created_at)
                SELECT id, status, status, ?, ? FROM returns WHERE id = ?
                """,
                (f"Kanıt eklendi: {file_name}", timestamp, return_id),
            )
            conn.execute("UPDATE returns SET updated_at = ? WHERE id = ?", (timestamp, return_id))
            row = conn.execute("SELECT * FROM return_evidence WHERE id = ?", (ev_id,)).fetchone()
            return dict(row)

    def get_evidence(self, evidence_id: int) -> dict[str, Any] | None:
        """Fetch a single evidence metadata record."""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM return_evidence WHERE id = ?", (evidence_id,)).fetchone()
            return dict(row) if row else None

    def delete_evidence(self, evidence_id: int) -> dict[str, Any] | None:
        """Delete an evidence row and return its metadata for disk cleanup."""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM return_evidence WHERE id = ?", (evidence_id,)).fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM return_evidence WHERE id = ?", (evidence_id,))
            return dict(row)

    def get_dashboard_stats(self) -> dict[str, Any]:
        """Compute summary KPIs and breakdown distributions for the dashboard."""
        with self.connect() as conn:
            summary_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_count,
                    COALESCE(SUM(quantity), 0) AS total_units,
                    COALESCE(SUM(refund_amount), 0) AS total_amount,
                    SUM(CASE WHEN status IN ('beklemede', 'kargoda') THEN 1 ELSE 0 END) AS pending_count,
                    SUM(CASE WHEN status IN ('teslim_alindi', 'inceleniyor', 'servise_teslim') THEN 1 ELSE 0 END) AS review_count,
                    SUM(CASE WHEN status IN ('onaylandi', 'iade_tamamlandi') THEN 1 ELSE 0 END) AS approved_count,
                    SUM(CASE WHEN status = 'reddedildi' THEN 1 ELSE 0 END) AS rejected_count,
                    COALESCE(SUM(CASE WHEN status IN ('onaylandi', 'iade_tamamlandi') THEN refund_amount ELSE 0 END), 0) AS approved_amount
                FROM returns
                """
            ).fetchone()

            by_status = [
                dict(r)
                for r in conn.execute(
                    "SELECT status, COUNT(*) AS count, COALESCE(SUM(refund_amount), 0) AS amount FROM returns GROUP BY status"
                ).fetchall()
            ]
            by_marketplace = [
                dict(r)
                for r in conn.execute(
                    "SELECT marketplace, COUNT(*) AS count, COALESCE(SUM(refund_amount), 0) AS amount FROM returns GROUP BY marketplace ORDER BY count DESC"
                ).fetchall()
            ]
            by_reason = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT CASE WHEN TRIM(return_reason) = '' THEN 'Belirtilmedi' ELSE return_reason END AS reason,
                           COUNT(*) AS count
                    FROM returns
                    GROUP BY reason
                    ORDER BY count DESC
                    LIMIT 8
                    """
                ).fetchall()
            ]
            recent_history = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT h.*, ret.return_code, ret.product_name
                    FROM return_history h
                    JOIN returns ret ON ret.id = h.return_id
                    ORDER BY h.id DESC
                    LIMIT 10
                    """
                ).fetchall()
            ]

        return {
            "summary": dict(summary_row) if summary_row else {},
            "by_status": by_status,
            "by_marketplace": by_marketplace,
            "by_reason": by_reason,
            "recent_history": recent_history,
        }
