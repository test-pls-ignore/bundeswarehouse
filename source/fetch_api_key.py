import re
import requests
import sys
from bs4 import BeautifulSoup

def get_key():
    url = "https://dip.bundestag.de/über-dip/hilfe/api"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        text_content = soup.get_text()
        
        # Regex passend zum Screenshot
        match = re.search(r"API-Key lautet:\s*([A-Za-z0-9\._\-]+)", text_content)
        
        if match:
            # WICHTIG: Nur den Key printen, keinen anderen Text!
            print(match.group(1).strip())
            sys.exit(0)
        else:
            sys.exit(1) # Fehlercode, damit der Workflow abbricht

    except Exception:
        sys.exit(1)

if __name__ == "__main__":
    get_key()