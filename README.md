# Eclipta Durum

Türkiye ve global dijital altyapı servisleri için Flask tabanlı, tek dosyalık durum izleme uygulaması.

## Kurulum

Python 3.10 veya üzerini kullanın:

```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\\Scripts\\activate
pip install -r requirements.txt
python app.py
```

Tarayıcıda `http://127.0.0.1:5000` adresini açın. Arka plan izleme, uygulama doğrudan `python app.py` ile başlatıldığında etkinleşir. İlk kontrol turu tamamlandıkça grafikler veriyle dolar.

Hızlı bir deneme için, uygulamayı `CHECK_INTERVAL=10 python app.py` ile başlatabilir veya `app.py` içindeki belirtilen satırı geçici olarak `time.sleep(10)` yapabilirsiniz.

## Özellikler

- 15 dakikada bir daemon thread ile HTTP/HTTPS kontrolü
- HTTP durum kodu ve sayfada beklenen anahtar sözcükle UP, DEGRADED veya DOWN sınıflandırması
- Python standart kütüphanesiyle SSL sertifikası bitiş takibi
- SQLite üzerinde 24 saatlik durum geçmişi, olaylar ve API sorun bildirimleri
- Responsive SVG çizgi grafikleri, uptime ve gecikme bilgisi
- Sayfa yenilemeden her 60 saniyede bir güncellenen açık temalı pano

## API

- `GET /`: durum panosu
- `GET /api/status`: kartlar ve 24 saatlik geçmiş için JSON
- `POST /api/report`: JSON gövdesiyle sorun bildirimi. Örnek: `{"service":"GitHub","issue_type":"Erişim sorunu"}`

`service`, uygulamadaki servis adlarından biri olmalı; `issue_type` 1–120 karakterlik metindir. Bu endpoint için arayüz formu bulunmaz.

## Veri ve güvenlik notları

Veritabanı ve günlükler uygulama dizininde sırasıyla `eclipta_status.db` ve `eclipta.log` olarak oluşur. Ağdan alınan değerler SQL sorgularına parametre olarak aktarılır; API bildirimi ise bilinen servis adlarıyla sınırlandırılmıştır. Gerçek ortamda uygulamayı ters vekil, erişim denetimi ve TLS arkasında çalıştırın.

## `durum.eclipta.tr` için sunucu hazırlığı

Depodaki `deploy/Caddyfile`, alan adı için HTTPS sertifikasını Caddy üzerinden otomatik alacak ters vekil ayarıdır. `deploy/eclipta-durum.service` ise uygulamayı yeniden başlatma ve temel sistem izolasyonu ile systemd altında çalıştırır.

1. DNS sağlayıcısında `durum.eclipta.tr` için sunucunun genel IP adresine bir `A` kaydı (IPv6 kullanılıyorsa `AAAA` kaydı) ekleyin.
2. Linux sunucuda projeyi `/opt/eclipta-durum` altına koyun; bir `eclipta` sistem kullanıcısı oluşturun, bu kullanıcıya klasör sahipliği verin ve sanal ortamda `pip install -r requirements.txt` çalıştırın.
3. Caddy'ye `deploy/Caddyfile` içeriğini tanıtın ve Caddy hizmetini yeniden yükleyin.
4. `deploy/eclipta-durum.service` dosyasındaki yolları kontrol edip `/etc/systemd/system/` altına kopyalayın; ardından `systemctl daemon-reload` ve `systemctl enable --now eclipta-durum` çalıştırın.

Caddy'nin 80 ve 443 portlarına erişebilmesi, DNS kaydının yayılmış olması ve güvenlik duvarının bu portlara izin vermesi gerekir. Bu proje yalnızca Flask ve requests bağımlılığı şartını koruduğu için ek bir WSGI sunucusu eklenmemiştir; internet erişimine açmadan önce işletim ekibinizle seçtiğiniz WSGI katmanını ve erişim politikasını doğrulayın.

## Lisans ve bağımlılıklar

Proje MIT Lisansı ile lisanslanmıştır. Tek üçüncü taraf bağımlılıkları Flask (BSD-3-Clause) ve requests (Apache-2.0) paketleridir; SSL, socket ve SQLite Python standart kütüphanesindendir.
