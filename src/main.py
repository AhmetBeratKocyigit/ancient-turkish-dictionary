import os
import json
import time
from pathlib import Path
from typing import List
from dotenv import load_dotenv

import fitz  # PyMuPDF
from PIL import Image
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

load_dotenv()

# src/main.py dosyasının bulunduğu yer üzerinden ana proje dizinini (root) bulur:
BASE_DIR = Path(__file__).resolve().parent.parent

# Klasör yolları
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "output"

# Output klasörü yoksa otomatik oluşturur
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)   

# ============================================================
# AYARLAR
# ============================================================



PDF_YOLU = DATA_DIR / "dlt.pdf"

# PDF'deki gerçek sayfa indeksleri:
# 10 -> 10. indeks, yani PDF'in 11. sayfası
# normalde 36 - 286
BASLANGIC_SAYFASI = 69
BITIS_SAYFASI = 286       # 15 dahil DEĞİL


CIKTI_DOSYASI = OUTPUT_DIR / "tam_sozluk.json"
HATA_DOSYASI = OUTPUT_DIR / "hatalar.json"

# API anahtarını ortam değişkeninden al.
# Windows:
#   set GEMINI_API_KEY=...
#
# PowerShell:
#   $env:GEMINI_API_KEY="..."
API_KEY = os.getenv("GEMINI_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY ortam değişkeni bulunamadı."
    )


# Güncel Gemini SDK
client = genai.Client(api_key=API_KEY)

# Görsel okuma + metin çıkarma için hızlı model.
MODEL = "gemini-2.5-pro"


# ============================================================
# JSON ŞEMASI
# ============================================================

class Kelime(BaseModel):
    kelime: str = Field(
        description=(
            "Sayfada Latin/Türkçe harflerle yazılmış "
            "madde başlığı."
        )
    )

    anlam: str = Field(
        description=(
            "Kelimenin sayfada verilen Türkçe anlamı. "
            "Yalnızca sayfadaki bilgiden çıkarılmalı."
        )
    )

    ornek_metin: str = Field(
        description=(
            "Varsa sav, örnek cümle veya örnek kullanım. "
            "Yoksa boş string."
        )
    )

    kelime_turu: str = Field(
        description=(
            "Kelimenin türü. Sayfada açıkça belirtiliyorsa "
            "veya güvenilir biçimde belirlenebiliyorsa "
            "isim, fiil, sıfat, zarf, zamir, edat, bağlaç, "
            "ünlem vb. olarak yaz. Emin olunamıyorsa "
            "'belirsiz' yaz. Modern Türkçe kategorisini "
            "zorla uygulama."
        )
    )

    dil_veya_lehce: str = Field(
        description=(
            "Sayfada kelimenin ait olduğu dil veya lehçe "
            "belirtiliyorsa yaz: Oğuzca, Kıpçakça vb. "
            "Belirtilmiyorsa boş string."
        )
    )

    kaynak_cilt: int = Field(
        description="Eserin cilt numarası. Bu veri setinde 1."
    )

    kitap_sayfa: int = Field(
        description=(
            "Kitabın basılı sayfa numarası. "
            "Görseldeki sayfa numarasından alınmalı."
        )
    )

    pdf_sayfa: int = Field(
        description="PDF içerisindeki sayfa numarası."
    )

    notlar: str = Field(
        description=(
            "Maddeyle ilgili önemli ek açıklamalar, "
            "dipnot bilgileri veya kaynakta açıkça verilen "
            "diğer bilgiler. Yoksa boş string."
        )
    )

    kontrol_gerekli: bool = Field(
        description=(
            "Kelime, anlam, örnek metin veya başka bir alan "
            "görsel nedeniyle okunamıyorsa true; aksi halde false."
        )
    )


class SayfaCiktisi(BaseModel):
    kelimeler: List[Kelime]


# ============================================================
# PROMPT
# ============================================================

