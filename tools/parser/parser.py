import fitz  # PyMuPDF
import re
import json
from pathlib import Path


# ============================================================
# AYARLAR
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

PDF_PATH = BASE_DIR / "dlt.pdf"
OUTPUT_PATH = BASE_DIR / "dlt_kelime_anlam.json"


# PyMuPDF'de bold font flag'i
BOLD_FLAG = 16


# PDF span'ları arasındaki maksimum fiziksel boşluk.
#
# Bu değer:
#   aç | ış | mak
#
# gibi PDF tarafından parçalanmış span'ların:
#   açışmak
#
# olarak birleşmesini sağlar.
#
# Gerçek kelimeler arasında daha büyük boşluk varsa:
#   iyi | gıdalar
#
# "iyi gıdalar" olarak kalır.
SPAN_SPACE_THRESHOLD = 2.0


# ============================================================
# SAYFA REFERANSLARI
# ============================================================

PAGE_REF_RE = re.compile(
    r"""
    [IVX]+\s*,\s*\d+
    (?:\s*,\s*\d+)*
    (?:
        \s*;\s*
        [IVX]+\s*,\s*\d+
        (?:\s*,\s*\d+)*
    )?
    """,
    re.VERBOSE | re.IGNORECASE
)


TRAILING_REFERENCE_RE = re.compile(
    r"""
    \s*
    [IVX]+\s*,\s*\d+
    (?:\s*,\s*\d+)*
    (?:
        \s*;\s*
        [IVX]+\s*,\s*\d+
        (?:\s*,\s*\d+)*
    )?
    .*$
    """,
    re.VERBOSE | re.IGNORECASE
)


# ============================================================
# TEMİZLEME
# ============================================================

def clean_text(text: str):
    """
    Genel metin temizliği.
    """

    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = text.replace("\u200b", "")
    text = text.replace("\ufeff", "")

    # Birden fazla boşluğu teke indir
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def clean_word(word: str):
    """
    Kelime temizliği.
    """

    word = clean_text(word)

    # Baştaki/sondaki tablo işaretleri
    word = word.strip(" \t.,;:")

    return word


def clean_meaning(meaning: str):
    """
    Anlam temizliği.
    """

    meaning = clean_text(meaning)

    # Cilt/sayfa referanslarını kaldır
    meaning = TRAILING_REFERENCE_RE.sub("", meaning)

    # Sondaki ayraçlar
    meaning = meaning.strip(" \t·.,;:")

    return meaning


# ============================================================
# BOLD KONTROLÜ
# ============================================================

def is_bold(span):
    """
    PyMuPDF span font flags üzerinden bold kontrolü.
    """

    flags = span.get("flags", 0)

    return bool(flags & BOLD_FLAG)


# ============================================================
# SPAN'LARI FİZİKSEL X MESAFESİNE GÖRE BİRLEŞTİR
# ============================================================

def join_spans(spans, space_threshold=SPAN_SPACE_THRESHOLD):
    """
    PDF'den gelen span'ları X koordinatlarındaki fiziksel
    mesafeye göre birleştirir.

    PDF bazen tek bir kelimeyi birden fazla span'a bölebilir:

        aç
        ış
        mak

    Bunları:

        açışmak

    şeklinde birleştirir.

    Gerçek kelimeler arasında fiziksel boşluk varsa:

        iyi    gıdalar    ile    beslemek

    şeklindeki boşlukları korur.
    """

    if not spans:
        return ""

    spans = sorted(
        spans,
        key=lambda s: s["bbox"][0]
    )

    first_text = clean_text(spans[0]["text"])

    if not first_text:
        return ""

    result = first_text

    previous_right = spans[0]["bbox"][2]

    for span in spans[1:]:

        text = clean_text(span["text"])

        if not text:
            continue

        current_left = span["bbox"][0]

        gap = current_left - previous_right

        # ----------------------------------------------------
        # Span'lar birbirine çok yakınsa:
        #
        # aç | ış | mak
        #
        # -> açışmak
        # ----------------------------------------------------

        if gap <= space_threshold:
            result += text

        # ----------------------------------------------------
        # Aralarında gerçek boşluk varsa:
        #
        # iyi | gıdalar
        #
        # -> iyi gıdalar
        # ----------------------------------------------------

        else:
            result += " " + text

        previous_right = span["bbox"][2]

    return clean_text(result)


