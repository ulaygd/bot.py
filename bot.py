import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

E = os.environ
GEMINI_KEY = E["GEMINI_API_KEY"].strip()
TG_TOKEN = E["TELEGRAM_BOT_TOKEN"].strip()
TG_CHAT = E["TELEGRAM_CHAT_ID"].strip()
HL_KEY = E.get("HIGHLIGHTLY_API_KEY", "").strip()
MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.1-flash-lite", "gemini-flash-latest"]

TR = ZoneInfo("Europe/Istanbul")
HL = "https://sports.highlightly.net"
NAT_CAND, CLUB_CAND, NAT_KEEP, MAX_M, CAP = 10, 12, 6, 14, 95

calls = [0]
errs = []
diag = []

TOP = [
    ("super lig", "turk"), ("süper lig", "türk"), ("premier league", "england"),
    ("la liga", "spain"), ("serie a", "ital"), ("bundesliga", "german"),
    ("ligue 1", "franc"), ("eredivisie", ""), ("primeira liga", "portug"),
    ("pro league", "belg"), ("serie a", "brazil"), ("liga profesional", "argentin"),
    ("major league soccer", ""), ("mls", ""),
]
CUPS = ("champions league", "europa league", "conference league", "confederation cup")
INTL = (
    "friendl", "qualif", "nations league", "world cup", "euro", "copa america",
    "africa cup", "asian cup", "gold cup", "international", "fifa", "conmebol",
    "concacaf", "cosafa", "cecafa", "arab cup", "nations cup",
)
BAD = (
    "women", "femin", "u16", "u17", "u18", "u19", "u20", "u21", "u23", "youth",
    "reserve", "amateur", "clubs", "futsal", "beach", "2. bundesliga",
    "bundesliga 2", "la liga 2", "laliga 2", "segunda", "ligue 2", "serie b",
    "serie c", "championship", "league one", "league two", "1. lig", "2. lig",
    "3. lig",
)

PROMPT = """Zaman aralığı (Türkiye saati): @@WINDOW@@.
Aşağıda GERÇEK futbol verileri var: milli takım ve kulüp maçları karışık. "TÜR" satırı 🌍 (milli) veya 🏟️ (kulüp) der.
"Son 5 maç" ham JSON: son sonuçları ve gol ortalamalarını kendin çıkar. Sağlayıcı olasılıkları ev/beraberlik/deplasman içindir.
SADECE bu verilere dayan, veride olmayan bilgiyi (oran, kadro, sakatlık) uydurma. Veri eksikse risk notunda belirt, güveni düşür.

GÖREV:
1. Sadece güvenilir, riski az maçları seç. Milli ve kulüp maçlarını AYRI BAŞLIKLARA BÖLME: tek bir "🎯 GÜNÜN SEÇİMLERİ" listesinde, güvene göre en güvenilirden başlayarak karışık yaz, her maçın başına 🌍 veya 🏟️ koy. En az 2-3 maç, güvenilir olan daha fazlaysa hepsi (sınır yok). Güvenilmeyenleri alma, sonda tek satırda nedenleriyle yaz. Hiç güvenilir maç yoksa bunu dürüstçe söyle.
2. Her maç için:
🌍/🏟️ Maç | Lig (ülke) | Tarih Saat (TR)
🔢 Skor beklentisi: en olası X-Y (%..), alternatifler X-Y (%..), X-Y (%..) | beklenen toplam gol ~..
📊 MS1 %.. MS0 %.. MS2 %.. | Çifte şans 1-0 %.. 0-2 %.. 1-2 %..
📊 Alt/Üst 1.5, 2.5, 3.5 (Alt %.. Üst %..) | KG Var %.. KG Yok %..
🧠 Neden: hangi veri neyi destekliyor, 2-3 madde.
🎯 EN MANTIKLI BAHİS: ... (güven %..) - MS, KG, Alt/Üst ve çifte şansı kıyasla, neden bu olduğunu yaz. En fazla bir alternatif.
⚠️ Risk: tek cümle.
3. Başa kısa "📅 Günün Özeti" (kaç maç, kaçı milli/kulüp).
4. Sona "🎟️ KUPONLAR": her maçtan en fazla bir seçimle, milli ve kulüp karışık. 🟢 Sağlam Kupon (2-3 seçim), 🟡 Dengeli Kupon (4-5 seçim). Her kupon için maç, seçim, güven % ve tahmini birleşik ihtimal (yüzdelerin çarpımı, hesapla). Oran verisi yok, toplam oranı hesaplama, kullanıcı oranları kendisi çarpar. Seçim arttıkça birleşik ihtimalin hızla düştüğünü bir cümleyle belirt.
5. En sona: "Yüzdeler verilere dayalı tahmindir, garanti değildir. Oran verisi olmadığı için değer analizi yapılmadı. Bahis risklidir."
6. Milli maçlarda rotasyon ve hazırlık maçı motivasyonu risklidir, güveni düşük tut. Yüzdeler tutarlı olsun (MS toplamı ~%100).
Kurallar: Türkçe, bol emoji, Markdown işareti (*, _, #) KULLANMA, düz metin.

VERİLER:
@@DATA@@"""


