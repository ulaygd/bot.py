import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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
MAX_MATCHES = 10

# Süper Lig, Premier Lig, La Liga, Serie A, Bundesliga, Ligue 1, ŞL, AL, KL
TOP_IDS = {203, 39, 140, 135, 78, 61, 2, 3, 848}
EXCLUDE = (
    "women", "femin", "u16", "u17", "u18", "u19", "u20", "u21", "u23",
    "youth", "reserve", "amateur", "clubs", "futsal", "beach",
)

diag = []  # hata ayıklama notları


# ---------------- API-Football ----------------
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


def is_wanted(fx):
    lg = fx["league"]
    home, away = fx["teams"]["home"]["name"], fx["teams"]["away"]["name"]
    text = f"{lg['name']} {home} {away}".lower()
    if any(w in text for w in EXCLUDE):
        return False
    if home.lower().endswith(" w") or away.lower().endswith(" w"):
        return False
    return lg["id"] in TOP_IDS or lg.get("country") == "World"


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
    today = datetime.now(TR).date().isoformat()
    data = af_get("/fixtures", {"date": today, "timezone": "Europe/Istanbul"}, note=True)
    if not data:
        return None, 0
    resp = data.get("response", [])
    items = []
    for fx in resp:
        if fx["fixture"]["status"]["short"] != "NS":
            continue
        if not is_wanted(fx):
            continue
        kick = datetime.fromisoformat(fx["fixture"]["date"]).astimezone(TR)
        items.append((kick, fx))
    diag.append(f"API-Football: bugün {len(resp)} maç, filtre sonrası {len(items)}")
    items.sort(key=lambda x: x[0])
    items = items[:MAX_MATCHES]
    if not items:
        return None, 0

    lines = []
    for kick, fx in items:
        fid = fx["fixture"]["id"]
        lines.append(f"MAÇ: {fx['teams']['home']['name']} - {fx['teams']['away']['name']}")
        lines.append(f"Lig: {fx['league']['name']} | Saat (TR): {kick.strftime('%H:%M')}")
        pred = af_get("/predictions", {"fixture": fid})
        if pred and pred.get("response"):
            lines.extend(fmt_prediction(pred["response"][0]))
        inj = af_get("/injuries", {"fixture": fid})
        if inj and inj.get("response"):
            lines.append("Eksikler: " + "; ".join(fmt_injuries(inj["response"])))
        lines.append("")
    return "\n".join(lines), len(items)


# ---------------- football-data.org (yedek) ----------------
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
    today = datetime.now(TR).date()
    tomorrow = today + timedelta(days=1)
    data = fd_get(
        "/matches", {"dateFrom": today.isoformat(), "dateTo": tomorrow.isoformat()}
    )
    if not data:
        return None, 0
    matches = []
    for m in data.get("matches", []):
        if m.get("status") not in ("TIMED", "SCHEDULED"):
            continue
        kick = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(TR)
        if kick.date() == today:
            matches.append((kick, m))
    matches.sort(key=lambda x: x[0])
    matches = matches[:MAX_MATCHES]
    diag.append(f"football-data: bugün {len(matches)} maç")
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
        lines.append(f"Lig: {m['competition']['name']} | Saat (TR): {kick.strftime('%H:%M')}")
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


# ---------------- Gemini ----------------
def build_prompt(data_text: str) -> str:
    today = datetime.now(TR).strftime("%d.%m.%Y")
    return f"""Bugünün tarihi: {today}. Aşağıda futbol API'lerinden alınmış GERÇEK maç verileri var.

SADECE bu verilere dayanarak analiz yap. Veride olmayan bilgiyi uydurma.
Bir maç için sakatlık/form verisi yoksa bunu o maçın risk notunda belirt.

Görevin:
1. Bu maçlar arasından en yüksek olasılıklı en az 2-3 maçı seç. Gerçekten güvenilir olan daha fazlaysa hepsini ekle (en fazla 6).
   Güvenilir maç yoksa zorlama ve bunu dürüstçe söyle.
2. Her maç için: ⚽ maç, lig, saat (TR) / 🎯 tahmin (MS1, MS0, MS2, Alt-Üst, KG Var-Yok veya Çifte şans) /
   📊 güven yüzdesi (tahmini) / 📝 verilere dayanan 2-3 cümlelik kısa analiz / ⚠️ tek cümlelik risk notu.
3. Başa kısa bir "📅 Günün Özeti", sona "Garanti yoktur, bahis risklidir" uyarısı ekle.
4. Milli maçlarda kadro rotasyonu ve hazırlık maçı motivasyonu risklidir, bunu risk notlarına yansıt.

Kurallar: Türkçe yaz, bol emoji kullan, Markdown işareti (*, _, #) KULLANMA, düz metin ve emoji yaz.

VERİLER:
{data_text}"""


def ask_gemini(prompt: str) -> str:
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3},
    }
    errors = []
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        try:
            r = requests.post(
                url, params={"key": GEMINI_API_KEY}, json=body, timeout=180
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


# ---------------- Telegram ----------------
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
    today = datetime.now(TR).strftime("%d.%m.%Y")
    try:
        data_text, count = build_af_data()
        if not data_text:
            data_text, count = build_fd_data()
        if not data_text:
            note = "\n".join(diag) if diag else "Kaynaklardan veri alınamadı."
            send_telegram(
                f"⚽ GÜNLÜK FUTBOL BÜLTENİ — {today}\n\n"
                f"Bugün analiz edilecek maç bulunamadı. 😴\n\n🔧 Kaynak durumu:\n{note}"
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
    send_telegram(f"⚽🔥 GÜNLÜK FUTBOL BÜLTENİ — {today}\n\n{report}")


if __name__ == "__main__":
    main()
