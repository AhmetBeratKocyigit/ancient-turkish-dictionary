"""
Divânu Lügati't-Türk
PDF → Metin → Gemini → Yapılandırılmış JSON

Kurulum:
    pip install pymupdf pydantic python-dotenv google-genai

.env:
    GEMINI_API_KEY=BURAYA_API_KEY

Klasör yapısı:
    proje/
    ├── data/
    │   └── dlt.pdf
    ├── output/
    └── main.py
"""

import os
import re
import json
import time
import hashlib
from pathlib import Path
from typing import List

import fitz  # PyMuPDF
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from google import genai
from google.genai import types


# ============================================================
# 1. AYARLAR
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

PDF_PATH = BASE_DIR / "data" / "dlt.pdf"

OUTPUT_DIR = BASE_DIR / "output"

RAW_TEXT_DIR = OUTPUT_DIR / "1_raw_text"
CHUNKS_DIR = OUTPUT_DIR / "2_chunks"
JSON_DIR = OUTPUT_DIR / "3_json"

PROGRESS_FILE = OUTPUT_DIR / "islenen_chunklar.json"
ERROR_FILE = OUTPUT_DIR / "hatalar.json"
MASTER_JSON_FILE = OUTPUT_DIR / "tam_sozluk.json"


# ------------------------------------------------------------
# İşlenecek sayfa aralığı
# ------------------------------------------------------------
# PDF sayfa numarası 1'den başlar.
#
# Örnek:
# 195 → 210
#
# Tüm PDF için:
# BASLANGIC_SAYFASI = 1
# BITIS_SAYFASI = None
# ------------------------------------------------------------

BASLANGIC_SAYFASI = 129
BITIS_SAYFASI = None


# ------------------------------------------------------------
# Chunk ayarları
# ------------------------------------------------------------
# API free-tier ve token kısıtlarını aşmamak için chunk boyutunu
# daha küçük tutuyoruz; akış aynen korunur.

CHUNK_SAYFA_SAYISI = 5
CHUNK_OVERLAP_SAYFASI = 1


# ------------------------------------------------------------
# Gemini
# ------------------------------------------------------------
# Google API model adları zamanla değiştiği için, geçerli düşük maliyetli
# adayları belirleyip otomatik fallback yapıyoruz.

MODEL = "gemini-3.5-flash-lite"
MODEL_CANDIDATES = [
    MODEL,
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",    
]

MAX_RETRY = 5

RETRY_BASE_SECONDS = 5


# ============================================================
# 2. KLASÖRLER
# ============================================================

RAW_TEXT_DIR.mkdir(parents=True, exist_ok=True)
CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
JSON_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 3. GEMINI API
# ============================================================

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY bulunamadı.\n"
        ".env dosyasına GEMINI_API_KEY=... eklemelisin."
    )

client = genai.Client(api_key=GEMINI_API_KEY)


