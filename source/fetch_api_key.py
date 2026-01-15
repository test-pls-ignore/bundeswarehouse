import re
import requests
import sys

# Constants
REQUEST_TIMEOUT = 30  # seconds

def get_key_from_yaml():
    # Wir nutzen die technische Definitionsdatei statt der Webseite
    url = "https://search.dip.bundestag.de/api/v1/openapi.yaml"
    
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        content = response.text
        
        # Wir suchen nach dem Muster aus der YAML Datei:
        # description: "Beispiel: *ApiKey OSOegLs...*"
        # Der Regex sucht nach "ApiKey" gefolgt von einem Leerzeichen und fängt dann den Key
        match = re.search(r"ApiKey\s+([A-Za-z0-9\._\-]+)", content)
        
        if match:
            clean_key = match.group(1).strip()
            print(clean_key)
            sys.exit(0)
        else:
            print("FEHLER: Kein Key in der YAML-Datei gefunden.", file=sys.stderr)
            sys.exit(1)

    except requests.exceptions.Timeout:
        print(f"KRITISCHER FEHLER: Request timeout after {REQUEST_TIMEOUT} seconds", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as e:
        print(f"KRITISCHER FEHLER: Network error - {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"KRITISCHER FEHLER: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    get_key_from_yaml()