def window():
    now = datetime.now(TR)
    end = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= end:
        end += timedelta(days=1)
    return now, end


def hl_get(path, params=None):
    if calls[0] >= CAP:
        return None
    calls[0] += 1
    time.sleep(1.2)
    try:
        r = requests.get(
            HL + path, headers={"x-rapidapi-key": HL_KEY}, params=params, timeout=40
        )
        if r.status_code != 200:
            errs.append(f"{path} HTTP {r.status_code} {r.text[:120]}")
            return None
        return r.json()
    except Exception as e:
        errs.append(f"{path} {e}")
        return None


def nm(x):
    if isinstance(x, dict):
        return str(x.get("name") or x.get("code") or "")
    return str(x or "")


def lg_text(m):
    return f"{nm(m.get('league'))} ({nm(m.get('country'))})"


def kick(m):
    try:
        d = str(m.get("date")).replace("Z", "+00:00")
        return datetime.fromisoformat(d).astimezone(TR)
    except Exception:
        return None


def prio(m):
    """0 milli, 1 kupa, 2 büyük lig, 9 diğer/elenen."""
    lg = nm(m.get("league")).lower()
    h = nm(m.get("homeTeam")).lower()
    a = nm(m.get("awayTeam")).lower()
    if any(w in f"{lg} {h} {a}" for w in BAD) or h.endswith(" w") or a.endswith(" w"):
        return 9
    txt = f"{lg} {nm(m.get('country')).lower()}"
    if any(w in lg for w in CUPS):
        return 1
    if any(w in lg for w in INTL):
        return 0
    if any(x in txt and y in txt for x, y in TOP):
        return 2
    return 9


def to_pct(v):
    try:
        f = float(str(v).replace("%", "").strip())
    except Exception:
        return 0.0
    return f * 100 if f <= 1 else f


def compact(obj, n):
    try:
        s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        s = str(obj)
    return s[:n]


def matches_of(date_str):
    out, offset = [], 0
    for _ in range(8):
        data = hl_get(
            "/football/matches",
            {"date": date_str, "limit": 100, "offset": offset, "timezone": "Europe/Istanbul"},
        )
        if not data:
            break
        if isinstance(data, dict):
            items = data.get("data") or []
            total = (data.get("pagination") or {}).get("totalCount", 0)
        else:
            items, total = data, 0
        out.extend(items)
        offset += 100
        if not items or offset >= total:
            break
    return out


def detail_of(mid):
    if not mid:
        return None
    data = hl_get(f"/football/matches/{mid}")
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list) and inner:
            return inner[0]
        return data
    return None


def probs_of(detail):
    try:
        pre = (detail.get("predictions") or {}).get("prematch") or []
        pr = pre[0].get("probabilities") or {}
        p = {k: to_pct(pr.get(k)) for k in ("home", "draw", "away")}
        return (p, pre[0]) if max(p.values()) > 0 else (None, None)
    except Exception:
        return None, None