# ============================================================
# 4. VERİ ŞEMASI
# ============================================================
class Kelime(BaseModel):
    """
    Sözlükteki tek bir kelime/lemma kaydı.
    """

    kelime: str = Field(
        ...,
        description="Sözlükte madde başı olarak verilen kelime."
    )

    anlam: str = Field(
        default="",
        description=(
            "Kaynakta verilen kelime anlamı. Eğer kelimenin açık bir Türkçe tanımı verilmemiş ise, "
            "doğrudan verilen örnek cümlenin/savın Türkçe çevirisinden veya bağlamından genel anlamı sen türet ve anlam alanına yaz."
        )
    )

    ornek_metin: str = Field(
        default="",
        description="Kaynakta kelime için verilen örnek, cümle veya kullanım ve günümüz Türkçe çevirisi. Yoksa boş string."
    )

    cekimler: List[str] = Field(
        default_factory=list,
        description=(
            "Maddenin altında verilen mastar, geniş zaman veya diğer gramer çekimleri "
            "(Örn: ['çıqârur', 'çıqarmâq'], ['alqaşür', 'alqaşmâq']). "
            "Bu formları ornek_metin veya notlar alanına koyma, sadece bu listeye ekle. Yoksa boş liste."
        )
    )

    kelime_turu: str = Field(
        default="belirsiz",
        description=(
            "Kelimenin türü. Sayfada açıkça belirtiliyorsa veya güvenilir biçimde belirlenebiliyorsa "
            "isim, fiil, sıfat, zarf, zamir, edat, bağlaç, ünlem vb. olarak yaz. Emin olunamıyorsa "
            "'belirsiz' yaz. Modern Türkçe kategorisini zorla uygulama."
        )
    )

    dil_veya_lehce: str = Field(
        default="",
        description="Kaynakta belirtilen dil veya lehçe bilgisi. Açıkça belirtilmiyorsa boş string."
    )

    kaynak_sayfa: str = Field(
        ...,
        description=(
            "Madde başının geçtiği kaynak sayfa numarası. Tek sayfa ise '129', "
            "iki sayfaya yayılıyorsa '129-130' şeklinde yazılmalıdır. "
            "Kaynak metindeki [KAYNAK_SAYFA: X] işaretlerinden alınmalıdır."
        )
    )

    notlar: str = Field(
        default="",
        description="Maddeyle ilgili önemli ek açıklamalar, dipnot bilgileri, belirsizlikler veya kaynakta açıkça görülen özel durumlar. Yoksa boş string."
    )

    kontrol_gerekli: bool = Field(
        default=False,
        description="Kayıt OCR/metin çıkarımı, anlam veya madde sınırı açısından şüpheliyse true."
    )


class ChunkCiktisi(BaseModel):
    """
    Gemini'nin bir chunk için döndüreceği çıktı.
    """

    kelimeler: List[Kelime]


# ============================================================
# 5. YARDIMCI FONKSİYONLAR
# ============================================================

def json_kaydet(dosya: Path, veri):
    """
    UTF-8 ve Türkçe karakterleri bozmadan JSON kaydeder.
    """
    with open(dosya, "w", encoding="utf-8") as f:
        json.dump(
            veri,
            f,
            ensure_ascii=False,
            indent=2
        )