PROMPT = r"""
Bu görsel, Divânü Lügati't-Türk'ün basılı bir sayfasıdır.

Görevin sayfadaki sözlük maddelerini dikkatli biçimde çıkarmaktır.

ÇOK ÖNEMLİ:

1. Sayfadaki ARAP HARFLİ madde başlıklarını JSON'a koyma.
2. Bunun yerine aynı maddenin açıklama bölümünde Latin/Türkçe
   harflerle yazılmış olan kelimeyi "kelime" alanına koy.
3. Dipnotları sözlük maddesi olarak kabul etme.
4. Sayfa numarasını sözlük maddesi olarak kabul etme.
5. Metinde geçen Brocke[l]mann gibi kaynak/kişi adlarını kelime
   olarak alma.
6. Bir madde birden fazla satıra bölünmüş olabilir.
   Satırları birleştir.
7. İki sütunlu sayfayı ayrı ayrı ve yukarıdan aşağıya incele.
8. Sol sütunun tamamını bitirdikten sonra sağ sütuna geç.
9. Görselde gerçekten bulunmayan bir kelime, anlam veya örnek
   cümle UYDURMA.
10. Okunamayan bir kelimeyi tahmin ederek başka bir kelimeye
    dönüştürme.
11. Türkçe karakterleri koru:
    ç, Ç, ğ, Ğ, ı, İ, ö, Ö, ş, Ş, ü, Ü
12. Anlam alanına mümkün olduğunca açıklamanın tamamını,
    fakat Arap harfli karşılıkları dahil etmeden aktar.
13. Varsa örnek sav/cümleyi "ornek_metin" alanına koy.
14. Örnek yoksa "" kullan.
15. Aynı kelime sayfada iki farklı maddede geçiyorsa iki ayrı
    kayıt oluştur.
16. Sadece bu görselde bulunan bilgiyi kullan.
17. Her madde için kelime_turu alanını doldur.
18. Kelime türünü yalnızca kaynakta açıkça belirtilmişse
    veya güvenilir biçimde belirlenebiliyorsa yaz.
19. Emin değilsen "belirsiz" kullan.
20. Kaynakta Oğuzca, Kıpçakça vb. bir dil/lehçe bilgisi
    varsa bunu dil_veya_lehce alanına aktar.
21. Kaynakta dil/lehçe bilgisi yoksa boş string kullan.
22. Kitabın basılı sayfa numarasını kitap_sayfa alanına yaz.
23. PDF sayfa numarasını pdf_sayfa alanına yaz.
24. Kaynakta açıkça bulunan önemli ek bilgileri notlar
    alanına aktar.
25. Bir bilgi okunamıyorsa tahmin etme ve kontrol_gerekli
    alanını true yap.
26. Model kendi bilgisinden hiçbir bilgi eklemesin.

Özellikle şu ayrımı yap:

ARAP HARFLİ:
    [buradaki Osmanlı/Arap yazısı]
    -> BUNU ALMA.

LATİN/TÜRKÇE:
    "barış", "başık", "buşug" vb.
    -> MADDE BAŞLIĞI OLARAK BUNU AL.

Çıktıyı yalnızca verilen JSON şemasına uygun oluştur.
"""


# ============================================================
# SAYFAYI GÖRSELE ÇEVİR
# ============================================================

def sayfayi_resme_cevir(pdf, sayfa_no: int) -> Image.Image:
    """
    PDF sayfasını yüksek kaliteli PNG olarak belleğe alır.
    """

    sayfa = pdf.load_page(sayfa_no)

    # 200 DPI:
    # 150 DPI'dan daha iyi OCR/görsel okuma.
    # Çok büyük PDF'lerde 180 de kullanılabilir.
    pix = sayfa.get_pixmap(
        dpi=200,
        colorspace=fitz.csRGB,
        alpha=False
    )

    img = Image.frombytes(
        "RGB",
        [pix.width, pix.height],
        pix.samples
    )

    return img


# ============================================================
# GEMINI'DEN JSON AL
# ============================================================

def resimden_veri_cikar(
    image: Image.Image,
    pdf_sayfa_no: int,
    max_deneme: int = 3
) -> List[dict]:

    prompt = (
        PROMPT
        + f"\n\nBu görsel PDF'in {pdf_sayfa_no + 1}. sayfasıdır."
        + "\nKaynak sayfa alanında bu sayfa numarasını kullan."
    )

    for deneme in range(1, max_deneme + 1):

        try:

            response = client.models.generate_content(
                model=MODEL,

                contents=[
                    prompt,
                    image
                ],

                config=types.GenerateContentConfig(
                    temperature=0,
                    response_mime_type="application/json",
                    response_schema=SayfaCiktisi,
                )
            )

            # Pydantic doğrulaması.
            sonuc = SayfaCiktisi.model_validate_json(
                response.text
            )

            return [
                kelime.model_dump()
                for kelime in sonuc.kelimeler
            ]

        except Exception as e:
            err_msg = str(e)
            print(f"    ⚠️ Deneme {deneme}/{max_deneme} başarısız: {err_msg}")

            if deneme < max_deneme:
                # Eğer hız limitine (Too Many Requests / 429) takıldıysa tam 60sn bekle ki sayaç sıfırlansın
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    bekleme = 60
                    print(f"    ⏳ Hız limitine takılındı (429). {bekleme} saniye soğuma bekleniyor...")
                else:
                    bekleme = 10 * (2 ** (deneme - 1))
                    print(f"    → {bekleme} saniye bekleniyor...")
                
                time.sleep(bekleme)

    raise RuntimeError(
        f"Sayfa {pdf_sayfa_no + 1} işlenemedi."
    )


