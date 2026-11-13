import json
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
HL_KEY = os.environ.get("HIGHLIGHTLY_API_KEY", "").strip()
AF_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
FD_KEY = os.environ.get("FOOTBALL_DATA_API_KEY", "").strip()
MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.1-flash-lite"]

TR = ZoneInfo("Europe/Istanbul")
HL_BASE = "https://sports.highlightly.net"
AF_BASE = "https://v3.football.api-sports.io"
FD_BASE = "https://api.football-data.org/v4"

HL_PRED = 22       # Highlightly: olasılık çekilecek maç sayısı
HL_MAX = 16        # Highlightly: Gemini'ye gidecek toplam maç sayısı
AF_PRED = 20       # API-Football: olasılık çekilecek maç sayısı
AF_MAX = 10        # API-Football: Gemini'ye gidecek toplam maç sayısı
NAT_MAX = 16       # her kaynakta garanti verilen en fazla milli maç
FD_MAX = 10        # yedek kaynakta en fazla maç
CALL_CAP = 95      # Highlightly günlük 100 istek sınırına karşı güvenlik

calls = [0]
hl_errors = []
diag = []

# (lig adı parçası, ülke parçası) - Highlightly için
TOP = [
    ("super lig", "turk"), ("süper lig", "türk"),
    ("premier league", "england"), ("la liga", "spain"),
    ("serie a", "ital"), ("bundesliga", "german"),
    ("ligue 1", "franc"), ("eredivisie", ""),
    ("primeira liga", "portug"), ("pro league", "belg"),
    ("serie a", "brazil"), ("liga profesional", "argentin"),
    ("major league soccer", ""), ("mls", ""),
]
# API-Football lig ID'leri: Süper Lig, Premier Lig, La Liga, Serie A, Bundesliga,
# Ligue 1, Şampiyonlar Ligi, Avrupa Ligi, Konferans Ligi, Hollanda, Portekiz,
# Belçika, Brezilya, Arjantin, MLS
TOP_IDS = {203, 39, 140, 135, 78, 61, 2, 3, 848, 88, 94, 144, 71, 128, 253}

# Kulüp kupaları (CAF Şampiyonlar Ligi dahil)
CUPS = ("champions league", "europa league", "conference league", "confederation cup")
# Milli takım yarışmaları
INTL = (
    "friendl", "qualif", "nations league", "world cup", "euro",
    "copa america", "africa cup", "asian cup", "gold cup", "international",
    "fifa", "conmebol", "concacaf", "cosafa", "cecafa", "arab cup",
    "nations cup",
)
EXCLUDE = (
    "women", "femin", "u16", "u17", "u18", "u19", "u20", "u21", "u23",
    "youth", "reserve", "amateur", "clubs", "futsal", "beach",
    "2. bundesliga", "bundesliga 2", "la liga 2", "laliga 2", "segunda",
    "ligue 2", "serie b", "serie c", "championship", "league one",
    "league two", "1. lig", "2. lig", "3. lig",
)


def get_window():
    """Şimdi ile bir sonraki 06:00 arası."""
    now = datetime.now(TR)
    end = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= end:
        end += timedelta(days=1)
    return now, end


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


def pick(rated, max_n):
    """Milli maçlar garanti, kalan yer en yüksek güvenli kulüp maçlarına.
    rated: (conf, kick, match, ..., nat) - nat her zaman son eleman."""
    nat = [x for x in rated if x[-1]][:NAT_MAX]
    others = sorted([x for x in rated if not x[-1]], key=lambda x: x[0], reverse=True)
    selected = nat + others[: max(0, max_n - len(nat))]
    selected.sort(key=lambda x: x[1])
    return selected


