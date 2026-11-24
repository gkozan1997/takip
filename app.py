"""Flask web server and REST API for İade Takip."""

from __future__ import annotations

import io
import mimetypes
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.utils import secure_filename

from config import (
    ALLOWED_IMAGE_EXTENSIONS,
    ALLOWED_SPREADSHEET_EXTENSIONS,
    CARGO_OPTIONS,
    CONDITION_OPTIONS,
    MARKETPLACE_OPTIONS,
    MAX_UPLOAD_SIZE_MB,
    REASON_OPTIONS,
    STATUS_OPTIONS,
    TZ_ISTANBUL,
    UPLOAD_DIR,
)
from database import ReturnDatabase
from excel_service import (
    build_export_workbook,
    build_template_workbook,
    parse_import_preview,
    rows_to_return_payloads,
)

# Short-lived in-memory cache for multi-step Excel import previews
_IMPORT_CACHE: dict[str, dict[str, Any]] = {}
_IMPORT_CACHE_TTL = 1800  # 30 minutes


def _prune_import_cache() -> None:
    now = time.time()
    expired = [k for k, v in _IMPORT_CACHE.items() if now - v["ts"] > _IMPORT_CACHE_TTL]
    for k in expired:
        _IMPORT_CACHE.pop(k, None)


def create_app(
    db_path: Path | str | None = None,
    upload_dir: Path | str | None = None,
) -> Flask:
    """Create and configure the İade Takip Flask application."""
    flask_app = Flask(__name__, template_folder="templates", static_folder="static")
    flask_app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE_MB * 1024 * 1024

    db = ReturnDatabase(db_path=db_path)
    evidence_dir = Path(upload_dir) if upload_dir else UPLOAD_DIR
    evidence_dir.mkdir(parents=True, exist_ok=True)

    @flask_app.get("/")
    def index() -> str:
        return render_template(
            "index.html",
            status_options=STATUS_OPTIONS,
            condition_options=CONDITION_OPTIONS,
            marketplace_options=MARKETPLACE_OPTIONS,
            cargo_options=CARGO_OPTIONS,
            reason_options=REASON_OPTIONS,
        )

    @flask_app.get("/api/health")
    def health() -> Any:
        return jsonify({"status": "ok", "service": "iade-takip"})

    @flask_app.get("/api/config")
    def get_config() -> Any:
        return jsonify(
            {
                "statuses": STATUS_OPTIONS,
                "conditions": CONDITION_OPTIONS,
                "marketplaces": MARKETPLACE_OPTIONS,
                "cargos": CARGO_OPTIONS,
                "reasons": REASON_OPTIONS,
            }
        )

    @flask_app.get("/api/stats")
    def get_stats() -> Any:
        return jsonify(db.get_dashboard_stats())

    @flask_app.get("/api/returns")
    def list_returns() -> Any:
        items = db.list_returns(
            search=request.args.get("search", ""),
            status=request.args.get("status", ""),
            marketplace=request.args.get("marketplace", ""),
            reason=request.args.get("reason", ""),
            condition=request.args.get("condition", ""),
            date_from=request.args.get("date_from", ""),
            date_to=request.args.get("date_to", ""),
            has_evidence=request.args.get("has_evidence", ""),
        )
        return jsonify({"items": items, "count": len(items)})

    @flask_app.post("/api/returns")
    def create_return() -> Any:
        payload = request.get_json(silent=True) or {}
        try:
            created = db.create_return(
                payload,
                history_note=str(payload.get("history_note") or "Manuel kayıt oluşturuldu"),
            )
            return jsonify({"item": created, "message": "İade kaydı oluşturuldu."}), 201
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @flask_app.get("/api/returns/<int:return_id>")
    def get_return(return_id: int) -> Any:
        item = db.get_return(return_id)
        if not item:
            return jsonify({"error": "İade kaydı bulunamadı."}), 404
        return jsonify({"item": item})

    @flask_app.put("/api/returns/<int:return_id>")
    def update_return(return_id: int) -> Any:
        payload = request.get_json(silent=True) or {}
        history_note = str(payload.pop("history_note", "") or "")
        try:
            updated = db.update_return(return_id, payload, history_note=history_note)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not updated:
            return jsonify({"error": "İade kaydı bulunamadı."}), 404
        return jsonify({"item": updated, "message": "İade kaydı güncellendi."})

    @flask_app.delete("/api/returns/<int:return_id>")
    def delete_return(return_id: int) -> Any:
        stored_files = db.delete_return(return_id)
        for stored_name in stored_files:
            file_path = (evidence_dir / stored_name).resolve()
            if file_path.parent == evidence_dir.resolve() and file_path.exists():
                file_path.unlink(missing_ok=True)
        return jsonify({"message": "İade kaydı silindi."})

    @flask_app.post("/api/returns/bulk-status")
    def bulk_status() -> Any:
        payload = request.get_json(silent=True) or {}
        ids = payload.get("ids") or []
        new_status = str(payload.get("status") or "").strip().lower()
        note = str(payload.get("note") or "").strip()
        try:
            clean_ids = [int(x) for x in ids]
        except (TypeError, ValueError):
            return jsonify({"error": "Geçersiz kayıt ID listesi."}), 400

        updated = db.bulk_update_status(clean_ids, new_status, note=note)
        if updated == 0:
            return jsonify({"error": "Güncellenecek geçerli kayıt veya durum bulunamadı."}), 400
        return jsonify({"updated": updated, "message": f"{updated} kayıt güncellendi."})

    @flask_app.get("/api/lookup")
    def lookup_scan() -> Any:
        code = request.args.get("code", "").strip()
        if not code:
            return jsonify({"items": [], "count": 0})
        items = db.lookup_by_scan(code)
        return jsonify({"items": items, "count": len(items), "code": code})

    @flask_app.post("/api/returns/<int:return_id>/evidence")
    def upload_evidence(return_id: int) -> Any:
        item = db.get_return(return_id)
        if not item:
            return jsonify({"error": "İade kaydı bulunamadı."}), 404

        files = request.files.getlist("files")
        if not files:
            single = request.files.get("file")
            if single:
                files = [single]

        if not files:
            return jsonify({"error": "Yüklenecek dosya seçilmedi."}), 400

        caption = request.form.get("caption", "")
        saved: list[dict[str, Any]] = []

        for f in files:
            if not f or not f.filename:
                continue
            orig_name = Path(f.filename).name
            ext = Path(orig_name).suffix.lower()
            if ext not in ALLOWED_IMAGE_EXTENSIONS:
                return jsonify(
                    {
                        "error": f"Desteklenmeyen dosya türü ({ext}). İzin verilenler: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}"
                    }
                ), 400

            safe_orig = secure_filename(orig_name) or f"kanit{ext}"
            stored_name = f"{return_id}_{secrets.token_hex(8)}{ext}"
            dest = evidence_dir / stored_name
            f.save(dest)
            file_size = dest.stat().st_size
            mime_type = f.mimetype or mimetypes.guess_type(orig_name)[0] or "application/octet-stream"

            ev = db.add_evidence(
                return_id=return_id,
                file_name=orig_name or safe_orig,
                stored_name=stored_name,
                mime_type=mime_type,
                file_size=file_size,
                caption=caption,
            )
            saved.append(ev)

        if not saved:
            return jsonify({"error": "Geçerli dosya yüklenemedi."}), 400

        updated_item = db.get_return(return_id)
        return jsonify({"saved": saved, "item": updated_item, "message": f"{len(saved)} kanıt yüklendi."}), 201

    @flask_app.get("/api/evidence/<int:evidence_id>/file")
    def serve_evidence(evidence_id: int) -> Any:
        ev = db.get_evidence(evidence_id)
        if not ev:
            return jsonify({"error": "Kanıt bulunamadı."}), 404

        file_path = (evidence_dir / ev["stored_name"]).resolve()
        if file_path.parent != evidence_dir.resolve() or not file_path.exists():
            return jsonify({"error": "Dosya diskte bulunamadı."}), 404

        return send_file(
            file_path,
            mimetype=ev.get("mime_type") or "application/octet-stream",
            as_attachment=False,
            download_name=ev["file_name"],
        )

    @flask_app.delete("/api/evidence/<int:evidence_id>")
    def delete_evidence(evidence_id: int) -> Any:
        ev = db.delete_evidence(evidence_id)
        if not ev:
            return jsonify({"error": "Kanıt bulunamadı."}), 404

        file_path = (evidence_dir / ev["stored_name"]).resolve()
        if file_path.parent == evidence_dir.resolve() and file_path.exists():
            file_path.unlink(missing_ok=True)
        return jsonify({"message": "Kanıt silindi."})

    @flask_app.post("/api/import/preview")
    def import_preview() -> Any:
        _prune_import_cache()
        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "Lütfen bir Excel (.xlsx, .xls) veya CSV dosyası seçin."}), 400

        ext = Path(file.filename).suffix.lower()
        if ext not in ALLOWED_SPREADSHEET_EXTENSIONS:
            return jsonify({"error": f"Desteklenmeyen dosya formatı: {ext}"}), 400

        try:
            parsed = parse_import_preview(file.read(), file.filename)
        except Exception as exc:
            return jsonify({"error": f"Dosya okunamadı: {exc}"}), 400

        token = secrets.token_urlsafe(12)
        _IMPORT_CACHE[token] = {
            "ts": time.time(),
            "filename": parsed["filename"],
            "rows": parsed["rows"],
        }

        return jsonify(
            {
                "import_token": token,
                "filename": parsed["filename"],
                "headers": parsed["headers"],
                "mapping": parsed["mapping"],
                "total_rows": parsed["total_rows"],
                "preview_rows": parsed["preview_rows"],
            }
        )

    @flask_app.post("/api/import/commit")
    def import_commit() -> Any:
        _prune_import_cache()
        payload = request.get_json(silent=True) or {}
        token = str(payload.get("import_token") or "")
        cached = _IMPORT_CACHE.get(token)
        if not cached:
            return jsonify({"error": "Önizleme oturumu zaman aşımına uğradı. Lütfen dosyayı tekrar yükleyin."}), 400

        raw_mapping = payload.get("mapping") or {}
        mapping: dict[str, int] = {}
        for field, col_idx in raw_mapping.items():
            if col_idx is not None and col_idx != "":
                try:
                    idx_int = int(col_idx)
                    if idx_int >= 0:
                        mapping[field] = idx_int
                except (TypeError, ValueError):
                    continue

        duplicate_mode = str(payload.get("duplicate_mode") or "skip")  # skip | update | create
        default_marketplace = str(payload.get("default_marketplace") or "Diğer")
        default_status = str(payload.get("default_status") or "beklemede")

        records = rows_to_return_payloads(
            cached["rows"],
            mapping=mapping,
            default_marketplace=default_marketplace,
            default_status=default_status,
        )
        if not records:
            return jsonify({"error": "Aktarılacak geçerli ürün satırı bulunamadı. Lütfen 'Ürün Adı' sütununu eşleştirin."}), 400

        created_count = 0
        updated_count = 0
        skipped_count = 0

        for rec in records:
            existing = None
            if duplicate_mode in {"skip", "update"}:
                existing = db.find_existing_for_import(rec.get("order_no", ""), rec.get("tracking_no", ""))

            if existing:
                if duplicate_mode == "skip":
                    skipped_count += 1
                    continue
                if duplicate_mode == "update":
                    db.update_return(
                        existing["id"],
                        rec,
                        history_note=f"Excel aktarımı ile güncellendi ({cached['filename']})",
                    )
                    updated_count += 1
                    continue

            db.create_return(
                rec,
                history_note=f"Excel ile içe aktarıldı ({cached['filename']})",
            )
            created_count += 1

        _IMPORT_CACHE.pop(token, None)
        return jsonify(
            {
                "created": created_count,
                "updated": updated_count,
                "skipped": skipped_count,
                "message": f"Aktarım tamamlandı: {created_count} yeni, {updated_count} güncellendi, {skipped_count} atlandı.",
            }
        )

    @flask_app.get("/api/export/template")
    def export_template() -> Any:
        data = build_template_workbook()
        return send_file(
            io.BytesIO(data),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name="Iade_Takip_Sablonu.xlsx",
        )

    @flask_app.get("/api/export/excel")
    def export_excel() -> Any:
        items = db.list_returns(
            search=request.args.get("search", ""),
            status=request.args.get("status", ""),
            marketplace=request.args.get("marketplace", ""),
            reason=request.args.get("reason", ""),
            condition=request.args.get("condition", ""),
            date_from=request.args.get("date_from", ""),
            date_to=request.args.get("date_to", ""),
            has_evidence=request.args.get("has_evidence", ""),
        )
        stats = db.get_dashboard_stats()
        data = build_export_workbook(items, stats)
        stamp = datetime.now(TZ_ISTANBUL).strftime("%Y%m%d_%H%M")
        return send_file(
            io.BytesIO(data),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"Iade_Takip_Raporu_{stamp}.xlsx",
        )

    return flask_app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    app.run(host=host, port=port, debug=False)

