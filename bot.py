import datetime
import requests
from google import genai
from google.genai import types

# ================= AYARLAR =================
GEMINI_API_KEY = "AQ.Ab8RN6LKLHPhrg8W_SMlL-00xXiRYRf2wSEj6pFeMEeXZhe9ng"
TELEGRAM_BOT_TOKEN = "9043761331:AAFrN4iW6c8Ze8igDDm-p-jL3uR-tRUteE"
TELEGRAM_CHAT_ID = "9043761331"
# ===========================================

def gunluk_analiz_olustur():
    client = genai.Client(api_key=GEMINI_API_KEY)
    bugun = datetime.date.today().strftime("%d.%m.%Y")

    prompt = f"""
    Bugünün tarihi: {bugun}.
    Görevin:
    1. Google Search aracını kullanarak bugünün gerçek futbol bültenini, maçlarını ve oran eğilimlerini güvenilir spor/istatistik kaynaklarından tara.
    2. Bugün oynanacak maçlar arasından riski en az olan, istatistiksel olarak tutma olasılığı en yüksek OLAN TÜM MAÇLARI seç (sayı kısıtı yok, ne kadar sağlam maç varsa hepsini dahil et).
    3. Seçtiğin her maç için net bir tahmin (Örn: MS 1, 2.5 Üst, KG Var, Çifte Şans vb.) ve mantıklı bir oran aralığı belirle.
    4. Her maçın altına bu tahmini neden seçtiğini (takımların son durumları, iç/dış saha performansları, eksikler vb.) net ve kısa cümlelerle açıkla.

    Çıktı formatı Telegram'da okunması son derece kolay, temiz, emojili ve profesyonel bir bülten formatında olsun. Asla hayali maç üretme.
    """

    print("🔍 Güvenilir kaynaklar taranıyor, risk analizi yapılıyor...")

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
    
    cevap = requests.post(url, json=payload)
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
      
