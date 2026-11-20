"""Eclipta Durum: Flask ve SQLite ile tek dosyalık servis durum izleyicisi.

Copyright (c) 2026 Eclipta. Bu yazılım MIT Lisansı ile sunulur; ayrıntılar
LICENSE dosyasındadır. Yalnızca Flask (BSD-3), requests (Apache-2.0) ve
Python standart kütüphanesini kullanır.
"""

from __future__ import annotations

import logging
import os
import socket
import sqlite3
import ssl
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify, render_template_string, request

APP_DIR = Path(__file__).resolve().parent
DATABASE = APP_DIR / "eclipta_status.db"
LOG_FILE = APP_DIR / "eclipta.log"
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "900"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "12"))
MAX_HISTORY_POINTS = 96

SERVICES = [
    ("Telekom", "Turkcell", "https://www.turkcell.com.tr", ("turkcell",)),
    ("Telekom", "Türk Telekom", "https://www.turktelekom.com.tr", ("telekom",)),
    ("Telekom", "Vodafone TR", "https://www.vodafone.com.tr", ("vodafone",)),
    ("Sosyal Medya", "Instagram", "https://www.instagram.com", ("instagram",)),
    ("Sosyal Medya", "X Twitter", "https://x.com", ("twitter", "x.com")),
    ("Sosyal Medya", "Discord", "https://discord.com", ("discord",)),
    ("Sosyal Medya", "Reddit", "https://www.reddit.com", ("reddit",)),
    ("Sosyal Medya", "WhatsApp", "https://www.whatsapp.com", ("whatsapp",)),
    ("Microsoft", "Microsoft", "https://www.microsoft.com", ("microsoft",)),
    ("Microsoft", "Office 365", "https://www.office.com", ("office", "microsoft")),
    ("Microsoft", "OneDrive", "https://onedrive.live.com", ("onedrive",)),
    ("Microsoft", "Teams", "https://teams.microsoft.com", ("teams", "microsoft")),
    ("Microsoft", "Copilot", "https://copilot.microsoft.com", ("copilot",)),
    ("Microsoft", "Xbox", "https://www.xbox.com", ("xbox",)),
    ("Microsoft", "Outlook", "https://outlook.live.com", ("outlook", "microsoft")),
    ("AI", "OpenAI", "https://chat.openai.com", ("openai", "chatgpt")),
    ("AI", "Claude", "https://claude.ai", ("claude",)),
    ("AI", "Gemini", "https://gemini.google.com", ("gemini", "google")),
    ("AI", "Grok", "https://grok.x.ai", ("grok",)),
    ("İletişim", "Slack", "https://slack.com", ("slack",)),
    ("Geliştirici", "GitHub", "https://github.com", ("github",)),
    ("Oyun", "Steam", "https://store.steampowered.com", ("steam",)),
    ("Oyun", "Rockstar Games", "https://www.rockstargames.com", ("rockstar",)),
    ("Oyun", "PlayStation", "https://www.playstation.com", ("playstation",)),
    ("Bulut", "AWS", "https://aws.amazon.com", ("aws", "amazon")),
    ("Bulut", "Google Cloud", "https://cloud.google.com", ("google",)),
    ("Bulut", "Microsoft Azure", "https://azure.microsoft.com", ("azure", "microsoft")),
    ("Bulut", "Huawei Cloud", "https://www.huaweicloud.com", ("huawei", "cloud")),
    ("Bulut", "Cloudflare", "https://www.cloudflare.com", ("cloudflare",)),
    ("Türkiye", "İş Bankası", "https://www.isbank.com.tr", ("iş bankası", "isbank")),
    ("Türkiye", "Yapı Kredi", "https://www.yapikredi.com.tr", ("yapı kredi", "yapikredi")),
    ("Türkiye", "Akbank", "https://www.akbank.com", ("akbank",)),
    ("Türkiye", "Garanti BBVA", "https://www.garantibbva.com.tr", ("garanti", "bbva")),
    ("Türkiye", "e-Devlet", "https://www.turkiye.gov.tr", ("turkiye", "giriş")),
    ("Türkiye", "SGK", "https://www.sgk.gov.tr", ("sgk", "sosyal güvenlik")),
]
SERVICE_NAMES = {service[1] for service in SERVICES}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("eclipta_durum")
app = Flask(__name__)


