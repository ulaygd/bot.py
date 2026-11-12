import os
import datetime
import requests
from google import genai
from google.genai import types

# ================= AYARLAR =================
# Secrets'tan çekmeyi dener, bulamazsa doğrudan buradaki yedeği kullanır
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = "9043761331"
# ===========================================

def gunluk_analiz_olustur():
    client = genai.Client(api_key=GEMINI_API_KEY)
    bugun = datetime.date.today().strftime("%d.%m.%Y")

    prompt = f"""
    Bugünün tarihi: {bugun}.
    Görevin:
    1. Google Search aracını kullanarak bugünün gerçek futbol bültenini, maçlarını ve oran eğilimlerini güvenilir spor/istatistik kaynaklarından tara.
    2. Bugün oynanacak maçlar arasından riski en az olan, istatistiksel olarak tutma olasılığı en yüksek OLAN TÜM MAÇLARI seç (sayı kısıtı yok).
    3. Seçtiğin her maç için net bir tahmin (Örn: MS 1, 2.5 Üst, KG Var vb.) ve mantıklı bir oran belirle.
    4. Her maçın altına bu tahmini neden seçtiğini kısaca açıkla.

    Çıktı formatı Telegram'da okunması kolay, temiz, emojili bir bülten olsun. Asla hayali maç üretme.
    """

    print("🔍 Google Search ile bülten taranıyor...")

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            temperature=0.2
        )
    )

    return response.text

def telegrama_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mesaj,
        "parse_mode": "Markdown"
    }
    
    print(f"DEBUG: Hedef Chat ID -> {TELEGRAM_CHAT_ID}")
    print(f"DEBUG: Token Kontrolü -> {'Mevcut' if TELEGRAM_BOT_TOKEN else 'BOŞ!'}")
    
    cevap = requests.post(url, json=payload)
    print(f"DEBUG: Telegram Durum Kodu -> {cevap.status_code}")
    print(f"DEBUG: Telegram Yanıtı -> {cevap.text}")
    
    if cevap.status_code == 200:
        print("✅ Günün analiz bülteni başarıyla Telegram'a gönderildi!")
    else:
        print(f"❌ Telegram hatası: {cevap.text}")

if __name__ == "__main__":
    try:
        bulten = gunluk_analiz_olustur()
        print("\n--- OLUŞTURULAN GÜNLÜK ANALİZ ---\n")
        print(bulten)
        print("\n----------------------------------\n")
        telegrama_gonder(bulten)
    except Exception as e:
        print(f"Bir hata oluştu: {e}")
        
