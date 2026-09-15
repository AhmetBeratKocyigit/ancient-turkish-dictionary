import json
import time
import os
import google.generativeai as genai
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
from rich.panel import Panel
from rich.table import Table

# Console ve Rich Yapılandırması
console = Console()

API_KEY = "YOUR_API_KEY_HERE"  # Lütfen kendi API anahtarınızı buraya ekleyin
genai.configure(api_key=API_KEY)

SYSTEM_PROMPT = """
Sen uzman bir Türkolog ve leksikografsın. Sana Divânu Lugāti't-Türk içerisindeki sözlüğün JSON formatındaki halini veriyorum. 
Sana verilen sözlük JSON verilerini şu kurallara göre kesin olarak temizle ve düzelt:

1. ANLAM ALANI TEMİZLİĞİ:
   - Anlam kısmına yanlışlıkla yazılmış özne, kişi veya örnek cümle kalıntılarını temizle (Örn: "adam acıktı" -> "acıkmak").
   - Eylem tanımlarını mutlaka mastar haliyle (-mak/-mek) yaz.
   - Tanım açıklamasını küçük harfle başlat (Özel isimler hariç).
   - Eş anlamlı veya yakın anlamlı kelimeler arasına virgül koy, tanımın sonuna nokta ekle.

2. ÖRNEK METİN VEYA NOT ALANI:
   - Eğer örnek cümle kısmına notlar kısmında olması gereken bir açıklama/bilgi karışmış ise bunu 'notlar' bölümüne aktar.

3. DİĞER:
   - Orijinal JSON yapısını ve tüm alan anahtarlarını bozmadan geçerli JSON formatında yanıt ver.
"""

def clean_dictionary_range(input_file: str, output_file: str, start_index: int = 0, end_index: int = 200, batch_size: int = 50):
    start_time = time.time()
    
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            all_data = json.load(f)
    except FileNotFoundError:
        console.print(f"[bold red]Hata:[/bold red] '{input_file}' dosyası bulunamadı!")
        return

    target_data = all_data[start_index:end_index]
    total_items = len(target_data)

    if total_items == 0:
        console.print("[bold yellow]Uyarı:[/bold yellow] Belirtilen aralıkta işlenecek kayıt bulunamadı.")
        return

    model = genai.GenerativeModel(
        model_name="gemini-3.5-flash-lite",
        system_instruction=SYSTEM_PROMPT,
        generation_config={"response_mime_type": "application/json"}
    )

    cleaned_data = []
    successful_batches = 0
    failed_batches = 0
    errors = []

    console.print(Panel.fit(
        f"[bold cyan]Divânu Lugāti't-Türk Veri Temizleme İşlemi[/bold cyan]\n"
        f"Toplam Kayıt Sayısı: [bold yellow]{len(all_data)}[/bold yellow] | "
        f"İşlenecek Aralık: [bold green]{start_index} - {end_index}[/bold green] ([bold]{total_items}[/bold] kayıt)",
        border_style="cyan"
    ))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=40, complete_style="green", finished_style="bold green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console
    ) as progress:

        task = progress.add_task("[yellow]Veriler işleniyor...", total=total_items)

        for i in range(0, total_items, batch_size):
            batch = target_data[i:i + batch_size]
            current_batch_size = len(batch)
            batch_num = i // batch_size + 1
            
            prompt = f"Aşağıdaki sözlük kayıtlarını kurallara göre temizle:\n{json.dumps(batch, ensure_ascii=False)}"
            
            try:
                response = model.generate_content(prompt)
                batch_result = json.loads(response.text)
                
                if isinstance(batch_result, list):
                    cleaned_data.extend(batch_result)
                else:
                    cleaned_data.append(batch_result)
                    
                successful_batches += 1
            except Exception as e:
                failed_batches += 1
                err_msg = f"Paket {batch_num}: {str(e)}"
                errors.append(err_msg)
                
                # Hata gerçekleştiği anda konsola yazdırır (ilerleme çubuğunu bozmaz)
                progress.console.print(f"[bold red]Hata oluştu ->[/bold red] {err_msg}")
                
                # Hata durumunda orijinal veriyi koru
                cleaned_data.extend(batch)

            time.sleep(1)
            progress.update(task, advance=current_batch_size)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, ensure_ascii=False, indent=2)

    elapsed_time = round(time.time() - start_time, 2)

    table = Table(title="[bold green]İşlem Tamamlandı ve Rapor Oluşturuldu[/bold green]", border_style="green")
    table.add_column("Metrik", style="bold cyan")
    table.add_column("Değer", style="bold white")

    table.add_row("İşlenen Kayıt Aralığı", f"{start_index} - {end_index}")
    table.add_row("Toplam Temizlenen Kayıt", str(len(cleaned_data)))
    table.add_row("Başarılı Paket Sayısı", f"[green]{successful_batches}[/green]")
    table.add_row("Hatalı Paket Sayısı", f"[red]{failed_batches}[/red]" if failed_batches > 0 else "0")
    table.add_row("Toplam Geçen Süre", f"{elapsed_time} saniye")
    table.add_row("Çıktı Dosyası", output_file)

    console.print("\n")
    console.print(table)

if __name__ == "__main__":
    clean_dictionary_range(
        input_file='dlt.json',
        output_file='llm_temizlenmis_sozluk.json',
        start_index=0,
        end_index=8536,
        batch_size=30
    )
    
    console.print("\n[bold cyan]İşlem tamamlandı. Konsolu kapatmak için ENTER tuşuna basın...[/bold cyan]")
    input()
