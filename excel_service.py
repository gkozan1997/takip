"""Excel and CSV import/export service for İade Takip."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
import pandas as pd

from config import CONDITION_OPTIONS, STATUS_MAP

# Canonical field -> candidate header keywords (lowercase, Turkish normalized)
FIELD_ALIASES: dict[str, list[str]] = {
    "order_no": ["sipariş no", "siparis no", "sipariş numarası", "order no", "order number", "paket no"],
    "tracking_no": ["takip no", "kargo takip", "iade kodu", "iade kargo kodu", "tracking", "gönderi no", "kargo no"],
    "barcode": ["barkod", "gtin", "ean", "barcode"],
    "cargo_company": ["kargo", "kargo firması", "kargo şirketi", "taşıyıcı", "carrier"],
    "marketplace": ["pazaryeri", "pazar yeri", "kanal", "platform", "marketplace", "mağaza"],
    "customer_name": ["müşteri", "müşteri adı", "alıcı", "ad soyad", "customer", "buyer"],
    "customer_phone": ["telefon", "tel", "gsm", "phone"],
    "product_name": ["ürün adı", "ürün", "urun adi", "urun", "ürün ismi", "product", "product name"],
    "sku": ["stok kodu", "sku", "ürün kodu", "model kodu", "tedarikçi stok kodu"],
    "quantity": ["adet", "miktar", "qty", "quantity", "sipariş adedi"],
    "refund_amount": ["tutar", "iade tutarı", "fiyat", "bedel", "amount", "price", "toplam"],
    "return_reason": ["iade nedeni", "sebep", "neden", "iade sebebi", "reason", "açıklama"],
    "status": ["durum", "iade durumu", "statü", "status"],
    "return_date": ["tarih", "iade tarihi", "talep tarihi", "sipariş tarihi", "date"],
    "notes": ["not", "notlar", "inceleme notu", "notes"],
}

TR_STATUS_NORMALIZATION: list[tuple[str, str]] = [
    ("tamam", "iade_tamamlandi"),
    ("ödeme", "iade_tamamlandi"),
    ("odeme", "iade_tamamlandi"),
    ("onay", "onaylandi"),
    ("kabul", "onaylandi"),
    ("red", "reddedildi"),
    ("iptal", "reddedildi"),
    ("incele", "inceleniyor"),
    ("kontrol", "inceleniyor"),
    ("servis", "servise_teslim"),
    ("teslim", "teslim_alindi"),
    ("ulaş", "teslim_alindi"),
    ("ulas", "teslim_alindi"),
    ("kargo", "kargoda"),
    ("yol", "kargoda"),
    ("bekle", "beklemede"),
    ("talep", "beklemede"),
]

CONDITION_LABELS = {c["key"]: c["label"] for c in CONDITION_OPTIONS}


def _norm_header(text: Any) -> str:
    if text is None:
        return ""
    s = str(text).strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_status_value(raw: Any) -> str:
    """Map free-text status values from marketplace exports to canonical status keys."""
    if not raw:
        return "beklemede"
    text = str(raw).strip().lower()
    if text in STATUS_MAP:
        return text
    for needle, key in TR_STATUS_NORMALIZATION:
        if needle in text:
            return key
    return "beklemede"


def read_tabular_file(file_bytes: bytes, filename: str) -> list[list[str]]:
    """Read xlsx, xls, or csv bytes into a 2D list of stripped string cells."""
    ext = Path(filename).suffix.lower()

    if ext == ".csv":
        decoded = ""
        for enc in ("utf-8-sig", "utf-8", "cp1254", "latin1"):
            try:
                decoded = file_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        sample = decoded[:2048]
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        reader = csv.reader(io.StringIO(decoded), delimiter=delimiter)
        return [[str(c).strip() for c in row] for row in reader if any(str(c).strip() for c in row)]

    if ext == ".xls":
        df = pd.read_excel(io.BytesIO(file_bytes), header=None, dtype=str).fillna("")
        return [
            [str(v).strip() for v in row]
            for row in df.values.tolist()
            if any(str(v).strip() for v in row)
        ]

    # Default: .xlsx / .xlsm
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    ws = wb.active
    rows: list[list[str]] = []
    for row in ws.iter_rows(values_only=True):
        cells = [("" if v is None else str(v).strip()) for v in row]
        if any(cells):
            rows.append(cells)
    wb.close()
    return rows


def detect_headers_and_mapping(rows: list[list[str]]) -> tuple[int, list[str], dict[str, int]]:
    """Locate the header row within the first 6 rows and infer field-to-column mapping."""
    if not rows:
        return 0, [], {}

    best_row_idx = 0
    best_score = -1
    all_aliases = [alias for aliases in FIELD_ALIASES.values() for alias in aliases]

    for idx, row in enumerate(rows[:6]):
        score = 0
        for cell in row:
            norm = _norm_header(cell)
            if not norm:
                continue
            if any(alias == norm or alias in norm for alias in all_aliases):
                score += 1
        if score > best_score:
            best_score = score
            best_row_idx = idx

    headers = rows[best_row_idx]
    mapping: dict[str, int] = {}
    used_indices: set[int] = set()

    # Exact matches first, then partial matches
    for pass_exact in (True, False):
        for field, aliases in FIELD_ALIASES.items():
            if field in mapping:
                continue
            for col_idx, header in enumerate(headers):
                if col_idx in used_indices:
                    continue
                norm = _norm_header(header)
                if not norm:
                    continue
                matched = any(norm == a for a in aliases) if pass_exact else any(a in norm for a in aliases)
                if matched:
                    mapping[field] = col_idx
                    used_indices.add(col_idx)
                    break

    return best_row_idx, headers, mapping


def parse_import_preview(file_bytes: bytes, filename: str) -> dict[str, Any]:
    """Parse an uploaded Excel/CSV file and return headers, auto-mapping, and preview rows."""
    raw_rows = read_tabular_file(file_bytes, filename)
    if len(raw_rows) < 2:
        raise ValueError("Dosyada en az bir başlık satırı ve bir veri satırı olmalıdır.")

    header_idx, headers, mapping = detect_headers_and_mapping(raw_rows)
    data_rows = raw_rows[header_idx + 1 :]
    if not data_rows:
        raise ValueError("Başlık satırından sonra içe aktarılacak kayıt bulunamadı.")

    return {
        "filename": filename,
        "header_row_index": header_idx,
        "headers": headers,
        "mapping": mapping,
        "total_rows": len(data_rows),
        "rows": data_rows,
        "preview_rows": data_rows[:10],
    }


def rows_to_return_payloads(
    rows: list[list[str]],
    mapping: dict[str, int],
    default_marketplace: str = "Diğer",
    default_status: str = "beklemede",
) -> list[dict[str, Any]]:
    """Transform 2D spreadsheet rows into normalized return payloads using column mapping."""
    payloads: list[dict[str, Any]] = []

    def cell(row: list[str], field: str) -> str:
        idx = mapping.get(field)
        if idx is None or idx < 0 or idx >= len(row):
            return ""
        return str(row[idx]).strip()

    for row in rows:
        product_name = cell(row, "product_name")
        if not product_name:
            # Fallback if user didn't map product_name but mapped sku or order_no
            fallback = cell(row, "sku") or cell(row, "order_no")
            if not fallback:
                continue
            product_name = fallback

        raw_status = cell(row, "status")
        status = normalize_status_value(raw_status) if raw_status else default_status

        payloads.append(
            {
                "order_no": cell(row, "order_no"),
                "tracking_no": cell(row, "tracking_no"),
                "barcode": cell(row, "barcode"),
                "cargo_company": cell(row, "cargo_company"),
                "marketplace": cell(row, "marketplace") or default_marketplace,
                "customer_name": cell(row, "customer_name"),
                "customer_phone": cell(row, "customer_phone"),
                "product_name": product_name,
                "sku": cell(row, "sku"),
                "quantity": cell(row, "quantity") or 1,
                "refund_amount": cell(row, "refund_amount") or 0,
                "return_reason": cell(row, "return_reason"),
                "status": status,
                "return_date": cell(row, "return_date")[:10] if cell(row, "return_date") else "",
                "notes": cell(row, "notes"),
            }
        )

    return payloads


def build_template_workbook() -> bytes:
    """Create a sample Excel import template for users."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "İade Şablonu"

    headers = [
        "Sipariş No",
        "Kargo Takip No",
        "Barkod",
        "Kargo Firması",
        "Pazaryeri",
        "Müşteri Adı",
        "Telefon",
        "Ürün Adı",
        "Stok Kodu",
        "Adet",
        "İade Tutarı",
        "İade Nedeni",
        "Durum",
        "İade Tarihi",
        "Notlar",
    ]
    sample_row = [
        "TY-987654321",
        "728901234567",
        "8690000123456",
        "Trendyol Express",
        "Trendyol",
        "Ahmet Yılmaz",
        "0555 123 45 67",
        "Örnek Kablosuz Kulaklık Siyah",
        "KLK-SYH-01",
        1,
        1250.00,
        "Müşteri Vazgeçti / Beğenmedi",
        "Beklemede",
        "2026-11-24",
        "Kutu açılmamış",
    ]

    header_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    thin = Side(style="thin", color="CBD5E1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    ws.append(sample_row)
    for col_idx in range(1, len(sample_row) + 1):
        cell = ws.cell(row=2, column=col_idx)
        cell.border = border
        cell.alignment = Alignment(vertical="center")

    widths = [18, 20, 18, 18, 14, 20, 16, 34, 16, 8, 14, 26, 16, 14, 26]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 28
    ws.row_dimensions[2].height = 22

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def build_export_workbook(records: list[dict[str, Any]], stats: dict[str, Any]) -> bytes:
    """Export filtered return records and dashboard summary into a styled Excel workbook."""
    wb = openpyxl.Workbook()

    # Sheet 1: Detailed Return Records
    ws = wb.active
    ws.title = "İade Kayıtları"

    columns = [
        ("İade Kodu", 18),
        ("Durum", 18),
        ("Pazaryeri", 15),
        ("Sipariş No", 18),
        ("Kargo Takip No", 20),
        ("Kargo Firması", 16),
        ("Müşteri Adı", 20),
        ("Telefon", 16),
        ("Ürün Adı", 36),
        ("Stok Kodu", 16),
        ("Barkod", 18),
        ("Adet", 8),
        ("İade Tutarı (₺)", 15),
        ("İade Nedeni", 26),
        ("Kondisyon", 20),
        ("İade Tarihi", 13),
        ("Notlar", 30),
    ]

    header_fill = PatternFill(start_color="0F172A", end_color="0F172A", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    thin = Side(style="thin", color="CBD5E1")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.append([c[0] for c in columns])
    ws.row_dimensions[1].height = 28
    for idx, (_, width) in enumerate(columns, start=1):
        c = ws.cell(row=1, column=idx)
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = border
        ws.column_dimensions[get_column_letter(idx)].width = width

    total_qty = 0
    total_amt = 0.0

    for r_idx, item in enumerate(records, start=2):
        status_meta = STATUS_MAP.get(item.get("status", ""), {"label": item.get("status", ""), "bg": "#f1f5f9", "color": "#334155"})
        qty = int(item.get("quantity") or 1)
        amt = float(item.get("refund_amount") or 0)
        total_qty += qty
        total_amt += amt

        row_values = [
            item.get("return_code", ""),
            status_meta["label"],
            item.get("marketplace", ""),
            item.get("order_no", ""),
            item.get("tracking_no", ""),
            item.get("cargo_company", ""),
            item.get("customer_name", ""),
            item.get("customer_phone", ""),
            item.get("product_name", ""),
            item.get("sku", ""),
            item.get("barcode", ""),
            qty,
            amt,
            item.get("return_reason", ""),
            CONDITION_LABELS.get(item.get("condition_grade", ""), item.get("condition_grade", "")),
            item.get("return_date", ""),
            item.get("notes", ""),
        ]
        ws.append(row_values)
        ws.row_dimensions[r_idx].height = 22

        for c_idx in range(1, len(columns) + 1):
            cell = ws.cell(row=r_idx, column=c_idx)
            cell.border = border
            cell.font = Font(name="Calibri", size=10, color="0F172A")
            if c_idx == 2:
                bg_hex = status_meta["bg"].lstrip("#")
                fg_hex = status_meta["color"].lstrip("#")
                cell.fill = PatternFill(start_color=bg_hex, end_color=bg_hex, fill_type="solid")
                cell.font = Font(name="Calibri", size=10, bold=True, color=fg_hex)
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif c_idx in (1, 12, 16):
                cell.alignment = Alignment(horizontal="center", vertical="center")
                if r_idx % 2 == 1:
                    cell.fill = zebra_fill
            elif c_idx == 13:
                cell.number_format = "#,##0.00 ₺"
                cell.alignment = Alignment(horizontal="right", vertical="center")
                if r_idx % 2 == 1:
                    cell.fill = zebra_fill
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
                if r_idx % 2 == 1:
                    cell.fill = zebra_fill

    # Totals row
    total_row = len(records) + 2
    ws.cell(row=total_row, column=1, value="TOPLAM").font = Font(bold=True, size=10)
    ws.cell(row=total_row, column=9, value=f"{len(records)} İade Kaydı").font = Font(bold=True, size=10)
    qty_cell = ws.cell(row=total_row, column=12, value=total_qty)
    qty_cell.font = Font(bold=True, size=10)
    qty_cell.alignment = Alignment(horizontal="center", vertical="center")
    amt_cell = ws.cell(row=total_row, column=13, value=round(total_amt, 2))
    amt_cell.font = Font(bold=True, size=10)
    amt_cell.number_format = "#,##0.00 ₺"
    amt_cell.alignment = Alignment(horizontal="right", vertical="center")
    for c_idx in range(1, len(columns) + 1):
        c = ws.cell(row=total_row, column=c_idx)
        c.fill = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
        c.border = border

    ws.freeze_panes = "A2"

    # Sheet 2: Summary Dashboard
    ws_sum = wb.create_sheet(title="Özet Rapor")
    summary = stats.get("summary", {})
    ws_sum.append(["İade Takip Sistemi - Özet Rapor", ""])
    ws_sum["A1"].font = Font(name="Calibri", size=14, bold=True, color="0F172A")
    ws_sum.append(["Toplam İade Kaydı", summary.get("total_count", 0)])
    ws_sum.append(["Toplam Ürün Adedi", summary.get("total_units", 0)])
    ws_sum.append(["Bekleyen / Kargoda", summary.get("pending_count", 0)])
    ws_sum.append(["Teslim Alınan / İncelenen", summary.get("review_count", 0)])
    ws_sum.append(["Onaylanan / Tamamlanan", summary.get("approved_count", 0)])
    ws_sum.append(["Reddedilen", summary.get("rejected_count", 0)])
    ws_sum.append(["Toplam İade Tutarı (₺)", float(summary.get("total_amount", 0) or 0)])
    ws_sum["B8"].number_format = "#,##0.00 ₺"
    ws_sum.column_dimensions["A"].width = 30
    ws_sum.column_dimensions["B"].width = 22

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()

