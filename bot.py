import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"].strip()
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"].strip()
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"].strip()
FD_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
AF_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.1-flash-lite"]

TR = ZoneInfo("Europe/Istanbul")
FD_BASE = "https://api.football-data.org/v4"
AF_BASE = "https://v3.football.api-sports.io"

MAX_PRED = 40      # olasılık verisi çekilecek maç sayısı
MAX_MATCHES = 20   # Gemini'ye gidecek en güvenilir maç sayısı
FD_MAX = 10        # yedek kaynakta en fazla maç

# Süper Lig, Premier Lig, La Liga, Serie A, Bundesliga, Ligue 1,
# Şampiyonlar Ligi, Avrupa Ligi, Konferans Ligi,
# Hollanda, Portekiz, Belçika, Brezilya, Arjantin, MLS
TOP_IDS = {203, 39, 140, 135, 78, 61, 2, 3, 848, 88, 94, 144, 71, 128, 253}

INTL_WORDS = (
    "friendl", "qualif", "nations league", "world cup", "euro",
    "copa america", "africa cup", "asian cup", "gold cup",
)

EXCLUDE = (
    "women", "femin", "u16", "u17", "u18", "u19", "u20", "u21", "u23",
    "youth", "reserve", "amateur", "clubs", "futsal", "beach",
)

diag = []


def get_window():
    """Şimdi ile bir sonraki 06:00 arası."""
    now = datetime.now(TR)
    end = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= end:
        end += timedelta(days=1)
    return now, end


def af_get(path, params=None, note=False):
    time.sleep(6)
    try:
        r = requests.get(
            AF_BASE + path,
            headers={"x-apisports-key": AF_KEY},
            params=params,
            timeout=30,
        )
    except Exception as e:
        print(f"API-Football bağlantı hatası: {e}")
        if note:
            diag.append(f"API-Football bağlantı hatası: {e}")
        return None
    if r.status_code != 200:
        print(f"API-Football {path}: {r.status_code} {r.text[:200]}")
        if note:
            diag.append(f"API-Football HTTP {r.status_code}")
        return None
    data = r.json()
    errs = data.get("errors")
    if errs:
        print(f"API-Football errors {path}: {errs}")
        if note:
            diag.append(f"API-Football hata: {str(errs)[:300]}")
    return data


def is_excluded(fx):
    lg = fx["league"]
    home, away = fx["teams"]["home"]["name"], fx["teams"]["away"]["name"]
    text = f"{lg['name']} {home} {away}".lower()
    if any(w in text for w in EXCLUDE):
        return True
    if home.lower().endswith(" w") or away.lower().endswith(" w"):
        return True
    return False


def is_intl(fx):
    lg = fx["league"]
    name = lg["name"].lower()
    return lg.get("country") == "World" or any(w in name for w in INTL_WORDS)


def is_wanted(fx):
    if is_excluded(fx):
        return False
    return fx["league"]["id"] in TOP_IDS or is_intl(fx)


def pct(v):
    try:
        return int(str(v).replace("%", "").strip())
    except Exception:
        return 0


def confidence(p):
    pc = (p.get("predictions", {}) or {}).get("percent") or {}
    return max(pct(pc.get("home")), pct(pc.get("draw")), pct(pc.get("away")))


