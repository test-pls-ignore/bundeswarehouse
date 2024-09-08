import re
import requests
from bs4 import BeautifulSoup

def fetch_webpage_content(url):
    """Holt den Inhalt einer Webseite."""
    try:
        response = requests.get(url)
        response.raise_for_status() 
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Fehler beim Abrufen der Webseite: {e}")
        return None

def extract_text_from_html(html_content):
    """Extrahiert den Text aus HTML-Inhalt."""
    if html_content:
        soup = BeautifulSoup(html_content, 'html.parser')
        return soup.get_text()
    else:
        return None

def search_for_api_key(text):
    """Sucht nach dem API-Key im Text."""
    if text:
        pattern = r"Beispiel:\s*\*ApiKey\s*([\w.-]+)\*"
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None

def save_api_key(api_key, filename="api_key.txt"):
    """Speichert den API-Key in einer Datei."""
    with open(filename, "w") as f:
        f.write(api_key)

def test_api_request(api_key):
    """Führt eine Testabfrage mit dem API-Key durch."""
    url = "https://search.dip.bundestag.de/api/v1" 
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        print(f"Fehler bei der Testabfrage: {e}")
        return False

def main():
    url = 'https://search.dip.bundestag.de/api/v1/openapi.yaml'
    html_content = fetch_webpage_content(url)
    text_content = extract_text_from_html(html_content)
    api_key = search_for_api_key(text_content)

    if api_key:
        print(f"API Key found: {api_key}")
        save_api_key(api_key)

        if test_api_request(api_key):
            print("API-Key ist gültig und Testabfrage erfolgreich!")
        else:
            print("API-Key ist ungültig oder Testabfrage fehlgeschlagen.")
    else:
        print("API Key nicht gefunden.")

if __name__ == "__main__":
    main()