# =============== Highlightly ===============
def hl_get(path, params=None):
    if calls[0] >= CALL_CAP:
        return None
    calls[0] += 1
    time.sleep(1.2)
    try:
        r = requests.get(
            HL_BASE + path,
            headers={"x-rapidapi-key": HL_KEY},
            params=params,
            timeout=40,
        )
    except Exception as e:
        hl_errors.append(f"bağlantı hatası: {e}")
        return None
    if r.status_code != 200:
        msg = f"{path} HTTP {r.status_code} {r.text[:150]}"
        print("Highlightly " + msg)
        if len(hl_errors) < 3:
            hl_errors.append(msg)
        return None
    try:
        return r.json()
    except Exception:
        hl_errors.append(f"{path} geçersiz JSON")
        return None


def name_of(x):
    if isinstance(x, dict):
        return str(x.get("name") or x.get("code") or "")
    return str(x or "")


def league_text(m):
    return f"{name_of(m.get('league'))} ({name_of(m.get('country'))})"


def parse_kick(m):
    try:
        return datetime.fromisoformat(str(m.get("date")).replace("Z", "+00:00")).astimezone(TR)
    except Exception:
        return None


def hl_excluded(m):
    text = (
        f"{name_of(m.get('league'))} {name_of(m.get('homeTeam'))} {name_of(m.get('awayTeam'))}"
    ).lower()
    if any(w in text for w in EXCLUDE):
        return True
    h = name_of(m.get("homeTeam")).lower()
    a = name_of(m.get("awayTeam")).lower()
    return h.endswith(" w") or a.endswith(" w")


def hl_priority(m):
    """0 = milli takım, 1 = kulüp kupaları, 2 = büyük lig, 9 = diğer."""
    lg = name_of(m.get("league")).lower()
    text = f"{lg} {name_of(m.get('country')).lower()}"
    if any(w in lg for w in CUPS):
        return 1
    if any(w in lg for w in INTL):
        return 0
    for league_kw, country_kw in TOP:
        if league_kw in text and country_kw in text:
            return 2
    return 9


def hl_nat(m):
    return hl_priority(m) == 0


def hl_wanted(m):
    return (not hl_excluded(m)) and hl_priority(m) < 9


def hl_matches(date_str):
    out = []
    offset = 0
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


def hl_detail(mid):
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


def hl_probs(detail):
    try:
        pre = (detail.get("predictions") or {}).get("prematch") or []
        if not pre:
            return None, None
        pr = pre[0].get("probabilities") or {}
        probs = {k: to_pct(pr.get(k)) for k in ("home", "draw", "away")}
        if max(probs.values()) <= 0:
            return None, None
        return probs, pre[0]
    except Exception:
        return None, None