def fmt_prediction(p):
    lines = []
    pr = p.get("predictions", {}) or {}
    if pr.get("advice"):
        lines.append(f"API tavsiyesi: {pr['advice']}")
    pc = pr.get("percent")
    if pc:
        lines.append(
            f"Olasılık % (ev/beraberlik/dep): {pc.get('home')} / {pc.get('draw')} / {pc.get('away')}"
        )
    if pr.get("under_over"):
        lines.append(f"API gol çizgisi tahmini (+ üst, - alt): {pr['under_over']}")
    pg = pr.get("goals") or {}
    if pg.get("home") or pg.get("away"):
        lines.append(
            f"API beklenen gol sınırı (ev/dep): {pg.get('home')} / {pg.get('away')}"
        )
    teams = p.get("teams", {}) or {}
    for label, key in (("Ev sahibi", "home"), ("Deplasman", "away")):
        t = teams.get(key, {}) or {}
        l5 = t.get("last_5", {}) or {}
        lg = t.get("league", {}) or {}
        parts = []
        if lg.get("form"):
            parts.append(f"sezon formu {lg['form'][-5:]}")
        if l5.get("form"):
            parts.append(f"son 5 form skoru {l5['form']}")
        gf = ((l5.get("goals") or {}).get("for") or {}).get("average")
        ga = ((l5.get("goals") or {}).get("against") or {}).get("average")
        if gf is not None and ga is not None:
            parts.append(f"son 5 maç gol ort. {gf} atılan / {ga} yenilen")
        sgf = (((lg.get("goals") or {}).get("for") or {}).get("average") or {}).get("total")
        sga = (((lg.get("goals") or {}).get("against") or {}).get("average") or {}).get("total")
        if sgf and sga:
            parts.append(f"sezon maç başı gol {sgf} atılan / {sga} yenilen")
        if parts:
            lines.append(f"{label}: " + ", ".join(parts))
    comp = p.get("comparison") or {}
    if comp.get("total"):
        lines.append(
            f"Genel karşılaştırma % (ev/dep): {comp['total'].get('home')} / {comp['total'].get('away')}"
        )
    h2h = p.get("h2h") or []
    res = []
    for m in h2h[:5]:
        g = m.get("goals", {})
        res.append(
            f"{m['teams']['home']['name']} {g.get('home')}-{g.get('away')} {m['teams']['away']['name']}"
        )
    if res:
        lines.append("Son aralarındaki maçlar: " + "; ".join(res))
    return lines


def fmt_injuries(inj):
    items = []
    for i in inj[:8]:
        items.append(
            f"{i['team']['name']}: {i['player']['name']} ({i['player'].get('reason') or i['player'].get('type')})"
        )
    return items


def build_af_data():
    if not AF_KEY:
        return None, 0
    now, end = get_window()
    dates = sorted({now.date().isoformat(), end.date().isoformat()})

    resp = []
    for d in dates:
        data = af_get("/fixtures", {"date": d, "timezone": "Europe/Istanbul"}, note=True)
        if data:
            resp.extend(data.get("response", []))
    if not resp:
        return None, 0

    wanted = []
    skipped = Counter()
    in_window = 0
    for fx in resp:
        if fx["fixture"]["status"]["short"] != "NS":
            continue
        kick = datetime.fromisoformat(fx["fixture"]["date"]).astimezone(TR)
        if not (now <= kick < end):
            continue
        in_window += 1
        if is_wanted(fx):
            wanted.append((kick, fx))
        else:
            lg = fx["league"]
            skipped[f"{lg['name']} ({lg.get('country')})"] += 1

    # Önce milli maçlar, sonra büyük ligler; kendi içinde saat sırası
    wanted.sort(key=lambda x: (0 if is_intl(x[1]) else 1, x[0]))
    candidates = wanted[:MAX_PRED]

    scored = []
    nodata = []
    for kick, fx in candidates:
        pred = af_get("/predictions", {"fixture": fx["fixture"]["id"]})
        if pred and pred.get("response"):
            p = pred["response"][0]
            scored.append((confidence(p), kick, fx, p))
        else:
            nodata.append(f"{fx['teams']['home']['name']}-{fx['teams']['away']['name']}")

    diag.append(
        f"Pencere: {now.strftime('%d.%m %H:%M')} - {end.strftime('%d.%m %H:%M')} | "
        f"API-Football: pencerede {in_window} maç, uygun {len(wanted)}, "
        f"incelenen {len(candidates)}, verisi olan {len(scored)}, verisi olmayan {len(nodata)}"
    )
    if nodata:
        diag.append("Veri alınamayan maçlar: " + ", ".join(nodata[:6]))
    if skipped:
        top = ", ".join(f"{k} x{v}" for k, v in skipped.most_common(8))
        diag.append("Elenen ligler: " + top)
    if not scored:
        return None, 0

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = scored[:MAX_MATCHES]
    selected.sort(key=lambda x: x[1])  # bültende saat sırasıyla

    lines = []
    for conf, kick, fx, p in selected:
        lg = fx["league"]
        lines.append(f"MAÇ: {fx['teams']['home']['name']} - {fx['teams']['away']['name']}")
        lines.append(
            f"Lig: {lg['name']} ({lg.get('country')}) | Tarih/Saat (TR): {kick.strftime('%d.%m %H:%M')}"
        )
        lines.extend(fmt_prediction(p))
        inj = af_get("/injuries", {"fixture": fx["fixture"]["id"]})
        if inj and inj.get("response"):
            lines.append("Eksikler: " + "; ".join(fmt_injuries(inj["response"])))
        lines.append("")
    return "\n".join(lines), len(selected)


