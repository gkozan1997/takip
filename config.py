"""Application configuration and domain constants for İade Takip."""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
IS_SERVERLESS = bool(os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))

_DEFAULT_DATA_DIR = Path("/tmp/iade_takip_data") if IS_SERVERLESS else (BASE_DIR / "data")
_DEFAULT_UPLOAD_DIR = Path("/tmp/iade_takip_uploads") if IS_SERVERLESS else (BASE_DIR / "uploads" / "evidence")

DATA_DIR = Path(os.environ["DATA_DIR"]) if os.environ.get("DATA_DIR") else _DEFAULT_DATA_DIR
UPLOAD_DIR = Path(os.environ["UPLOAD_DIR"]) if os.environ.get("UPLOAD_DIR") else _DEFAULT_UPLOAD_DIR
DEFAULT_DB_PATH = DATA_DIR / "iade_takip.db"

TZ_ISTANBUL = timezone(timedelta(hours=3))

MAX_UPLOAD_SIZE_MB = 15
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".pdf"}
ALLOWED_SPREADSHEET_EXTENSIONS = {".xlsx", ".xls", ".csv"}

STATUS_OPTIONS = [
    {"key": "beklemede", "label": "Beklemede", "color": "#64748b", "bg": "#f1f5f9"},
    {"key": "kargoda", "label": "Kargoda / Yolda", "color": "#0284c7", "bg": "#e0f2fe"},
    {"key": "teslim_alindi", "label": "Teslim Alındı", "color": "#7c3aed", "bg": "#ede9fe"},
    {"key": "inceleniyor", "label": "İnceleniyor", "color": "#d97706", "bg": "#fef3c7"},
    {"key": "servise_teslim", "label": "Servise Teslim Edildi", "color": "#be185d", "bg": "#fce7f3"},
    {"key": "onaylandi", "label": "Onaylandı", "color": "#16a34a", "bg": "#dcfce7"},
    {"key": "reddedildi", "label": "Reddedildi", "color": "#dc2626", "bg": "#fee2e2"},
    {"key": "iade_tamamlandi", "label": "Ödeme / İade Tamam", "color": "#0d9488", "bg": "#ccfbf1"},
]

STATUS_MAP = {item["key"]: item for item in STATUS_OPTIONS}
VALID_STATUSES = set(STATUS_MAP.keys())

CONDITION_OPTIONS = [
    {"key": "belirtilmedi", "label": "Belirtilmedi"},
    {"key": "satilabilir", "label": "Yeniden Satılabilir"},
    {"key": "ambalaj_acik", "label": "Ambalajı Açık / Hasarlı"},
    {"key": "hasarli", "label": "Ürün Hasarlı / Kusurlu"},
    {"key": "eksik_parca", "label": "Eksik Parça / Aksesuar"},
    {"key": "hurda", "label": "Kullanılamaz / Hurda"},
]
VALID_CONDITIONS = {item["key"] for item in CONDITION_OPTIONS}

MARKETPLACE_OPTIONS = [
    "Jebinde.com",
    "N11 Ahidenal",
    "N11 Bingöl Ticaret",
    "Hepsi Burada Ahidenal",
    "Hepsi Burada Bingöl Ticaret",
    "Trendyol Ahidenal",
    "Trendyol Bingöl Ticaret Beko",
    "Trendyol Dipbi Türkiye",
    "Trendyol Kiruzan Türkiye",
    "Pazarama",
    "İdefix",
    "Koçtaş Ahidenal",
    "Koçtaş Bingöl Ticaret",
]

CARGO_OPTIONS = [
    "DHL",
    "Yurtiçi Kargo",
    "Hepsijet",
    "Hepsijet XL",
    "Aras Kargo",
    "Ceva Lojistik",
    "Diğer",
]

REASON_OPTIONS = [
    "Müşteri Vazgeçti / Beğenmedi",
    "Hasarlı / Kırık Ürün",
    "Kargo Kaynaklı Hasar",
    "Arızalı / Çalışmıyor",
    "Yanlış Ürün Gönderildi",
    "Eksik Parça / Aksesuar",
    "Model / Beden Uyumsuz",
    "Açıklamayla Uyuşmuyor",
    "Diğer",
]