def build_hl_data():
    if not HL_KEY:
        diag.append("Highlightly: HIGHLIGHTLY_API_KEY bulunamadı")
        return None, 0
    now, end = get_window()
    dates = sorted({now.date().isoformat(), end.date().isoformat()})

    seen, fetched = set(), []
    for d in dates:
        for m in hl_matches(d):
            mid = m.get("id")
            if mid in seen:
                continue
            seen.add(mid)
            fetched.append(m)
    if not fetched:
        diag.append("Highlightly: maç listesi boş geldi. " + " | ".join(hl_errors))
        return None, 0

    wanted = []
    skipped = Counter()
    all_leagues = Counter()
    in_window = 0
    for m in fetched:
        kick = parse_kick(m)
        if kick is None or not (now <= kick < end):
            continue
        in_window += 1
        all_leagues[league_text(m)] += 1
        if hl_wanted(m):
            wanted.append((kick, m))
        else:
            skipped[league_text(m)] += 1

    wanted.sort(key=lambda x: (hl_priority(x[1]), x[0]))
    candidates = wanted[:HL_PRED]

    rated = []
    for kick, m in candidates:
        detail = hl_detail(m.get("id"))
        probs, pre = hl_probs(detail) if detail else (None, None)
        conf = max(probs.values()) if probs else 0
        rated.append((conf, kick, m, probs, pre, hl_nat(m)))

    with_pred = sum(1 for x in rated if x[3])
    selected = pick(rated, HL_MAX)
    nat_count = sum(1 for x in selected if x[-1])

    diag.append(
        f"Pencere: {now.strftime('%d.%m %H:%M')} - {end.strftime('%d.%m %H:%M')} | "
        f"Highlightly: çekilen {len(fetched)}, pencerede {in_window}, uygun {len(wanted)}, "
        f"incelenen {len(candidates)}, tahmin verisi olan {with_pred}, "
        f"seçilen {len(selected)} (milli {nat_count})"
    )
    if all_leagues:
        top_all = ", ".join(f"{k} x{v}" for k, v in all_leagues.most_common(12))
        diag.append("Penceredeki ligler: " + top_all)
    if skipped:
        top = ", ".join(f"{k} x{v}" for k, v in skipped.most_common(8))
        diag.append("Highlightly elenen ligler: " + top)
    if not selected:
        diag.append("Highlightly: uygun maç yok. " + " | ".join(hl_errors))
        return None, 0

    team_cache = {}

    def last_five(team):
        tid = team.get("id") if isinstance(team, dict) else None
        if not tid:
            return None
        if tid not in team_cache:
            team_cache[tid] = hl_get(f"/football/last-five/{tid}")
        return team_cache[tid]

    lines = []
    skipped_l5 = 0
    for conf, kick, m, probs, pre, nat in selected:
        home, away = m.get("homeTeam") or {}, m.get("awayTeam") or {}
        lines.append(f"MAÇ: {name_of(home)} - {name_of(away)}")
        lines.append(f"TÜR: {'🌍 MİLLİ TAKIM MAÇI' if nat else 'Kulüp maçı'}")
        lines.append(
            f"Lig: {league_text(m)} | Tarih/Saat (TR): {kick.strftime('%d.%m %H:%M')}"
        )
        if probs:
            lines.append(
                f"Sağlayıcı modeli olasılıkları % (ev/beraberlik/dep): "
                f"{probs['home']:.0f} / {probs['draw']:.0f} / {probs['away']:.0f}"
            )
            lines.append(f"Model bilgisi: {compact(pre, 300)}")
        else:
            lines.append("Sağlayıcı tahmin verisi yok.")
        l5h, l5a = last_five(home), last_five(away)
        if l5h is None and l5a is None:
            skipped_l5 += 1
        lines.append(f"Ev sahibi son 5 maç (ham veri): {compact(l5h, 1500) if l5h else 'yok'}")
        lines.append(f"Deplasman son 5 maç (ham veri): {compact(l5a, 1500) if l5a else 'yok'}")
        lines.append("")

    if skipped_l5:
        diag.append(f"Highlightly: {skipped_l5} maçta son 5 maç verisi alınamadı")
    diag.append(f"Highlightly istek sayısı: {calls[0]}/100")
    if hl_errors:
        diag.append("Highlightly hataları: " + " | ".join(hl_errors[:3]))
    return "\n".join(lines), len(selected)


# =============== API-Football ===============
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


def af_excluded(fx):
    lg = fx["league"]
    home, away = fx["teams"]["home"]["name"], fx["teams"]["away"]["name"]
    text = f"{lg['name']} {home} {away}".lower()
    if any(w in text for w in EXCLUDE):
        return True
    return home.lower().endswith(" w") or away.lower().endswith(" w")


def af_nat(fx):
    name = fx["league"]["name"].lower()
    if any(w in name for w in CUPS):
        return False
    return any(w in name for w in INTL)


def af_wanted(fx):
    if af_excluded(fx):
        return False
    name = fx["league"]["name"].lower()
    if any(w in name for w in CUPS):
        return True
    return fx["league"]["id"] in TOP_IDS or af_nat(fx)


def af_conf(p):
    pc = (p.get("predictions", {}) or {}).get("percent") or {}
    return max(to_pct(pc.get("home")), to_pct(pc.get("draw")), to_pct(pc.get("away")))


def af_fmt_prediction(p):
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


def af_fmt_injuries(inj):
    items = []
    for i in inj[:8]:
        items.append(
            f"{i['team']['name']}: {i['player']['name']} ({i['player'].get('reason') or i['player'].get('type')})"
        )
    return items