def fd_get(path, params=None):
    time.sleep(7)
    for _ in range(2):
        r = requests.get(
            FD_BASE + path,
            headers={"X-Auth-Token": FD_KEY},
            params=params,
            timeout=30,
        )
        if r.status_code == 429:
            time.sleep(60)
            continue
        if r.status_code != 200:
            print(f"football-data hatası {path}: {r.status_code} {r.text[:200]}")
            return None
        return r.json()
    return None


def build_fd_data():
    if not FD_KEY:
        return None, 0
    now, end = get_window()
    data = fd_get(
        "/matches",
        {
            "dateFrom": now.date().isoformat(),
            "dateTo": (end.date() + timedelta(days=1)).isoformat(),
        },
    )
    if not data:
        return None, 0
    matches = []
    for m in data.get("matches", []):
        if m.get("status") not in ("TIMED", "SCHEDULED"):
            continue
        kick = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(TR)
        if now <= kick < end:
            matches.append((kick, m))
    matches.sort(key=lambda x: x[0])
    matches = matches[:FD_MAX]
    diag.append(f"football-data (yedek): pencerede {len(matches)} maç")
    if not matches:
        return None, 0

    codes = sorted({m["competition"]["code"] for _, m in matches})
    standings = {}
    for code in codes:
        d = fd_get(f"/competitions/{code}/standings")
        table = {}
        if d:
            for st in d.get("standings", []):
                if st.get("type") == "TOTAL":
                    for row in st.get("table", []):
                        table[row["team"]["id"]] = row
        standings[code] = table

    lines = []
    for kick, m in matches:
        code = m["competition"]["code"]
        lines.append(f"MAÇ: {m['homeTeam']['name']} - {m['awayTeam']['name']}")
        lines.append(
            f"Lig: {m['competition']['name']} | Tarih/Saat (TR): {kick.strftime('%d.%m %H:%M')}"
        )
        for label, t in (("Ev sahibi", m["homeTeam"]), ("Deplasman", m["awayTeam"])):
            row = standings[code].get(t["id"])
            if row:
                lines.append(
                    f"{label} puan durumu: {row['position']}. sıra, {row['playedGames']} maç, "
                    f"{row['won']}G {row['draw']}B {row['lost']}M, {row['points']} puan, "
                    f"averaj {row['goalsFor']}-{row['goalsAgainst']}"
                )
        lines.append("")
    return "\n".join(lines), len(matches)