# ============================================================
# KAYIT YÜKLEME
# ============================================================

def mevcut_veriyi_yukle() -> List[dict]:

    if not os.path.exists(CIKTI_DOSYASI):
        return []

    try:
        with open(
            CIKTI_DOSYASI,
            "r",
            encoding="utf-8"
        ) as f:

            veri = json.load(f)

        if isinstance(veri, list):
            return veri

    except Exception as e:
        print(f"⚠️ Mevcut JSON okunamadı: {e}")

    return []


# ============================================================
# ANA İŞLEM
# ============================================================

def ana_islem():

    tum_sozluk = mevcut_veriyi_yukle()

    islenen_sayfalar = {
        item["pdf_sayfa"]
        for item in tum_sozluk
        if "pdf_sayfa" in item
    }

    hatalar = []

    print("=" * 60)
    print("DIVÂNÜ LÜGATİ'T-TÜRK VERİ ÇIKARMA")
    print("=" * 60)

    with fitz.open(PDF_YOLU) as pdf:

        toplam = BITIS_SAYFASI - BASLANGIC_SAYFASI

        for sira, sayfa_no in enumerate(
            range(BASLANGIC_SAYFASI, BITIS_SAYFASI),
            start=1
        ):

            pdf_sayfa_numarasi = sayfa_no + 1

            print()
            print(
                f"[{sira}/{toplam}] "
                f"PDF sayfası {pdf_sayfa_numarasi}"
            )

            # Daha önce işlendi mi?
            if pdf_sayfa_numarasi in islenen_sayfalar:

                print("    ↳ Daha önce işlenmiş, atlanıyor.")
                continue

            try:

                # ------------------------------------------------
                # PDF → IMAGE
                # ------------------------------------------------

                print("    → Sayfa görüntüsü hazırlanıyor...")

                image = sayfayi_resme_cevir(
                    pdf,
                    sayfa_no
                )

                print(
                    f"    → Görsel: "
                    f"{image.width}x{image.height}"
                )

                # ------------------------------------------------
                # IMAGE → JSON
                # ------------------------------------------------

                print("    → Gemini analiz ediyor...")

                sayfa_verisi = resimden_veri_cikar(
                    image,
                    sayfa_no
                )

                # ------------------------------------------------
                # SONUÇ
                # ------------------------------------------------

                print(
                    f"    ✓ {len(sayfa_verisi)} madde bulundu."
                )

                tum_sozluk.extend(sayfa_verisi)

                islenen_sayfalar.add(
                    pdf_sayfa_numarasi
                )

                # ------------------------------------------------
                # HER SAYFADAN SONRA KAYDET
                # ------------------------------------------------
                #
                # Program ortada kapanırsa tüm veriyi kaybetmezsin.
                #

                with open(
                    CIKTI_DOSYASI,
                    "w",
                    encoding="utf-8"
                ) as f:

                    json.dump(
                        tum_sozluk,
                        f,
                        ensure_ascii=False,
                        indent=2
                    )

                print(
                    f"    ✓ Kaydedildi: {CIKTI_DOSYASI}"
                )

                # API'yi gereksiz yere bombardıman etme.
                time.sleep(5)

            except Exception as e:

                print(
                    f"    ❌ SAYFA HATASI: {e}"
                )

                hatalar.append({
                    "pdf_sayfa": pdf_sayfa_numarasi,
                    "hata": str(e)
                })

                with open(HATA_DOSYASI, "w", encoding="utf-8") as f:
                    json.dump(hatalar, f, ensure_ascii=False, indent=2)

                # Hata olsa da sonraki sayfaya devam et.
                continue

    # ========================================================
    # HATALARI KAYDET
    # ========================================================

    with open(
        HATA_DOSYASI,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            hatalar,
            f,
            ensure_ascii=False,
            indent=2
        )

    # ========================================================
    # İSTATİSTİK
    # ========================================================

    print()
    print("=" * 60)
    print("İŞLEM TAMAMLANDI")
    print("=" * 60)

    print(
        f"Toplam sözlük maddesi: {len(tum_sozluk)}"
    )

    print(
        f"Hatalı sayfa: {len(hatalar)}"
    )

    print(
        f"Çıktı: {CIKTI_DOSYASI}"
    )

    print(
        f"Hatalar: {HATA_DOSYASI}"
    )


# ============================================================
# PROGRAM
# ============================================================

if __name__ == "__main__":
    ana_islem()