def build_af_data():
    if not AF_KEY:
        diag.append("API-Football: API_FOOTBALL_KEY bulunamadı")
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
    all_leagues = Counter()
    in_window = 0
    for fx in resp:
        if fx["fixture"]["status"]["short"] != "NS":
            continue
        kick = datetime.fromisoformat(fx["fixture"]["date"]).astimezone(TR)
        if not (now <= kick < end):
            continue
        in_window += 1
        lg = fx["league"]
        label = f"{lg['name']} ({lg.get('country')})"
        all_leagues[label] += 1
        if af_wanted(fx):
            wanted.append((kick, fx))
        else:
            skipped[label] += 1

    wanted.sort(key=lambda x: (0 if af_nat(x[1]) else 1, x[0]))
    candidates = wanted[:AF_PRED]

    scored = []
    nodata = 0
    for kick, fx in candidates:
        pred = af_get("/predictions", {"fixture": fx["fixture"]["id"]})
        if pred and pred.get("response"):
            p = pred["response"][0]
            scored.append((af_conf(p), kick, fx, p, af_nat(fx)))
        else:
            nodata += 1

    selected = pick(scored, AF_MAX)
    nat_count = sum(1 for x in selected if x[-1])

    diag.append(
        f"API-Football: pencerede {in_window} maç, uygun {len(wanted)}, "
        f"incelenen {len(candidates)}, verisi olan {len(scored)}, verisi olmayan {nodata}, "
        f"seçilen {len(selected)} (milli {nat_count})"
    )
    if all_leagues:
        top_all = ", ".join(f"{k} x{v}" for k, v in all_leagues.most_common(10))
        diag.append("API-Football penceredeki ligler: " + top_all)
    if skipped:
        top = ", ".join(f"{k} x{v}" for k, v in skipped.most_common(6))
        diag.append("API-Football elenen ligler: " + top)
    if not selected:
        return None, 0

    lines = []
    for conf, kick, fx, p, nat in selected:
        lg = fx["league"]
        lines.append(f"MAÇ: {fx['teams']['home']['name']} - {fx['teams']['away']['name']}")
        lines.append(f"TÜR: {'🌍 MİLLİ TAKIM MAÇI' if nat else 'Kulüp maçı'}")
        lines.append(
            f"Lig: {lg['name']} ({lg.get('country')}) | Tarih/Saat (TR): {kick.strftime('%d.%m %H:%M')}"
        )
        lines.extend(af_fmt_prediction(p))
        inj = af_get("/injuries", {"fixture": fx["fixture"]["id"]})
        if inj and inj.get("response"):
            lines.append("Eksikler: " + "; ".join(af_fmt_injuries(inj["response"])))
        lines.append("")
    return "\n".join(lines), len(selected)


# =============== football-data.org (yedek) ===============
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
        # =============== Gemini ===============
