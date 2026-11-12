import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

TR = ZoneInfo("Europe/Istanbul")


def build_prompt() -> str:
    today = datetime.now(TR).strftime("%d.%m.%Y")
    return f"""Bugünün tarihi: {today} (Türkiye saati).
Google aramasını kullanarak BUGÜN oynanacak futbol maçlarını bul (Süper Lig, Premier Lig, La Liga,
Serie A, Bundesliga, Ligue 1, Şampiyonlar Ligi, Avrupa Ligi, Konferans Ligi vb.).

Görevin:
1. Güncel form, sakat/cezalı oyuncular, puan durumu, iç saha/deplasman performansı, son karşılaşmalar
   ve oran/istatistik bilgilerine bakarak EN YÜKSEK olasılıklı maçları seç.
2. En az 2-3 maç seç. Bundan fazla gerçekten güvenilir maç varsa hepsini ekle (en fazla 6).
   Güvenilir maç yoksa zorlama; bunu dürüstçe belirt.
3. Her maç için şunları yaz:
   - ⚽ Maç, lig, Türkiye saati
   - 🎯 Tahmin (MS1 / MS0 / MS2 / Alt-Üst / KG Var-Yok / Çifte şans)
   - 📊 Güven yüzdesi (tahmini)
   - 📝 2-3 cümlelik kısa analiz (form, eksikler, h2h)
   - ⚠️ Tek cümlelik risk notu
4. Bülten başına kısa bir "📅 Günün Özeti" ekle, sonda "Garanti yoktur, bahis risklidir" uyarısı olsun.

Kurallar: Türkçe yaz, bol emoji kullan, Markdown işareti (*, _, #) KULLANMA, düz metin ve emoji yaz.
Sadece arama sonuçlarında doğrulayabildiğin bilgileri kullan; uydurma."""


def ask_gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.3},
    }
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(
                url, params={"key": GEMINI_API_KEY}, json=body, timeout=180
            )
            r.raise_for_status()
            data = r.json()
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                return text
            last_err = "Boş yanıt"
        except Exception as e:  # noqa: BLE001
            last_err = e
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"Gemini yanıt vermedi: {last_err}")


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
        r.raise_for_status()
        time.sleep(1)


def main():
    today = datetime.now(TR).strftime("%d.%m.%Y")
    try:
        report = ask_gemini(build_prompt())
        send_telegram(f"⚽🔥 GÜNLÜK FUTBOL BÜLTENİ — {today}\n\n{report}")
    except Exception as e:  # noqa: BLE001
        send_telegram(f"❌ Futbol botu hata verdi: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
    