def database_connection() -> sqlite3.Connection:
    """Yeni, kısa ömürlü ve thread güvenli bir SQLite bağlantısı döndürür."""
    connection = sqlite3.connect(DATABASE, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def initialise_database() -> None:
    """İzleme, bildirim ve olay tablolarını oluşturur."""
    with database_connection() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS status_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('UP', 'DOWN', 'DEGRADED')),
                response_time REAL,
                cert_days INTEGER,
                timestamp TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_status_log_service_time
                ON status_log(service, timestamp DESC);
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                issue_type TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS incidents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                status TEXT NOT NULL CHECK(status IN ('ongoing', 'resolved'))
            );
            CREATE INDEX IF NOT EXISTS idx_incidents_service_status
                ON incidents(service, status);
        """)


def certificate_days(url: str) -> int | None:
    """HTTPS sertifikasının UTC bitişine kalan tam gün sayısını hesaplar."""
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return None
    context = ssl.create_default_context()
    with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=REQUEST_TIMEOUT) as raw_socket:
        with context.wrap_socket(raw_socket, server_hostname=parsed.hostname) as secure_socket:
            certificate = secure_socket.getpeercert()
    expiry = datetime.strptime(certificate["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    return (expiry - datetime.now(timezone.utc)).days


def check_service(service: tuple[str, str, str, tuple[str, ...]]) -> dict[str, Any]:
    """Bir servisi HTTP gövdesindeki beklenen sözcüklere göre sınıflandırır."""
    _, name, url, expected = service
    started = time.perf_counter()
    status, response_time = "DOWN", None
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "EcliptaDurum/1.0"})
        response_time = round((time.perf_counter() - started) * 1000, 1)
        if 200 <= response.status_code < 400:
            text = response.text.casefold()
            status = "UP" if any(term.casefold() in text for term in expected) else "DEGRADED"
        else:
            status = "DOWN"
    except requests.RequestException as error:
        logger.warning("%s HTTP kontrolü başarısız: %s", name, error)

    cert_days = None
    if urlparse(url).scheme.lower() == "https":
        try:
            cert_days = certificate_days(url)
        except (OSError, ssl.SSLError, KeyError, ValueError) as error:
            logger.warning("%s sertifika kontrolü başarısız: %s", name, error)
    return {"service": name, "status": status, "response_time": response_time, "cert_days": cert_days}


def record_result(result: dict[str, Any]) -> None:
    """Kontrol sonucunu saklar ve DOWN olaylarını başlatır/çözer."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with database_connection() as db:
        db.execute(
            "INSERT INTO status_log(service, status, response_time, cert_days, timestamp) VALUES (?, ?, ?, ?, ?)",
            (result["service"], result["status"], result["response_time"], result["cert_days"], now),
        )
        ongoing = db.execute(
            "SELECT id FROM incidents WHERE service = ? AND status = 'ongoing' ORDER BY id DESC LIMIT 1",
            (result["service"],),
        ).fetchone()
        if result["status"] == "DOWN" and ongoing is None:
            db.execute("INSERT INTO incidents(service, start_time, status) VALUES (?, ?, 'ongoing')", (result["service"], now))
        elif result["status"] != "DOWN" and ongoing is not None:
            db.execute("UPDATE incidents SET end_time = ?, status = 'resolved' WHERE id = ?", (now, ongoing["id"]))


def monitor_once() -> None:
    """Tüm servisleri sırayla kontrol eder; ağ yükünü ölçülü tutar."""
    for service in SERVICES:
        result = check_service(service)
        record_result(result)
        logger.info("%s: %s", result["service"], result["status"])


def background_monitor() -> None:
    """Uygulama ömrü boyunca daemon thread içinde periyodik tarama yapar."""
    while True:
        try:
            monitor_once()
        except Exception:  # döngünün tek servis hatasıyla ölmesini önler
            logger.exception("Arka plan izleme turu beklenmedik şekilde başarısız oldu")
        time.sleep(CHECK_INTERVAL)  # Varsayılan time.sleep(900); Hızlı test için time.sleep(10) yap


