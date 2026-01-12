import re
import requests
import sys
from bs4 import BeautifulSoup

def get_key():
    url = "https://dip.bundestag.de/über-dip/hilfe/api"
    
    # WICHTIG: Wir tarnen uns als normaler Browser (Chrome auf Windows)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        # Fehlermeldung auf stderr schreiben, damit sie im GitHub Log erscheint
        # print("Debug: Rufe URL ab...", file=sys.stderr) 
        
        response = requests.get(url, headers=headers)
        response.raise_for_status() # Wirft Fehler bei 403/404/500
        
        soup = BeautifulSoup(response.text, 'html.parser')
        text_content = soup.get_text()
        
        # Regex Suche
        match = re.search(r"API-Key lautet:\s*([A-Za-z0-9\._\-]+)", text_content)
        
        if match:
            # Nur der Key darf auf stdout landen!
            print(match.group(1).strip())
            sys.exit(0)
        else:
            print("FEHLER: Regex hat keinen Key im Text gefunden!", file=sys.stderr)
            # Optional: Die ersten 500 Zeichen ausgeben zum Debuggen
            # print(text_content[:500], file=sys.stderr)
            sys.exit(1)

    except Exception as e:
        print(f"KRITISCHER FEHLER: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    get_key()