def build_data():
    if not HL_KEY:
        diag.append("HIGHLIGHTLY_API_KEY bulunamadı")
        return None
    now, end = window()
    seen, fetched = set(), []
    for d in sorted({now.date().isoformat(), end.date().isoformat()}):
        for m in matches_of(d):
            if m.get("id") not in seen:
                seen.add(m.get("id"))
                fetched.append(m)
    if not fetched:
        diag.append("Maç listesi boş geldi. " + " | ".join(errs[:3]))
        return None

    wanted, skipped, leagues = [], Counter(), Counter()
    for m in fetched:
        k = kick(m)
        if k is None or not (now <= k < end):
            continue
        leagues[lg_text(m)] += 1
        if prio(m) < 9:
            wanted.append((k, m))
        else:
            skipped[lg_text(m)] += 1
    wanted.sort(key=lambda x: (prio(x[1]), x[0]))

    nat = [x for x in wanted if prio(x[1]) == 0][:NAT_CAND]
    club = [x for x in wanted if prio(x[1]) != 0]
    cands = nat + club[: max(0, NAT_CAND + CLUB_CAND - len(nat))]

    rated = []
    for k, m in cands:
        det = detail_of(m.get("id"))
        p, pre = probs_of(det) if det else (None, None)
        conf = max(p.values()) if p else 0
        rated.append((conf, k, m, p, pre, prio(m) == 0))
    n_pred = sum(1 for x in rated if x[3])

    nat_sel = sorted([x for x in rated if x[5]], key=lambda x: -x[0])[:NAT_KEEP]
    oth = sorted([x for x in rated if not x[5]], key=lambda x: -x[0])
    sel = nat_sel + oth[: max(0, MAX_M - len(nat_sel))]
    sel.sort(key=lambda x: -x[0])

    diag.append(
        f"Pencere: {now.strftime('%d.%m %H:%M')} - {end.strftime('%d.%m %H:%M')} | "
        f"çekilen {len(fetched)}, uygun {len(wanted)}, incelenen {len(cands)}, "
        f"tahmin verisi olan {n_pred}, seçilen {len(sel)} "
        f"(milli {len(nat_sel)}, kulüp {len(sel) - len(nat_sel)})"
    )
    if leagues:
        diag.append("Penceredeki ligler: " + ", ".join(f"{k} x{v}" for k, v in leagues.most_common(12)))
    if skipped:
        diag.append("Elenen ligler: " + ", ".join(f"{k} x{v}" for k, v in skipped.most_common(8)))
    if not sel:
        diag.append("Uygun maç yok. " + " | ".join(errs[:3]))
        return None

    cache = {}

    def last5(team):
        tid = team.get("id") if isinstance(team, dict) else None
        if not tid:
            return None
        if tid not in cache:
            cache[tid] = hl_get(f"/football/last-five/{tid}")
        return cache[tid]

    lines = []
    for conf, k, m, p, pre, is_nat in sel:
        h, a = m.get("homeTeam") or {}, m.get("awayTeam") or {}
        lines.append(f"MAÇ: {nm(h)} - {nm(a)}")
        lines.append("TÜR: " + ("🌍 milli takım maçı" if is_nat else "🏟️ kulüp maçı"))
        lines.append(f"Lig: {lg_text(m)} | Tarih/Saat (TR): {k.strftime('%d.%m %H:%M')}")
        if p:
            lines.append(
                f"Sağlayıcı olasılıkları % (ev/beraberlik/dep): "
                f"{p['home']:.0f} / {p['draw']:.0f} / {p['away']:.0f}"
            )
            lines.append(f"Model bilgisi: {compact(pre, 300)}")
        else:
            lines.append("Sağlayıcı tahmin verisi yok.")
        lh, la = last5(h), last5(a)
        lines.append(f"Ev sahibi son 5 maç: {compact(lh, 1500) if lh else 'yok'}")
        lines.append(f"Deplasman son 5 maç: {compact(la, 1500) if la else 'yok'}")
        lines.append("")
    diag.append(f"Highlightly istek sayısı: {calls[0]}/100")
    if errs:
        diag.append("Hatalar: " + " | ".join(errs[:3]))
    return "\n".join(lines)


def ask_gemini(data):
    now, end = window()
    win = f"{now.strftime('%d.%m %H:%M')} - {end.strftime('%d.%m %H:%M')}"
    prompt = PROMPT.replace("@@WINDOW@@", win).replace("@@DATA@@", data)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 32000},
    }
    errors = []
    for model in MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(4):
            try:
                r = requests.post(url, params={"key": GEMINI_KEY}, json=body, timeout=300)
            except Exception as e:
                errors.append(f"{model}: {e}")
                time.sleep(20)
                continue
            if r.status_code == 200:
                try:
                    parts = r.json()["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts).strip()
                except Exception:
                    text = ""
                if text:
                    return text
                errors.append(f"{model}: boş yanıt")
                break
            msg = f"{model}: {r.status_code} {r.text[:120]}"
            print(msg)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(30 * (attempt + 1))
                continue
            errors.append(msg)
            break
    raise RuntimeError("Gemini hatası: " + " | ".join(errors)[:1500])


def send(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    parts, cur = [], ""
    for para in text.split("\n\n"):
        if len(cur) + len(para) + 2 > 3800 and cur:
            parts.append(cur)
            cur = ""
        cur += para + "\n\n"
    if cur.strip():
        parts.append(cur)
    for part in parts:
        r = requests.post(url, json={"chat_id": TG_CHAT, "text": part.strip()}, timeout=30)
        if r.status_code != 200:
            print(f"Telegram hatası: {r.status_code} {r.text[:300]}")
        r.raise_for_status()
        time.sleep(1)


def main():
    now, end = window()
    title = (
        f"⚽🔥 GÜNLÜK FUTBOL BÜLTENİ — {now.strftime('%d.%m.%Y')}\n"
        f"🕕 {now.strftime('%d.%m %H:%M')} → {end.strftime('%d.%m %H:%M')} arası maçlar"
    )
    try:
        data = build_data()
        if not data:
            note = "\n".join(diag) or "Veri alınamadı."
            send(f"{title}\n\nBu aralıkta analiz edilecek maç bulunamadı. 😴\n\n🔧 Kaynak durumu:\n{note}")
            return
        report = ask_gemini(data)
    except Exception as e:
        print(e)
        try:
            send(f"❌ Futbol botu hata verdi: {str(e)[:1500]}")
        except Exception:
            pass
        sys.exit(1)
    send(f"{title}\n\n{report}\n\n🔧 " + "\n".join(diag))


if __name__ == "__main__":
    main()
