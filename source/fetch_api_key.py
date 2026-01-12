import re
import requests
import sys
from bs4 import BeautifulSoup

def get_key():
    url = "https://dip.bundestag.de/über-dip/hilfe/api"
    
    # Tarnung als Browser
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        text_content = soup.get_text()
        
        # DEBUGGING: Wir suchen grob nach der Stelle und geben sie im Fehlerfall aus
        start_index = text_content.find("API-Key")
        if start_index != -1:
            # Wir nehmen einen Ausschnitt von 200 Zeichen ab dem Wort "API-Key"
            # um zu sehen, was da wirklich steht.
            snippet = text_content[start_index:start_index+200]
            # Wir schreiben das als Debug-Info auf stderr (damit es im GitHub Log rot auftaucht, aber nicht als Key gewertet wird)
            print(f"DEBUG INFO - Gefundener Text-Schnipsel: {snippet!r}", file=sys.stderr)
            
            # Strategie: Wir suchen in diesem Schnipsel nach dem Key.
            # Ein Key ist ein langes Wort (mind 20 Zeichen) aus Buchstaben, Zahlen, Punkten, Bindestrichen.
            # Wir ignorieren, ob davor "lautet:" oder sonst was steht.
            match = re.search(r"([A-Za-z0-9\._\-]{20,})", snippet)
            
            if match:
                clean_key = match.group(1).strip()
                # Sicherheitscheck: Ist das wirklich der Key oder nur ein langer Text?
                # Der Key hat Punkte und ist kryptisch.
                print(clean_key)
                sys.exit(0)
            else:
                print("FEHLER: Kein Token im Schnipsel gefunden, das wie ein Key aussieht.", file=sys.stderr)
                sys.exit(1)
        else:
            print("FEHLER: Wort 'API-Key' gar nicht auf der Seite gefunden.", file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        print(f"KRITISCHER FEHLER: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    get_key()