def json_oku(dosya: Path, varsayilan):
    """
    JSON dosyası varsa okur.
    """
    if not dosya.exists():
        return varsayilan

    try:
        with open(dosya, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return varsayilan


def normalize_metin(metin: str) -> str:
    """
    Metni Gemini'ye göndermeden önce temel temizlikten geçirir.
    """

    # Fazla boşlukları azalt
    metin = re.sub(r"[ \t]+", " ", metin)

    # 3+ boş satırı 2 satıra indir
    metin = re.sub(r"\n{3,}", "\n\n", metin)

    return metin.strip()


def kelime_hash(kelime: dict) -> str:

    temel = "|".join([
        str(kelime.get("kelime", "")).strip(),
        str(kelime.get("anlam", "")).strip(),
        str(kelime.get("kaynak_sayfa", "")).strip(),
    ])

    return hashlib.sha256(
        temel.encode("utf-8")
    ).hexdigest()


# ============================================================
# 6. PDF METİN ÇIKARMA
# ============================================================

def sayfa_metnini_cikar(page, sayfa_numarasi: int) -> str:
    """
    PyMuPDF kullanarak sayfadaki metni çıkarır.

    DLT PDF'sinin iki sütunlu olması ihtimaline karşı
    metin bloklarının x/y koordinatlarını kullanır.

    Dönen metin:

    [KAYNAK_SAYFA: 203]

    ...
    """

    page_width = page.rect.width

    blocks = page.get_text(
        "blocks",
        sort=False
    )

    metin_bloklari = []

    for block in blocks:
        if len(block) < 7:
            continue

        x0, y0, x1, y1, text, block_no, block_type = block[:7]

        # Sadece metin bloklarını al
        if block_type != 0:
            continue

        text = text.strip()

        if not text:
            continue

        metin_bloklari.append({
            "x0": x0,
            "y0": y0,
            "x1": x1,
            "y1": y1,
            "text": text,
            "width": x1 - x0,
        })

    if not metin_bloklari:
        return f"[KAYNAK_SAYFA: {sayfa_numarasi}]\n"

    # --------------------------------------------------------
    # İki sütun kontrolü
    # --------------------------------------------------------

    midpoint = page_width / 2

    sol = []
    sag = []
    tam_genislik = []

    for block in metin_bloklari:

        x0 = block["x0"]
        x1 = block["x1"]
        width = block["width"]

        # Sayfanın büyük kısmını kaplayan bloklar
        # başlık vb. olabilir.
        if width >= page_width * 0.65:
            tam_genislik.append(block)

        else:
            merkez_x = (x0 + x1) / 2

            if merkez_x < midpoint:
                sol.append(block)
            else:
                sag.append(block)

    # --------------------------------------------------------
    # Gerçekten iki sütun varsa:
    # önce üstteki tam genişlik blokları,
    # sonra sol sütun,
    # sonra sağ sütun.
    # --------------------------------------------------------

    iki_sutun_var = (
        len(sol) >= 2 and
        len(sag) >= 2
    )

    if iki_sutun_var:

        tam_genislik.sort(
            key=lambda b: (b["y0"], b["x0"])
        )

        sol.sort(
            key=lambda b: (b["y0"], b["x0"])
        )

        sag.sort(
            key=lambda b: (b["y0"], b["x0"])
        )

        sirali_bloklar = (
            tam_genislik +
            sol +
            sag
        )

    else:

        # Tek sütun / normal düzen
        sirali_bloklar = sorted(
            metin_bloklari,
            key=lambda b: (b["y0"], b["x0"])
        )

    parcalar = []

    for block in sirali_bloklar:
        parcalar.append(block["text"])

    metin = "\n".join(parcalar)

    metin = normalize_metin(metin)

    return (
        f"[KAYNAK_SAYFA: {sayfa_numarasi}]\n"
        f"{metin}\n"
    )


# ============================================================
# 7. PDF → RAW TEXT
# ============================================================

def raw_metni_olustur():
    """
    PDF'nin seçilen sayfalarını çıkarır.

    Her sayfa ayrı .md dosyası olarak kaydedilir.
    """

    if not PDF_PATH.exists():
        raise FileNotFoundError(
            f"PDF bulunamadı: {PDF_PATH}"
        )

    print("\n" + "=" * 60)
    print("PDF METİN ÇIKARMA")
    print("=" * 60)

    doc = fitz.open(PDF_PATH)

    toplam_sayfa = len(doc)

    baslangic = BASLANGIC_SAYFASI

    if BITIS_SAYFASI is None:
        bitis = toplam_sayfa
    else:
        bitis = min(
            BITIS_SAYFASI,
            toplam_sayfa
        )

    if baslangic < 1:
        baslangic = 1

    if baslangic > toplam_sayfa:
        doc.close()

        raise ValueError(
            f"Başlangıç sayfası PDF'den büyük: {baslangic}"
        )

    print(f"PDF toplam sayfa: {toplam_sayfa}")
    print(f"İşlenecek aralık: {baslangic} - {bitis}")

    tum_sayfalar = {}

    for sayfa_numarasi in range(
        baslangic,
        bitis + 1
    ):

        page = doc[
            sayfa_numarasi - 1
        ]

        metin = sayfa_metnini_cikar(
            page,
            sayfa_numarasi
        )

        dosya = (
            RAW_TEXT_DIR /
            f"sayfa_{sayfa_numarasi:04d}.md"
        )

        with open(
            dosya,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(metin)

        tum_sayfalar[
            sayfa_numarasi
        ] = metin

        print(
            f"[{sayfa_numarasi}/{bitis}] "
            f"{len(metin):,} karakter"
        )

    doc.close()

    print("\nMetin çıkarma tamamlandı.")

    return tum_sayfalar


# ============================================================
# 8. TEXT LAYER TESTİ
# ============================================================

def text_layer_test():
    """
    PDF'nin gerçekten seçilebilir / okunabilir metin içerip
    içermediğini kontrol eder.
    """

    print("\n" + "=" * 60)
    print("TEXT LAYER TESTİ")
    print("=" * 60)

    if not PDF_PATH.exists():
        raise FileNotFoundError(
            f"PDF bulunamadı: {PDF_PATH}"
        )

    doc = fitz.open(PDF_PATH)

    test_sayfasi = BASLANGIC_SAYFASI

    if test_sayfasi > len(doc):
        test_sayfasi = 1

    page = doc[
        test_sayfasi - 1
    ]

    ham_metin = page.get_text("text")

    doc.close()

    print(
        f"Test sayfası: {test_sayfasi}"
    )

    print(
        f"Çıkarılan karakter: "
        f"{len(ham_metin):,}"
    )

    print("\n--- İLK 3000 KARAKTER ---\n")

    print(
        ham_metin[:3000]
    )

    print(
        "\n--- TEST SONU ---"
    )

    if len(ham_metin.strip()) < 100:
        print(
            "\nUYARI: Bu sayfadan çok az metin çıkarıldı."
        )
        print(
            "PDF taranmış görüntü olabilir veya "
            "metin katmanı sorunlu olabilir."
        )

        return False

    print(
        "\nText layer kullanılabilir görünüyor."
    )

    return True


# ============================================================
# 9. CHUNK OLUŞTURMA
# ============================================================

def chunklari_olustur(tum_sayfalar: dict):
    """
    Sayfaları Gemini'ye gönderilecek chunk'lara böler.

    Örneğin:

    195-202
    202-209
    209-210

    şeklinde overlap oluşturabilir.
    """

    print("\n" + "=" * 60)
    print("CHUNK OLUŞTURMA")
    print("=" * 60)

    sayfa_numaralari = sorted(
        tum_sayfalar.keys()
    )

    if not sayfa_numaralari:
        return []

    chunks = []

    index = 0
    chunk_no = 1

    while index < len(sayfa_numaralari):

        chunk_sayfalari = sayfa_numaralari[
            index:
            index + CHUNK_SAYFA_SAYISI
        ]

        if not chunk_sayfalari:
            break

        baslangic = chunk_sayfalari[0]
        bitis = chunk_sayfalari[-1]

        parcalar = []

        parcalar.append(
            f"===== CHUNK {chunk_no} ====="
        )

        parcalar.append(
            f"===== KAYNAK SAYFA ARALIĞI: "
            f"{baslangic}-{bitis} ====="
        )

        for sayfa_no in chunk_sayfalari:

            parcalar.append(
                tum_sayfalar[sayfa_no]
            )

        chunk_metni = "\n\n".join(
            parcalar
        )

        chunk_dosyasi = (
            CHUNKS_DIR /
            f"chunk_{chunk_no:04d}_"
            f"{baslangic}_{bitis}.md"
        )

        with open(
            chunk_dosyasi,
            "w",
            encoding="utf-8"
        ) as f:
            f.write(chunk_metni)

        chunks.append({
            "chunk_no": chunk_no,
            "sayfa_baslangic": baslangic,
            "sayfa_bitis": bitis,
            "sayfalar": chunk_sayfalari,
            "metin": chunk_metni,
            "dosya": str(chunk_dosyasi)
        })

        print(
            f"Chunk {chunk_no}: "
            f"{baslangic}-{bitis}"
        )

        # ----------------------------------------------------
        # Overlap
        # ----------------------------------------------------

        ilerleme = (
            CHUNK_SAYFA_SAYISI -
            CHUNK_OVERLAP_SAYFASI
        )

        if ilerleme <= 0:
            ilerleme = 1

        index += ilerleme
        chunk_no += 1

    print(
        f"\nToplam chunk: {len(chunks)}"
    )

    return chunks


# ============================================================
# 10. GEMINI PROMPT
# ============================================================

SYSTEM_PROMPT = """
Sen Divânu Lügati't-Türk'ün dijitalleştirilmesi için çalışan
titiz bir sözlük veri çıkarma sistemisin.

Görevin sana verilen kaynak metninden sözlük maddelerini
yapılandırılmış JSON verisine dönüştürmektir.

ÇOK ÖNEMLİ KURALLAR:

1. SADECE verilen kaynak metnini kullan.

2. Kaynakta bulunmayan bilgileri kesinlikle uydurma.

3. Kelimenin açık bir Türkçe tanımı/anlamı verilmemişse, verilen örnek 
   cümlenin/savın Türkçe çevirisinden veya bağlamından temel kavramsal karşılığını 
   türetip "anlam" alanına yaz. Ancak bunun dışında dışarıdan kaynakta olmayan 
   tarihsel/etimolojik anlamlar uydurma.

4. Kelime türü kaynakta açıkça belirtilmiyorsa veya metin yapısından 
   güvenle belirlenemiyorsa "belirsiz" yaz.

5. Dil veya lehçe bilgisi kaynakta açıkça belirtilmiyorsa (Örn: "Oğuz lehçesi", 
   "Argu lehçesi", "Tübüt lehçesi") boş string bırak. Parantez içindeki veya 
   açıklamadaki lehçe/kabile adlarını "dil_veya_lehce" alanına ayıkla.

6. Kaynakta örnek cümle, kullanım, sav veya ifade varsa "ornek_metin" alanına aktar.

7. Örnek yoksa:
   "ornek_metin": ""

8. Not gerekmiyorsa:
   "notlar": ""

9. Bir kayıt konusunda ciddi bir belirsizlik veya metinde okuma/OCR hatası varsa:
   "kontrol_gerekli": true

10. Emin olduğun kayıtlar için:
    "kontrol_gerekli": false

11. Türkçe karakterleri ve transkripsiyon işaretlerini aynen koru:
    ı, İ, ş, Ş, ğ, Ğ, ü, Ü, ö, Ö, ç, Ç, â, î, û, q

12. Kaynak metindeki anlamı yeniden yorumlama.
    Kaynağın anlamını mümkün olduğunca sadık şekilde aktar.

13. Madde başı ile açıklamayı birbirine karıştırma. Bold/kalın yazılmış (**kelime**) 
    yapıları öncelikli olarak madde başı kabul et.

14. Aynı kelime kaynakta farklı anlamlarla ayrı sözlük
    maddeleri halinde verilmişse bunları ayrı kayıtlar
    olarak çıkar.

15. Kaynakta aynı kelime farklı tür veya anlamlarla
    ayrı maddeler halinde bulunuyorsa bunları birleştirme.

16. Bir sözlük maddesi bir sayfada başlayıp sonraki
    sayfada devam ediyorsa tek kayıt oluştur.

17. Böyle bir durumda:
    kaynak_sayfa = "BAŞLANGIÇ-BİTİŞ" (Örn: "134-135") şeklinde yaz.

18. Chunk sınırında başlayan/bitmeyen bir madde varsa
    komşu sayfalardaki metni kullanarak maddenin devamını
    takip etmeye çalış.

19. Bir sonraki sayfanın metni aynı sözlük maddesinin
    devamıysa yeni bir kelime kaydı oluşturma.

20. Kaynak sayfası bilgisi metindeki:
    [KAYNAK_SAYFA: X] veya <!-- SAYFA_BASLANGIC: X -->
    işaretlerinden alınmalıdır.

21. Kaynakta bir sayfa numarası açıkça verilmişse onu değiştirme.

22. Kaynak metninde okunması zor veya bozuk bir bölüm
    varsa tahmin ederek düzeltmek yerine kontrol_gerekli
    alanını true yap.

23. Sözlükte olmayan açıklayıcı başlıkları kelime olarak çıkarma.

24. Bir kelime için kaynakta birden fazla örnek bulunuyorsa
    mümkün olduğunca tamamını aynı ornek_metin alanında koru.

25. Kaynakta bulunmayan etimoloji, günümüz Türkçesi dışı tarihsel 
    ek açıklamalar ekleme.

26. Kelimenin temel sözlük tanımını "anlam" alanında tut. 
    Kimlerin kullandığı, sosyal statü farkları, kültürel kullanım veya 
    coğrafi/tarihsel açıklamalara dair cümleleri "notlar" alanına aktar.

27. "ornek_metin" alanı içerisinde geçen örnek cümlenin tamamını doğrudan "anlam" 
    alanına kopyalama; kelimenin doğrudan kavramsal karşılığını çıkar.

28. ÇEKİMLER / GRAMER FORMLARI (KRİTİK KURAL):
    Maddelerin altında veya sonunda yer alan mastar (-maq/-mâq/-mek) ve geniş zaman 
    (-ur/-ür/-ar/-er) formlarını (Örn: "aşâr, aşâmâq", "alqaşür, alqaşmâq") 
    KESİNLİKLE "ornek_metin" veya "notlar" alanına koyma! 
    Bu formları ayıklayıp sadece "cekimler" dizisine (array) ekle.

AMAÇ:
Kaynağın kendisini mümkün olduğunca sadık, eksiksiz ve 
dilbilimsel olarak doğru yapılandırılmış sözlük verisine dönüştürmek.
"""


def kullanici_promptu_olustur(
    chunk_no: int,
    sayfa_baslangic: int,
    sayfa_bitis: int,
    metin: str
) -> str:

    return f"""
Aşağıdaki metin Divânu Lügati't-Türk kaynak PDF'sinden
çıkarılmıştır.

Chunk numarası:
{chunk_no}

Kaynak sayfa aralığı:
{sayfa_baslangic}-{sayfa_bitis}

Metni dikkatlice incele ve içindeki sözlük maddelerini
yapılandırılmış biçimde çıkar.

Özellikle şunlara dikkat et:

- Madde başlarını doğru belirle.
- Bir maddenin sonraki sayfada devam edip etmediğini kontrol et.
- Aynı kelimenin farklı anlamlarını ayrı kayıtlar olarak koru.
- Kaynakta olmayan hiçbir bilgiyi ekleme.
- Belirsiz kayıtları kontrol_gerekli=true yap.
- Sayfa numaralarını metindeki [KAYNAK_SAYFA: X] işaretlerinden belirle.

KAYNAK METNİ:

---------------- BEGIN SOURCE ----------------

{metin}

----------------- END SOURCE -----------------
"""


# ============================================================
# 11. GEMINI ÇAĞRISI
# ============================================================

def gemini_chunk_isle(
    chunk: dict
) -> List[dict]:

    chunk_no = chunk["chunk_no"]

    prompt = kullanici_promptu_olustur(
        chunk_no=chunk_no,
        sayfa_baslangic=chunk["sayfa_baslangic"],
        sayfa_bitis=chunk["sayfa_bitis"],
        metin=chunk["metin"]
    )

    for deneme in range(
        1,
        MAX_RETRY + 1
    ):

        for model_adi in MODEL_CANDIDATES:
            try:

                print(
                    f"\nGemini → Chunk {chunk_no} "
                    f"(deneme {deneme}/{MAX_RETRY}, model={model_adi})"
                )

                response = client.models.generate_content(
                    model=model_adi,

                    contents=prompt,

                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,

                        response_mime_type="application/json",

                        response_schema=ChunkCiktisi,

                        temperature=0
                    )
                )

                if not response.text:
                    raise RuntimeError(
                        "Gemini boş cevap döndürdü."
                    )

                sonuc = ChunkCiktisi.model_validate_json(
                    response.text
                )

                kelimeler = [
                    kelime.model_dump()
                    for kelime in sonuc.kelimeler
                ]

                print(
                    f"Chunk {chunk_no}: "
                    f"{len(kelimeler)} kayıt çıkarıldı."
                )

                return kelimeler

            except Exception as e:

                hata = str(e)
                hata_lower = hata.lower()

                if (
                    "404" in hata_lower
                    or "not_found" in hata_lower
                    or "model" in hata_lower and "no longer available" in hata_lower
                ):
                    print(
                        f"Model {model_adi} kullanılmıyor; bir sonraki model adayı deneniyor."
                    )
                    continue

                print(
                    f"Gemini hatası: {hata}"
                )

                if deneme >= MAX_RETRY:
                    raise

                if (
                    "429" in hata_lower
                    or "resource exhausted" in hata_lower
                    or "rate limit" in hata_lower
                ):
                    bekleme = 30 * deneme
                elif (
                    "503" in hata_lower
                    or "unavailable" in hata_lower
                ):
                    bekleme = 20 * deneme
                else:
                    bekleme = RETRY_BASE_SECONDS * (
                        2 ** (deneme - 1)
                    )

                print(
                    f"{bekleme} saniye bekleniyor..."
                )
                time.sleep(bekleme)
                break

    return []


# ============================================================
# 12. İLERLEME TAKİBİ
# ============================================================

def ilerleme_dosyasini_yukle():

    veri = json_oku(
        PROGRESS_FILE,
        {
            "tamamlanan": []
        }
    )

    if "tamamlanan" not in veri:
        veri["tamamlanan"] = []

    return veri


def ilerleme_kaydet(
    tamamlanan: List[int]
):

    json_kaydet(
        PROGRESS_FILE,
        {
            "tamamlanan": sorted(
                list(set(tamamlanan))
            )
        }
    )


# ============================================================
# 13. HATA KAYDI
# ============================================================

def hata_kaydet(
    chunk_no: int,
    hata: str
):

    hatalar = json_oku(
        ERROR_FILE,
        []
    )

    hatalar.append({
        "chunk_no": chunk_no,
        "hata": hata,
        "zaman": time.strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    })

    json_kaydet(
        ERROR_FILE,
        hatalar
    )


# ============================================================
# 14. ANA JSON'U GÜNCELLE
# ============================================================

def ana_jsonu_guncelle(
    yeni_kelimeler: List[dict]
):

    mevcut = json_oku(
        MASTER_JSON_FILE,
        []
    )

    if not isinstance(mevcut, list):
        mevcut = []

    mevcut_hashler = {
        kelime_hash(k)
        for k in mevcut
    }

    eklenen = 0
    tekrar = 0

    for kelime in yeni_kelimeler:

        h = kelime_hash(kelime)

        if h in mevcut_hashler:
            tekrar += 1
            continue

        mevcut.append(kelime)

        mevcut_hashler.add(h)

        eklenen += 1

    # Önce kaynak sayfasına,
    # sonra kelimeye göre sırala.

    mevcut.sort(
        key=lambda x: ( 
            x.get(
                "kaynak_sayfa",
                999999
            ),
            x.get(
                "kelime",
                ""
            ).lower()
        )
    )

    json_kaydet(
        MASTER_JSON_FILE,
        mevcut
    )

    print(
        f"Master JSON: "
        f"+{eklenen} yeni / "
        f"{tekrar} tekrar"
    )

    return mevcut


# ============================================================
# 15. CHUNK JSON KAYDET
# ============================================================

def chunk_json_kaydet(
    chunk: dict,
    kelimeler: List[dict]
):

    dosya = (
        JSON_DIR /
        f"chunk_{chunk['chunk_no']:04d}_"
        f"{chunk['sayfa_baslangic']}_"
        f"{chunk['sayfa_bitis']}.json"
    )

    json_kaydet(
        dosya,
        {
            "chunk_no": chunk["chunk_no"],

            "kaynak_sayfa_baslangic":
                chunk["sayfa_baslangic"],

            "kaynak_sayfa_bitis":
                chunk["sayfa_bitis"],

            "kelimeler": kelimeler
        }
    )


# ============================================================
# 16. CHUNK'LARI GEMINI'YE GÖNDER
# ============================================================

def chunklari_isle(
    chunks: List[dict]
):

    print("\n" + "=" * 60)
    print("GEMINI VERİ ÇIKARMA")
    print("=" * 60)

    ilerleme = ilerleme_dosyasini_yukle()

    tamamlanan = set(
        ilerleme["tamamlanan"]
    )

    toplam_yeni = 0

    for index, chunk in enumerate(
        chunks,
        start=1
    ):

        chunk_no = chunk["chunk_no"]

        print("\n" + "-" * 60)

        print(
            f"İlerleme: {index}/{len(chunks)}"
        )

        print(
            f"Chunk: {chunk_no}"
        )

        print(
            f"Sayfalar: "
            f"{chunk['sayfa_baslangic']}-"
            f"{chunk['sayfa_bitis']}"
        )

        # ----------------------------------------------------
        # Daha önce işlendi mi?
        # ----------------------------------------------------

        if chunk_no in tamamlanan:

            print(
                "Bu chunk daha önce işlendi, atlanıyor."
            )

            continue

        # ----------------------------------------------------
        # Gemini
        # ----------------------------------------------------

        try:

            kelimeler = gemini_chunk_isle(
                chunk
            )

            # Chunk JSON
            chunk_json_kaydet(
                chunk,
                kelimeler
            )

            # Master JSON
            ana_jsonu_guncelle(
                kelimeler
            )

            tamamlanan.add(
                chunk_no
            )

            ilerleme_kaydet(
                list(tamamlanan)
            )

            toplam_yeni += len(kelimeler)

            print(
                f"Chunk {chunk_no} tamamlandı."
            )

        except Exception as e:

            hata = str(e)

            print(
                f"\n!!! CHUNK {chunk_no} BAŞARISIZ !!!"
            )

            print(hata)

            hata_kaydet(
                chunk_no,
                hata
            )

            print(
                "Bu chunk atlanıyor; "
                "program diğer chunk'larla devam edecek."
            )

            continue

    print("\n" + "=" * 60)
    print("GEMINI İŞLEMİ TAMAMLANDI")
    print("=" * 60)

    print(
        f"Bu çalıştırmada çıkarılan kayıt: "
        f"{toplam_yeni}"
    )


# ============================================================
# 17. SON DURUM
# ============================================================

def son_durum():

    print("\n" + "=" * 60)
    print("SON DURUM")
    print("=" * 60)

    mevcut = json_oku(
        MASTER_JSON_FILE,
        []
    )

    ilerleme = json_oku(
        PROGRESS_FILE,
        {
            "tamamlanan": []
        }
    )

    hatalar = json_oku(
        ERROR_FILE,
        []
    )

    print(
        f"Toplam sözlük kaydı: "
        f"{len(mevcut)}"
    )

    print(
        f"Tamamlanan chunk: "
        f"{len(ilerleme.get('tamamlanan', []))}"
    )

    print(
        f"Kayıtlı hata: "
        f"{len(hatalar)}"
    )

    print(
        f"\nMaster JSON:"
        f"\n{MASTER_JSON_FILE}"
    )


# ============================================================
# 18. ANA İŞLEM
# ============================================================

def ana_islem():

    print("\n")
    print("=" * 60)
    print("DIVÂNÜ LÜGATİ'T-TÜRK DİJİTALLEŞTİRME")
    print("=" * 60)

    print(
        f"\nPDF: {PDF_PATH}"
    )

    print(
        f"Model: {MODEL}"
    )

    print(
        f"Sayfa aralığı: "
        f"{BASLANGIC_SAYFASI}-"
        f"{BITIS_SAYFASI if BITIS_SAYFASI else 'SON'}"
    )

    # --------------------------------------------------------
    # 1. PDF text layer testi
    # --------------------------------------------------------

    text_layer_test()

    # --------------------------------------------------------
    # 2. PDF → raw text
    # --------------------------------------------------------

    tum_sayfalar = raw_metni_olustur()

    # --------------------------------------------------------
    # 3. Chunk oluştur
    # --------------------------------------------------------

    chunks = chunklari_olustur(
        tum_sayfalar
    )

    # --------------------------------------------------------
    # 4. Gemini
    # --------------------------------------------------------

    chunklari_isle(
        chunks
    )

    # --------------------------------------------------------
    # 5. Son durum
    # --------------------------------------------------------

    son_durum()


# ============================================================
# 19. PROGRAMI BAŞLAT
# ============================================================

if __name__ == "__main__":

    try:

        ana_islem()

    except KeyboardInterrupt:

        print(
            "\n\nİşlem kullanıcı tarafından durduruldu."
        )

    except Exception as e:

        print(
            "\n\n!!! KRİTİK HATA !!!"
        )

        print(
            str(e)
        )

        raise