import os
import re
import fitz  # PyMuPDF
import requests
import openpyxl
import shutil
import pytesseract
import unicodedata

from docx import Document
from PIL import Image
from datetime import datetime

# Config
OLLAMA_HOST = "http://localhost:11434/api/generate"
INPUT_DIR = "./old_2"
OUTPUT_DIR = "./new_2"
MODEL_NAME = "llama3.1"
MAX_TEXT_LENGTH = 2000
MAX_FILENAME_LENGTH = 100

def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    return "\n".join(page.get_text() for page in doc)

def extract_text_from_txt(file_path):
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def extract_text_from_excel(file_path):
    wb = openpyxl.load_workbook(file_path, data_only=True)
    text = []
    for sheet in wb:
        for row in sheet.iter_rows(values_only=True):
            text.append(" ".join(str(cell) if cell else "" for cell in row))
    return "\n".join(text)

def extract_text_from_docx(file_path):
    doc = Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs])

def extract_text_from_jpg(file_path):
    try:
        image = Image.open(file_path)
        text = pytesseract.image_to_string(image, lang="nld+eng")
        return text
    except Exception as e:
        print(f"⚠️ OCR mislukt voor {file_path}: {e}")
        return ""

def extract_text(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext in [".txt", ".csv"]:
        return extract_text_from_txt(file_path)
    elif ext in [".xlsx", ".xls"]:
        return extract_text_from_excel(file_path)
    elif ext == ".docx":
        return extract_text_from_docx(file_path)
    elif ext in [".jpg", ".jpeg", ".png"]:
        image = Image.open(file_path)
        text = pytesseract.image_to_string(image)

        lines = text.splitlines()
        top_lines = "\n".join(lines[:10])
        text_content = f"{top_lines}\n\n{text}"

        return text_content



def clean_filename(name):
    name = name.lower().strip()
    name = re.sub(r'[^a-z0-9_.]', '_', name)  # laat puntje staan voor .pdf
    name = re.sub(r'_+', '_', name)
    name = name.replace('_.', '.')  # voorkom fout zoals 'nils_renes_.pdf'
    return name[:MAX_FILENAME_LENGTH]

def normalize_date_format(filename):
    # Ondersteun ook datums met underscores als scheiding
    match = re.search(r'(\d{2})[_-](\d{2})[_-](\d{4})$', filename)
    if match:
        # Haal de datumdelen eruit
        day, month, year = match.groups()
        normalized_date = f"{day}-{month}-{year}"
        # Verwijder de originele datum en vervang met correcte formaat
        filename = re.sub(r'(\d{2})[_-](\d{2})[_-](\d{4})$', normalized_date, filename)
    return filename


def ask_llm_for_filename(text_content, ext=".pdf"):
    prompt = f"""
Je bent een functie die een geldige bestandsnaam genereert op basis van de tekstinhoud van een document.

🧾 Formaat:
[instantie]_[onderwerp]_[datumdocument]

📌 Regels:
- Kleine letters
- Alleen underscores (_) tussen woorden
- Geen streepjes (-), haakjes, quotes, of speciale tekens
- Alleen letters, cijfers en underscores toegestaan
- De datum MOET exact in dit formaat staan: 2 cijfers voor de dag, een koppelteken (-), 2 cijfers voor de maand, een koppelteken (-), 4 cijfers voor het jaar. Bijvoorbeeld: 24-05-2024
- Voeg geen extensie toe (.pdf etc.)
- Geef maar één regel terug, zonder uitleg, code of labels

📍 Voorwaarden voor instantie:
- Kies als instantie **alleen een organisatie of bedrijf** die expliciet in de tekst staat vermeld
- Gebruik **nooit** woorden als "geboorte", "inkomen", "adres", "verwerking", "belasting", tenzij het echt de naam is van een instantie
- Als geen duidelijke instantie gevonden wordt, gebruik `onbekend`

📍 Voor onderwerp:
- Gebruik een herkenbare titel of kopregel zoals 'toestemming verwerking persoonsgegevens', 'verklaring', 'screening', 'contract'
- Houd het onderwerp kort en beschrijvend (max 3 woorden)

📍 Voor datum:
- Als er geen datum te vinden is, gebruik 01-01-1900

--- Tekstinhoud ---
{text_content[:MAX_TEXT_LENGTH]}
--- EINDE ---
"""

    response = requests.post(OLLAMA_HOST, json={
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False
    })

    result = response.json()["response"].strip()
    lines = result.splitlines()
    bestandsnaam = ""

    for line in lines:
        # Stap 1: basis cleaning
        cleaned = line.strip().lower()

        # Stap 2: verwijder ongewenste tekens en accenten
        cleaned = re.sub(r'[*"`:\[\](){}]', '', cleaned)  # markdown, haakjes etc.
        cleaned = re.sub(r'\.pdf$', '', cleaned)  # haal '.pdf' weg indien aanwezig
        cleaned = re.sub(r'^re_', '', cleaned)  # strip 're_' vooraan
        cleaned = strip_accents(cleaned)

        # Stap 3: dubbele underscores opruimen
        cleaned = re.sub(r'_+', '_', cleaned)

        # Corrigeer datum als die in 8-cijferig formaat zit
        cleaned = normalize_date_format(cleaned)        

        # Stap 4: controleer of dit een geldige bestandsnaam is
        if is_valid_filename(cleaned):
            bestandsnaam = cleaned
            break


    if not bestandsnaam:
        print(f"⚠️ Geen geldige naam gevonden in LLM-response:\n{result}")
        raise ValueError(f"Ongeldige bestandsnaam gegenereerd: {result}")

    return bestandsnaam


def rename_files():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for filename in os.listdir(INPUT_DIR):
        ext = os.path.splitext(filename)[1].lower()
        file_path = os.path.join(INPUT_DIR, filename)

        if ext not in [".pdf", ".txt", ".csv", ".xlsx", ".xls", ".docx", ".jpg", ".jpeg"]:
            print(f"⏭️ Bestandstype overgeslagen: {filename}")
            continue

        print(f"📄 Verwerken: {filename}")
        try:
            text = extract_text(file_path)
            suggested_name = ask_llm_for_filename(text, ext)
            new_name = clean_filename(suggested_name) + ext  # ext is bijv. ".jpg"
            destination_path = os.path.join(OUTPUT_DIR, new_name)
            shutil.copyfile(file_path, destination_path)
            print(f"✅ Gekopieerd en hernoemd naar: {new_name}")

        except Exception as e:
            fallback_name = f"onbekend_{filename.replace(ext, '')}_{datetime.now().strftime('%Y%m%d%H%M%S')}{ext}"
            fallback_path = os.path.join(OUTPUT_DIR, fallback_name)
            shutil.copyfile(file_path, fallback_path)
            print(f"⚠️ Fout bij '{filename}': {e}")
            print(f"➡️ Bestand gekopieerd met fallback naam: {fallback_name}")       

def strip_accents(text):
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )


def is_valid_filename(filename):
    parts = filename.split('_')
    if len(parts) < 3:
        return False

    date_part = parts[-1]
    if not re.match(r'^\d{2}-\d{2}-\d{4}$', date_part):
        return False

    # Check of alle andere delen alleen letters/cijfers bevatten
    for part in parts[:-1]:
        if not re.match(r'^[a-z0-9]+$', part):
            return False

    return True

   

if __name__ == "__main__":
    rename_files()