def build_prompt(data_text: str) -> str:
    now, end = get_window()
    return f"""Bugünün tarihi: {now.strftime('%d.%m.%Y')}. Kapsanan zaman aralığı (Türkiye saati):
{now.strftime('%d.%m %H:%M')} - {end.strftime('%d.%m %H:%M')}.
Aşağıda futbol API'lerinden alınmış GERÇEK maç verileri var (büyük ligler, kupalar ve milli takım maçları).
Her maçta "TÜR" satırı var: "🌍 MİLLİ TAKIM MAÇI" veya "Kulüp maçı".
Veri bir veya iki KAYNAKTAN gelebilir. Aynı maç iki kaynakta da varsa onu TEK maç olarak yaz ve iki kaynağın
verisini birleştirerek kullan (takım adları farklı yazılmış olabilir).
"Son 5 maç (ham veri)" JSON halindedir: oradan takımların son sonuçlarını, attığı/yediği golleri kendin çıkar
ve ortalamaları hesapla. Sağlayıcı olasılıkları ev/beraberlik/deplasman içindir.

SADECE bu verilere dayanarak analiz yap. Veride olmayan bilgiyi (oran, kadro) uydurma.
Bir maç için veri eksikse (son 5 maç yok, tahmin yok, sakatlık verisi yok) bunu o maçın risk notunda belirt ve güveni düşük tut.

Görevin:
1. MİLLİ TAKIM MAÇLARINI ASLA ATLAMA. "🌍 MİLLİ TAKIM MAÇI" yazan HER maçı analiz et ve "🌍 MİLLİ MAÇLAR"
   başlığı altında yaz. Güven düşükse veya veri azsa bunu risk notunda belirt, ama maç listede olsun.
   Ardından "🏟️ KULÜP MAÇLARI" başlığı altında kulüp maçlarından güvenilir gördüğün HER maçı yaz
   (Afrika ve diğer kıtaların kulüp maçları dahil).
   Kulüp maçlarında en az 2-3 güvenilir maç ara, daha fazlası güvenilirse hepsini ekle, sayı sınırı yok.
   Kulüp maçlarında gerçekten güvenilir olan yoksa zorlama ve bunu dürüstçe söyle.
   Elenen kulüp maçları için tek satırda neden elendiğini yaz. Her başlıkta maçları saat sırasıyla yaz.

2. Yazdığın HER maç için şu şablonu doldur (kısa ve net tut, çok maç olabilir):

⚽ Maç adı | Lig (ülke) | Tarih Saat (TR)
🔢 Skor beklentisi: en olası X-Y (%..), alternatifler X-Y (%..), X-Y (%..) | beklenen toplam gol ~..
📊 MS1 %.. MS0 %.. MS2 %.. | Çifte şans 1-0 %.. 0-2 %.. 1-2 %..
📊 Alt/Üst 1.5: Alt %.. Üst %.. | 2.5: Alt %.. Üst %.. | 3.5: Alt %.. Üst %.. | KG Var %.. KG Yok %..
🧠 Neden: hangi veri neyi destekliyor (son 5 maç gol ortalamaları, form, sağlayıcı olasılıkları, h2h, eksikler) 2-3 madde.
🎯 EN MANTIKLI BAHİS: ... (güven %..) | neden bu: MS, KG, Alt/Üst ve Çifte şansı kıyasla.
   Örnek: "MS1 %48 ama KG Var %68, bu yüzden KG Var daha mantıklı." En fazla bir alternatif bahis ekle.
⚠️ Risk: tek cümle.

3. Başa kısa bir "📅 Günün Özeti" ekle (kaç milli, kaç kulüp maçı yazdığını belirt).

4. En sona "🎟️ KUPONLAR" bölümü ekle. Yukarıda yazdığın maçlardan, her maçtan EN FAZLA BİR seçim alarak:
   - 🟢 Sağlam Kupon: en yüksek güvenli 2-3 seçim.
   - 🟡 Dengeli Kupon: en güvenli 4-5 seçim (sağlam kupondakileri içerebilir).
   Her kupon için: maç, seçim, güven %, ve tahmini birleşik ihtimal (seçimlerin yüzdelerinin çarpımı, hesaplayıp yaz).
   Oran verisi olmadığı için toplam oranı hesaplama; kullanıcı oranları kendisi bahis sitesinden çarpabilir, bunu yaz.
   Seçim sayısı arttıkça birleşik ihtimalin hızla düştüğünü bir cümleyle belirt.
   Uygun seçim azsa kuponu küçük tut veya "uygun seçim yok" yaz.

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


# =============== Telegram ===============
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
        sections = []
        t1, _ = build_hl_data()
        if t1:
            sections.append("=== KAYNAK 1: Highlightly ===\n" + t1)
        t2, _ = build_af_data()
        if t2:
            sections.append("=== KAYNAK 2: API-Football ===\n" + t2)
        if not sections:
            t3, _ = build_fd_data()
            if t3:
                sections.append("=== KAYNAK: football-data.org ===\n" + t3)
        if not sections:
            note = "\n".join(diag) if diag else "Kaynaklardan veri alınamadı."
            send_telegram(
                f"{title}\n\nBu aralıkta analiz edilecek maç bulunamadı. 😴\n\n🔧 Kaynak durumu:\n{note}"
            )
            return
        report = ask_gemini(build_prompt("\n\n".join(sections)))
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
