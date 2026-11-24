# İade Takip Sistemi

Geçmiş projelerden tamamen bağımsız olarak tasarlanmış, pazaryeri ve depo süreçlerine uygun modern **İade Takip** web uygulaması.

## Özellikler

- **İade Kayıt Yönetimi:** Pazaryeri, sipariş no, kargo takip no, barkod, müşteri, stok kodu, tutar, iade nedeni ve ürün kondisyonu takibi.
- **Barkod & Kargo Kontrol Ekranı:** El terminali, klavye veya desteklenen cihazlarda kamera ile hızlı kod sorgulama; tek tıkla "Teslim Alındı" işaretleme veya yeni kayıt açma.
- **Durum Takibi & İşlem Geçmişi:** Beklemede, Kargoda, Teslim Alındı, İnceleniyor, Onaylandı, Reddedildi ve Ödeme Tamam aşamaları ile otomatik zaman damgalı geçmiş günlüğü.
- **Fotoğraf & Kanıt Yükleme:** Her iade kaydına çoklu fotoğraf/PDF kanıtı ekleme, açıklama yazma ve büyütüp inceleme.
- **Excel / CSV İçe & Dışa Aktarım:** Otomatik sütun eşleştirme sihirbazı, mükerrer kayıt kontrolü, örnek şablon indirme ve özet raporlu Excel çıktısı.
- **Mobil Uyumlu Arayüz:** Masaüstü tablo görünümü ve telefonlar için özel alt navigasyon ile kart görünümü.

## Çalıştırma

### Windows (Tek Tık)
`baslat.bat` dosyasına çift tıklayın. Sunucu `http://127.0.0.1:5050` adresinde açılır.

### Terminal
```bash
pip install -r requirements.txt
python app.py
```

### Testleri Çalıştırma
```bash
python -m unittest discover -s tests -v
```

## İnternete Yayınlama (GitHub + Render)

1. GitHub'da yeni bir repository oluşturun (ör. `iade-takip`) ve projeyi push edin:
   ```bash
   git remote add origin https://github.com/<kullanici-adiniz>/iade-takip.git
   git push -u origin main
   ```
2. [Render.com](https://render.com) hesabınıza giriş yapıp **New + -> Blueprint** (veya **Web Service**) seçin ve GitHub reponuzu bağlayın.
3. `render.yaml` üzerinden build/start ayarları otomatik algılanır (`gunicorn app:app`).
4. Kalıcı veri saklama için Render'da kalıcı disk (Disk) ekleyip ortam değişkeni olarak `DATA_DIR=/var/data` ve `UPLOAD_DIR=/var/data/evidence` tanımlayabilirsiniz.