# ============================================================
# TÜM SPAN'LARI AL
# ============================================================

def get_all_spans(page):

    data = page.get_text("dict")

    spans = []

    for block in data.get("blocks", []):

        if block.get("type") != 0:
            continue

        for line in block.get("lines", []):

            for span in line.get("spans", []):

                text = span.get("text", "")

                if not text.strip():
                    continue

                spans.append({
                    "text": text,
                    "bbox": span["bbox"],
                    "flags": span.get("flags", 0),
                    "font": span.get("font", ""),
                    "size": span.get("size", 0),
                })

    return spans


# ============================================================
# Y KONUMUNA GÖRE SATIRLARI GRUPLA
# ============================================================

def group_by_y(spans, tolerance=3.0):

    spans = sorted(
        spans,
        key=lambda s: (
            s["bbox"][1],
            s["bbox"][0]
        )
    )

    rows = []

    for span in spans:

        y = span["bbox"][1]

        placed = False

        for row in rows:

            row_y = row["y"]

            if abs(y - row_y) <= tolerance:

                row["spans"].append(span)

                # Ortalama Y'yi güncelle
                row["y"] = sum(
                    s["bbox"][1]
                    for s in row["spans"]
                ) / len(row["spans"])

                placed = True

                break

        if not placed:

            rows.append({
                "y": y,
                "spans": [span]
            })

    # Her satırı X'e göre sırala
    for row in rows:

        row["spans"].sort(
            key=lambda s: s["bbox"][0]
        )

    return rows


# ============================================================
# TABLO SATIRI PARSE
# ============================================================

def parse_table_row(row):

    spans = row["spans"]

    # --------------------------------------------------------
    # Bold spanları bul
    # --------------------------------------------------------

    bold_spans = [
        s for s in spans
        if is_bold(s)
    ]

    if not bold_spans:
        return None

    # X konumuna göre sırala
    bold_spans.sort(
        key=lambda s: s["bbox"][0]
    )

    # --------------------------------------------------------
    # İlk bold grubunu kelime kabul et
    #
    # Aynı kelime PDF tarafından birkaç span'a bölünmüş
    # olabilir.
    # --------------------------------------------------------

    kelime_spans = []

    first_bold_x = bold_spans[0]["bbox"][0]

    for span in bold_spans:

        # Aynı kelime içerisindeki parçalar genellikle
        # birbirine yakın olur.
        if span["bbox"][0] - first_bold_x < 250:

            kelime_spans.append(span)

    kelime_spans.sort(
        key=lambda s: s["bbox"][0]
    )

    # --------------------------------------------------------
    # Kelimeyi fiziksel X mesafesine göre birleştir
    # --------------------------------------------------------

    kelime = clean_word(
        join_spans(kelime_spans)
    )

    if not kelime:
        return None

    # --------------------------------------------------------
    # Bold kelimenin bittiği X
    # --------------------------------------------------------

    last_bold_x = max(
        s["bbox"][2]
        for s in kelime_spans
    )

    # --------------------------------------------------------
    # Bold'dan sonra gelen normal spanları bul
    # --------------------------------------------------------

    meaning_spans = []

    for span in spans:

        if is_bold(span):
            continue

        if span["bbox"][0] >= last_bold_x - 2:

            meaning_spans.append(span)

    meaning_spans.sort(
        key=lambda s: s["bbox"][0]
    )

    # --------------------------------------------------------
    # Anlamı fiziksel mesafeye göre birleştir
    # --------------------------------------------------------

    meaning = clean_meaning(
        join_spans(meaning_spans)
    )

    if not meaning:
        return None

    # --------------------------------------------------------
    # Sayfa referanslarını kaldır
    # --------------------------------------------------------

    meaning = PAGE_REF_RE.sub(
        "",
        meaning
    )

    meaning = clean_meaning(meaning)

    if not meaning:
        return None

    return {
        "kelime": kelime,
        "anlam": meaning
    }


