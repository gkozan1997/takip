"""Automated tests for İade Takip application."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

import openpyxl

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import create_app


class IadeTakipTestCase(unittest.TestCase):
    """End-to-end API tests using isolated temporary database and upload folder."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        tmp_path = Path(self.tmpdir.name)
        self.app = create_app(
            db_path=tmp_path / "test_iade.db",
            upload_dir=tmp_path / "uploads",
        )
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_health_and_index(self) -> None:
        res_health = self.client.get("/api/health")
        self.assertEqual(res_health.status_code, 200)
        self.assertEqual(res_health.get_json()["status"], "ok")

        res_index = self.client.get("/")
        self.assertEqual(res_index.status_code, 200)
        self.assertIn("İade Takip", res_index.get_data(as_text=True))

    def test_crud_lookup_and_status_history(self) -> None:
        # Create
        create_res = self.client.post(
            "/api/returns",
            json={
                "marketplace": "Trendyol",
                "order_no": "TY-100200",
                "tracking_no": "7788990011",
                "barcode": "8691234567890",
                "customer_name": "Ayşe Demir",
                "product_name": "Beko Türk Kahve Makinesi",
                "sku": "BK-TKM-01",
                "quantity": 2,
                "refund_amount": "2499,50",
                "return_reason": "Arızalı / Çalışmıyor",
                "status": "beklemede",
            },
        )
        self.assertEqual(create_res.status_code, 201)
        item = create_res.get_json()["item"]
        rid = item["id"]
        self.assertEqual(item["refund_amount"], 2499.5)
        self.assertEqual(len(item["history"]), 1)

        # Lookup by tracking_no and barcode
        lookup_res = self.client.get("/api/lookup?code=7788990011")
        self.assertEqual(lookup_res.status_code, 200)
        self.assertEqual(lookup_res.get_json()["count"], 1)

        # Update status
        upd_res = self.client.put(
            f"/api/returns/{rid}",
            json={"status": "inceleniyor", "history_note": "Kutu açıldı"},
        )
        self.assertEqual(upd_res.status_code, 200)
        updated = upd_res.get_json()["item"]
        self.assertEqual(updated["status"], "inceleniyor")
        self.assertEqual(len(updated["history"]), 2)

        # Filter by marketplace and status
        list_res = self.client.get("/api/returns?marketplace=Trendyol&status=inceleniyor")
        self.assertEqual(list_res.status_code, 200)
        self.assertEqual(list_res.get_json()["count"], 1)

        # Bulk status update
        bulk_res = self.client.post(
            "/api/returns/bulk-status",
            json={"ids": [rid], "status": "onaylandi"},
        )
        self.assertEqual(bulk_res.status_code, 200)
        self.assertEqual(bulk_res.get_json()["updated"], 1)

        # Stats
        stats_res = self.client.get("/api/stats")
        self.assertEqual(stats_res.status_code, 200)
        self.assertEqual(stats_res.get_json()["summary"]["approved_count"], 1)

        # Delete
        del_res = self.client.delete(f"/api/returns/{rid}")
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(self.client.get(f"/api/returns/{rid}").status_code, 404)

    def test_evidence_upload_serve_and_delete(self) -> None:
        rid = self.client.post(
            "/api/returns",
            json={"product_name": "Kırık Blender", "marketplace": "Hepsiburada"},
        ).get_json()["item"]["id"]

        fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        up_res = self.client.post(
            f"/api/returns/{rid}/evidence",
            data={
                "caption": "Kırık kapak fotoğrafı",
                "files": (io.BytesIO(fake_png), "hasar.png"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(up_res.status_code, 201)
        saved = up_res.get_json()["saved"]
        self.assertEqual(len(saved), 1)
        ev_id = saved[0]["id"]

        # Filter has_evidence=yes
        filtered = self.client.get("/api/returns?has_evidence=yes").get_json()
        self.assertEqual(filtered["count"], 1)

        # Serve file
        file_res = self.client.get(f"/api/evidence/{ev_id}/file")
        self.assertEqual(file_res.status_code, 200)
        self.assertEqual(file_res.data, fake_png)
        file_res.close()

        # Delete evidence
        del_ev = self.client.delete(f"/api/evidence/{ev_id}")
        self.assertEqual(del_ev.status_code, 200)
        self.assertEqual(self.client.get(f"/api/evidence/{ev_id}/file").status_code, 404)

    def test_excel_import_and_export(self) -> None:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Sipariş No", "Kargo Takip No", "Pazaryeri", "Müşteri Adı", "Ürün Adı", "Adet", "İade Tutarı", "Durum"])
        ws.append(["HB-5001", "TRK-5001", "Hepsiburada", "Mehmet Kaya", "Kablosuz Süpürge", 1, 4500.0, "Kargoda"])
        ws.append(["TY-5002", "TRK-5002", "Trendyol", "Elif Yılmaz", "Saç Kurutma Makinesi", 2, 1800.0, "Onaylandı"])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        prev_res = self.client.post(
            "/api/import/preview",
            data={"file": (buf, "iadeler.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(prev_res.status_code, 200)
        prev_json = prev_res.get_json()
        self.assertEqual(prev_json["total_rows"], 2)
        self.assertIn("product_name", prev_json["mapping"])

        commit_res = self.client.post(
            "/api/import/commit",
            json={
                "import_token": prev_json["import_token"],
                "mapping": prev_json["mapping"],
                "duplicate_mode": "skip",
            },
        )
        self.assertEqual(commit_res.status_code, 200)
        self.assertEqual(commit_res.get_json()["created"], 2)

        # Template and export endpoints
        tpl_res = self.client.get("/api/export/template")
        self.assertEqual(tpl_res.status_code, 200)
        tpl_res.close()

        exp_res = self.client.get("/api/export/excel")
        self.assertEqual(exp_res.status_code, 200)
        exp_wb = openpyxl.load_workbook(io.BytesIO(exp_res.data))
        self.assertIn("İade Kayıtları", exp_wb.sheetnames)
        self.assertIn("Özet Rapor", exp_wb.sheetnames)
        exp_wb.close()


if __name__ == "__main__":
    unittest.main()