def fetch_status() -> dict[str, Any]:
    """Kartlar için son 24 saatin durum, uptime ve zaman serisini döndürür."""
    cutoff = datetime.now(timezone.utc).timestamp() - 24 * 60 * 60
    with database_connection() as db:
        rows = db.execute("SELECT * FROM status_log WHERE timestamp >= ? ORDER BY timestamp ASC", (datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds"),)).fetchall()
        reported = {row["service"] for row in db.execute("SELECT DISTINCT service FROM reports WHERE timestamp >= ?", (datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds"),))}
    by_service: dict[str, list[sqlite3.Row]] = {name: [] for name in SERVICE_NAMES}
    for row in rows:
        if row["service"] in by_service:
            by_service[row["service"]].append(row)

    categories: dict[str, list[dict[str, Any]]] = {}
    overall_statuses = []
    for category, name, url, _ in SERVICES:
        history = by_service[name][-MAX_HISTORY_POINTS:]
        latest = history[-1] if history else None
        status = latest["status"] if latest else "DEGRADED"
        if name in reported and status == "UP":
            status = "DEGRADED"
        overall_statuses.append(status)
        successful = sum(row["status"] != "DOWN" for row in history)
        categories.setdefault(category, []).append({
            "name": name, "url": url, "protocol": urlparse(url).scheme.upper(), "status": status,
            "response_time": latest["response_time"] if latest else None,
            "cert_days": latest["cert_days"] if latest else None,
            "uptime": round(successful / len(history) * 100, 1) if history else None,
            "history": [{"status": row["status"], "response_time": row["response_time"], "timestamp": row["timestamp"]} for row in history],
        })
    if "DOWN" in overall_statuses:
        hero = {"level": "down", "message": "Bazı servislerde kesinti var, ekiplerimiz durumu inceliyor."}
    elif "DEGRADED" in overall_statuses:
        hero = {"level": "degraded", "message": "Bazı servislerde yavaşlama veya erişim sorunları bildirildi."}
    else:
        hero = {"level": "up", "message": "Tüm sistemler normal çalışıyor"}
    return {"hero": hero, "categories": categories, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


HTML = r"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Eclipta Durum</title><style>
*{box-sizing:border-box}body{margin:0;background:#f6f8fb;color:#172033;font:14px/1.45 Arial,sans-serif}.wrap{max-width:1180px;margin:auto;padding:28px 18px}header{display:flex;align-items:baseline;gap:12px}h1{margin:0;color:#2563eb;font-size:28px}header p{margin:0;color:#64748b}.hero{margin:22px 0;padding:16px 18px;border-radius:12px;color:#fff;font-weight:700}.hero.up{background:#16a34a}.hero.down{background:#dc2626}.hero.degraded{background:#ca8a04}.category{margin-top:26px}.category h2{font-size:16px;margin:0 0 10px}.card{background:#fff;border:1px solid #e5eaf2;border-radius:12px;padding:14px;margin:8px 0;display:grid;grid-template-columns:minmax(210px,1.2fr) minmax(180px,1fr) 150px;align-items:center;gap:14px;box-shadow:0 1px 2px #17203308}.identity{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.dot{width:10px;height:10px;border-radius:50%;background:#ca8a04}.dot.UP{background:#16a34a}.dot.DOWN{background:#dc2626}.name{font-weight:700}.badge{font-size:12px;padding:3px 7px;border-radius:99px;background:#eaf0ff;color:#2563eb}.cert{background:#eef2f7;color:#475569}.cert.warn{background:#fef3c7;color:#92400e}.cert.bad{background:#fee2e2;color:#991b1b}.metrics{color:#64748b;white-space:nowrap}.metrics strong{color:#172033}.graph{height:45px}.graph svg{display:block;width:100%;height:45px}.empty{color:#94a3b8;font-size:12px;text-align:center}.updated{color:#64748b;font-size:12px;margin:26px 0 0}@media(max-width:720px){.card{grid-template-columns:1fr}.graph{order:3}.metrics{order:2}}
</style></head><body><main class="wrap"><header><h1>Eclipta Durum</h1><p>Dijital altyapı servis izleme</p></header><section id="hero" class="hero degraded">Veri yükleniyor…</section><div id="content"></div><p id="updated" class="updated"></p></main><script>
const esc=s=>String(s).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
function graph(history){if(history.length<2)return '<div class="empty">Veri bekleniyor…</div>';let max=Math.max(...history.map(x=>x.response_time||0),1);let points=history.map((x,i)=>{let y=x.status==='DOWN'?42:4+((x.response_time||max)/max)*30;return [i*100/(history.length-1),y,x.status]});let run=(wanted,color)=>{let groups=[],current=[];points.forEach(p=>{if(p[2]===wanted){current.push(p)}else if(current.length){groups.push(current);current=[]}});if(current.length)groups.push(current);return groups.map(g=>'<polyline fill="none" stroke="'+color+'" stroke-width="2" points="'+g.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ')+'"/>').join('')};let good=points.map(p=>p[2]!=='DOWN'?[p[0],p[1],'GOOD']:p);let goodRuns=[],cur=[];good.forEach(p=>{if(p[2]==='GOOD')cur.push(p);else{if(cur.length)goodRuns.push(cur);cur=[]}});if(cur.length)goodRuns.push(cur);let down=points.filter(p=>p[2]==='DOWN');return '<svg viewBox="0 0 100 46" preserveAspectRatio="none" aria-label="24 saatlik gecikme grafiği"><line x1="0" y1="42" x2="100" y2="42" stroke="#e5eaf2"/> '+goodRuns.map(g=>'<polyline fill="none" stroke="#16a34a" stroke-width="2" points="'+g.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ')+'"/>').join('')+run('DOWN','#dc2626')+down.map(p=>'<circle cx="'+p[0].toFixed(1)+'" cy="'+p[1].toFixed(1)+'" r="1.8" fill="#dc2626"/>').join('')+'</svg>'}
function cert(days){if(days===null||days===undefined)return '<span class="badge cert">🔐 Bilinmiyor</span>';if(days<0)return '<span class="badge cert bad">🔴 Sertifika süresi dolmuş</span>';if(days<30)return '<span class="badge cert warn">🟡 Sertifika: '+days+' gün</span>';return '<span class="badge cert">🔐 '+days+' gün</span>'}
function render(data){let hero=document.querySelector('#hero');hero.className='hero '+data.hero.level;hero.textContent=(data.hero.level==='up'?'✅ ':data.hero.level==='down'?'⚠️ ':'⚡ ')+data.hero.message;document.querySelector('#content').innerHTML=Object.entries(data.categories).map(([category,services])=>'<section class="category"><h2>'+esc(category)+'</h2>'+services.map(s=>'<article class="card"><div class="identity"><i class="dot '+s.status+'"></i><span class="name">'+esc(s.name)+'</span><span class="badge">'+esc(s.protocol)+'</span>'+cert(s.cert_days)+'</div><div class="graph">'+graph(s.history)+'</div><div class="metrics"><strong>'+ (s.uptime===null?'—':s.uptime+'%')+'</strong> uptime · <strong>'+(s.response_time===null?'—':Math.round(s.response_time)+' ms')+'</strong></div></article>').join('')+'</section>').join('');document.querySelector('#updated').textContent='Son güncelleme: '+new Date(data.updated_at).toLocaleString('tr-TR')}
async function refresh(){try{let r=await fetch('/api/status',{headers:{Accept:'application/json'}});if(!r.ok)throw Error();render(await r.json())}catch(e){document.querySelector('#hero').textContent='⚠️ Durum verisi alınamadı.'}}refresh();setInterval(refresh,60000);
</script></body></html>"""


@app.get("/")
def index() -> str:
    """Durum panosunu sunar; veri istemci tarafından yenilenir."""
    return render_template_string(HTML)


@app.get("/api/status")
def api_status():
    """Anlık durum ve 24 saatlik grafik verisini JSON olarak verir."""
    return jsonify(fetch_status())


@app.post("/api/report")
def api_report():
    """UI olmaksızın, doğrulanmış servis sorun bildirimini saklar."""
    payload = request.get_json(silent=True) or {}
    service = payload.get("service")
    issue_type = payload.get("issue_type")
    if not isinstance(service, str) or service not in SERVICE_NAMES or not isinstance(issue_type, str) or not issue_type.strip() or len(issue_type) > 120:
        return jsonify({"error": "Geçerli service ve issue_type alanları zorunludur."}), 400
    with database_connection() as db:
        db.execute("INSERT INTO reports(service, issue_type, timestamp) VALUES (?, ?, ?)", (service, issue_type.strip(), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    logger.info("Sorun bildirimi: %s", service)
    return jsonify({"ok": True}), 201


initialise_database()
if __name__ == "__main__":
    threading.Thread(target=background_monitor, name="eclipta-monitor", daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