def build_prompt(data_text: str) -> str:
    now, end = get_window()
    return f"""Bugünün tarihi: {now.strftime('%d.%m.%Y')}. Kapsanan zaman aralığı (Türkiye saati):
{now.strftime('%d.%m %H:%M')} - {end.strftime('%d.%m %H:%M')}.
Aşağıda futbol API'lerinden alınmış GERÇEK maç verileri var (büyük ligler ve milli takım maçları).

SADECE bu verilere dayanarak analiz yap. Veride olmayan bilgiyi uydurma.
Bir maç için sakatlık/form verisi yoksa bunu o maçın risk notunda belirt.

Görevin:
1. Güvenilir gördüğün HER maçı yaz. En az 2-3 maç olmalı, güvenilir maç daha fazlaysa hepsini ekle, sayı sınırı yok.
   Gerçekten güvenilir maç yoksa zorlama ve bunu dürüstçe söyle. Elenen maçlar için tek satırda neden elendiğini yaz.
   Maçları saat sırasıyla yaz.

2. Seçtiğin HER maç için şu şablonu doldur (kısa ve net tut, çok maç olabilir):

⚽ Maç adı | Lig (ülke) | Tarih Saat (TR)
🔢 Skor beklentisi: en olası X-Y (%..), alternatifler X-Y (%..), X-Y (%..) | beklenen toplam gol ~..
📊 MS1 %.. MS0 %.. MS2 %.. | Çifte şans 1-0 %.. 0-2 %.. 1-2 %..
📊 Alt/Üst 1.5: Alt %.. Üst %.. | 2.5: Alt %.. Üst %.. | 3.5: Alt %.. Üst %.. | KG Var %.. KG Yok %..
🧠 Neden: hangi veri neyi destekliyor (gol ortalamaları, form, h2h, eksikler, API olasılıkları) 2-3 madde.
🎯 EN MANTIKLI BAHİS: ... (güven %..) | neden bu: MS, KG, Alt/Üst ve Çifte şansı kıyasla.
   Örnek: "MS1 %48 ama KG Var %68, bu yüzden KG Var daha mantıklı." En fazla bir alternatif bahis ekle.
⚠️ Risk: tek cümle.

3. Başa kısa bir "📅 Günün Özeti" ekle.

4. En sona "🎟️ KUPONLAR" bölümü ekle. Yukarıda yazdığın maçlardan, her maçtan EN FAZLA BİR seçim alarak:
   - 🟢 Sağlam Kupon: en yüksek güvenli 2-3 seçim.
   - 🟡 Dengeli Kupon: en güvenli 4-5 seçim (sağlam kupondakileri içerebilir).
   Her kupon için: maç, seçim, güven %, ve tahmini birleşik ihtimal (seçimlerin yüzdelerinin çarpımı, hesaplayıp yaz).
   Oran verisi olmadığı için toplam oranı hesaplama; kullanıcı oranları kendisi bahis sitesinden çarpabilir, bunu yaz.
   Seçim sayısı arttıkça birleşik ihtimalin hızla düştüğünü bir cümleyle belirt.
   Aynı seçimleri iki kupona koymak serbest ama kuponları farklı tutmaya çalış. Uygun seçim azsa kuponu küçük tut veya "uygun seçim yok" yaz.

5. En sona şu uyarıyı yaz: "Yüzdeler verilere dayalı tahmindir, istatistiksel garanti değildir. Oran verisi olmadığı için değer (value) analizi yapılmadı. Bahis risklidir."
6. Milli maçlarda kadro rotasyonu ve hazırlık maçı motivasyonu risklidir, güven yüzdesini buna göre düşük tut.
7. Veri azsa yüzdeleri uç değerlere çekme, belirsizliği yansıt. Yüzdeler tutarlı olsun (MS toplamı ~%100).

Kurallar: Türkçe yaz, bol emoji kullan, Markdown işareti (*, _, #) KULLANMA, düz metin ve emoji yaz.

VERİLER:
{data_text}"""


def ask_gemini(prompt: str) -> str:
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 32000},
    }
    errors = []
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = requests.post(
                url, params={"key": GEMINI_API_KEY}, json=body, timeout=300
            )
            if r.status_code == 200:
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts).strip()
                if text:
                    return text
                errors.append(f"{model}: boş yanıt")
            else:
                msg = f"{model}: {r.status_code} {r.text[:300]}"
                print(msg)
                errors.append(msg)
        except Exception as e:
            errors.append(f"{model}: {e}")
        time.sleep(5)
    raise RuntimeError("Gemini hatası: " + " | ".join(errors))


def chunk(text: str, limit: int = 3800):
    parts, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > limit and cur:
            parts.append(cur)
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        parts.append(cur)
    return parts


def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for part in chunk(text):
        r = requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": part.strip()},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"Telegram hatası: {r.status_code} {r.text[:300]}")
        r.raise_for_status()
        time.sleep(1)


def main():
    now, end = get_window()
    title = (
        f"⚽🔥 GÜNLÜK FUTBOL BÜLTENİ — {now.strftime('%d.%m.%Y')}\n"
        f"🕕 {now.strftime('%d.%m %H:%M')} → {end.strftime('%d.%m %H:%M')} arası maçlar"
    )
    try:
        data_text, count = build_af_data()
        if not data_text:
            data_text, count = build_fd_data()
        if not data_text:
            note = "\n".join(diag) if diag else "Kaynaklardan veri alınamadı."
            send_telegram(
                f"{title}\n\nBu aralıkta analiz edilecek maç bulunamadı. 😴\n\n🔧 Kaynak durumu:\n{note}"
            )
            return
        report = ask_gemini(build_prompt(data_text))
    except Exception as e:
        print(e)
        try:
            send_telegram(f"❌ Futbol botu hata verdi: {str(e)[:1500]}")
        except Exception:
            pass
        sys.exit(1)
    footer = "\n".join(diag)
    send_telegram(f"{title}\n\n{report}\n\n🔧 {footer}")


if __name__ == "__main__":
    main()
