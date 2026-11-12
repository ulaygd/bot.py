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
FD_KEY = os.environ["FOOTBALL_DATA_API_KEY"].strip()
MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.1-flash-lite"]

TR = ZoneInfo("Europe/Istanbul")
FD_BASE = "https://api.football-data.org/v4"
MAX_MATCHES = 12


def fd_get(path, params=None):
    """football-data.org: dakikada 10 istek sınırı için bekleyerek çağırır."""
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


def get_todays_matches():
    now = datetime.now(TR)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    data = fd_get(
        "/matches", {"dateFrom": today.isoformat(), "dateTo": tomorrow.isoformat()}
    )
    if not data:
        return []
    result = []
    for m in data.get("matches", []):
        if m.get("status") not in ("TIMED", "SCHEDULED"):
            continue
        kick = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(TR)
        if kick.date() != today:
            continue
        result.append((kick, m))
    result.sort(key=lambda x: x[0])
    return result[:MAX_MATCHES]


def get_standings(code):
    data = fd_get(f"/competitions/{code}/standings")
    table = {}
    if not data:
        return table
    for st in data.get("standings", []):
        if st.get("type") != "TOTAL":
            continue
        for row in st.get("table", []):
            table[row["team"]["id"]] = row
    return table


def get_form(code):
    """Son 30 günde biten maçlardan her takımın son 5 maçını çıkarır."""
    today = datetime.now(timezone.utc).date()
    data = fd_get(
        f"/competitions/{code}/matches",
        {
            "status": "FINISHED",
            "dateFrom": (today - timedelta(days=30)).isoformat(),
            "dateTo": today.isoformat(),
        },
    )
    form = defaultdict(list)
    if not data:
        return form
    matches = sorted(data.get("matches", []), key=lambda m: m["utcDate"])
    for m in matches:
        ft = m["score"]["fullTime"]
        if ft["home"] is None or ft["away"] is None:
            continue
        h, a = m["homeTeam"], m["awayTeam"]
        hg, ag = ft["home"], ft["away"]
        for team, opp, gf, ga, venue in (
            (h, a, hg, ag, "İç"),
            (a, h, ag, hg, "Dep"),
        ):
            res = "G" if gf > ga else ("B" if gf == ga else "M")
            form[team["id"]].append(f"{res} {gf}-{ga} ({venue}) vs {opp['shortName'] or opp['name']}")
    return form


def build_data_text():
    matches = get_todays_matches()
    if not matches:
        return None, 0
    codes = sorted({m["competition"]["code"] for _, m in matches})
    standings, forms = {}, {}
    for code in codes:
        standings[code] = get_standings(code)
        forms[code] = get_form(code)

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
            last5 = forms[code].get(t["id"], [])[-5:]
            if last5:
                lines.append(f"{label} son maçlar: " + "; ".join(last5))
        lines.append("")
    return "\n".join(lines), len(matches)


def build_prompt(data_text: str) -> str:
    today = datetime.now(TR).strftime("%d.%m.%Y")
    return f"""Bugünün tarihi: {today}. Aşağıda football-data.org'dan alınmış GERÇEK maç verileri var.

SADECE bu verilere dayanarak analiz yap. Veride olmayan bilgiyi (sakatlık, kadro, oran, h2h) uydurma.
Sakatlık/kadro verisi olmadığını bülten sonunda tek cümleyle belirt.

Görevin:
1. Bu maçlar arasından en yüksek olasılıklı en az 2-3 maçı seç. Gerçekten güvenilir olan daha fazlaysa hepsini ekle (en fazla 6).
   Güvenilir maç yoksa zorlama ve bunu dürüstçe söyle.
2. Her maç için: ⚽ maç, lig, saat (TR) / 🎯 tahmin (MS1, MS0, MS2, Alt-Üst, KG Var-Yok veya Çifte şans) /
   📊 güven yüzdesi (tahmini) / 📝 verilere dayanan 2-3 cümlelik kısa analiz / ⚠️ tek cümlelik risk notu.
3. Başa kısa bir "📅 Günün Özeti", sona "Garanti yoktur, bahis risklidir" uyarısı ekle.

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
        data_text, count = build_data_text()
        if not data_text:
            send_telegram(
                f"⚽ GÜNLÜK FUTBOL BÜLTENİ — {today}\n\n"
                "Bugün takip ettiğim liglerde maç yok. 😴"
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