# ============================================================
# ALTERNATİF SATIR PARSER
# ============================================================

def parse_line(spans):

    if not spans:
        return None

    # --------------------------------------------------------
    # Bold spanları bul
    # --------------------------------------------------------

    bold_spans = [
        span for span in spans
        if is_bold(span)
    ]

    if not bold_spans:
        return None

    bold_spans.sort(
        key=lambda x: x["bbox"][0]
    )

    # --------------------------------------------------------
    # Kelime
    # --------------------------------------------------------

    kelime = clean_word(
        join_spans(bold_spans)
    )

    if not kelime:
        return None

    # --------------------------------------------------------
    # Bold kelimenin bittiği X
    # --------------------------------------------------------

    last_bold_x = max(
        span["bbox"][2]
        for span in bold_spans
    )

    # --------------------------------------------------------
    # Anlam
    # --------------------------------------------------------

    meaning_spans = []

    for span in spans:

        if is_bold(span):
            continue

        x0 = span["bbox"][0]

        if x0 >= last_bold_x - 2:

            meaning_spans.append(span)

    meaning_spans.sort(
        key=lambda x: x["bbox"][0]
    )

    meaning = clean_meaning(
        join_spans(meaning_spans)
    )

    if not meaning:
        return None

    return {
        "kelime": kelime,
        "anlam": meaning
    }


# ============================================================
# PDF PARSE
# ============================================================

def parse_pdf(pdf_path):

    doc = fitz.open(pdf_path)

    records = []

    total_pages = len(doc)

    print(
        f"Toplam PDF sayfası: {total_pages:,}"
    )

    for page_index, page in enumerate(doc, 1):

        spans = get_all_spans(page)

        rows = group_by_y(spans)

        for row in rows:

            record = parse_table_row(row)

            if record is None:
                continue

            records.append(record)

        # İlerleme
        if page_index % 50 == 0:

            print(
                f"İşlenen sayfa: "
                f"{page_index:,}/{total_pages:,} "
                f"| Kayıt: {len(records):,}"
            )

    doc.close()

    return records


# ============================================================
# SON TEMİZLİK
# ============================================================

def clean_records(records):

    cleaned = []

    for record in records:

        kelime = clean_word(
            record.get(
                "kelime",
                ""
            )
        )

        anlam = clean_meaning(
            record.get(
                "anlam",
                ""
            )
        )

        if not kelime:
            continue

        if not anlam:
            continue

        cleaned.append({
            "kelime": kelime,
            "anlam": anlam
        })

    return cleaned


# ============================================================
# JSON KAYDET
# ============================================================

def save_json(records, output_path):

    with open(
        output_path,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            records,
            f,
            ensure_ascii=False,
            indent=2
        )


# ============================================================
# MAIN
# ============================================================

def main():

    if not PDF_PATH.exists():

        raise FileNotFoundError(
            f"PDF bulunamadı:\n{PDF_PATH}"
        )

    print("=" * 70)
    print("DIVÂNÜ LÜGATİ'T-TÜRK")
    print("BOLD KELİME → NORMAL ANLAM PARSER")
    print("=" * 70)

    print()
    print(
        f"PDF: {PDF_PATH}"
    )

    print()
    print("PDF okunuyor...")

    records = parse_pdf(
        PDF_PATH
    )

    records = clean_records(
        records
    )

    save_json(
        records,
        OUTPUT_PATH
    )

    print()
    print("=" * 70)
    print("TAMAMLANDI")
    print("=" * 70)

    print(
        f"Kayıt sayısı : {len(records):,}"
    )

    print(
        f"Çıktı        : {OUTPUT_PATH}"
    )

    print()
    print("İlk 30 kayıt:")
    print("-" * 70)

    for i, record in enumerate(
        records[:30],
        1
    ):

        print(
            f"{i:>3}. "
            f"{record['kelime']} → "
            f"{record['anlam']}"
        )

    print()
    print(
        "Aynı kelimenin tekrarları korunur."
    )

    print(
        "Kaynak/cilt/sayfa bilgileri JSON'a alınmaz."
    )


# ============================================================
# ÇALIŞTIR
# ============================================================

if __name__ == "__main__":
    main()

