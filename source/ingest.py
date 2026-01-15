import requests
import sys
import os
from sqlalchemy.orm import Session
from models import init_db, Vorgang, Dokument, Aktivitaet
from datetime import datetime

API_BASE = "https://search.dip.bundestag.de/api/v1"
REQUEST_TIMEOUT = 30  # seconds
DEFAULT_FETCH_LIMIT = 10

def load_api_key():
    try:
        with open("api_key.txt", "r") as f:
            key = f.read().strip()
            if not key:
                print("Error: api_key.txt is empty.")
                sys.exit(1)
            return key
    except FileNotFoundError:
        print("Error: api_key.txt not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading api_key.txt: {e}")
        sys.exit(1)

def fetch_resource(resource_name, api_key, limit=DEFAULT_FETCH_LIMIT):
    headers = {"Authorization": f"ApiKey {api_key}"}
    url = f"{API_BASE}/{resource_name}"
    params = {
        "format": "json",
        "limit": limit
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code != 200:
            print(f"Error fetching {resource_name}: {response.status_code} - {response.text}")
            return []
        
        try:
            data = response.json()
        except ValueError as e:
            print(f"Error parsing JSON for {resource_name}: {e}")
            return []
            
        return data.get("documents", [])
    except requests.exceptions.Timeout:
        print(f"Timeout fetching {resource_name} after {REQUEST_TIMEOUT} seconds")
        return []
    except requests.exceptions.RequestException as e:
        print(f"Network error fetching {resource_name}: {e}")
        return []
    except Exception as e:
        print(f"Exception fetching {resource_name}: {e}")
        return []

def parse_date(date_string):
    """Safely parse date string, return None if invalid."""
    if not date_string:
        return None
    try:
        return datetime.strptime(date_string, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

def ingest_data():
    api_key = load_api_key()
    engine = init_db()
    session = Session(engine)
    
    print("Fetching Vorgänge...")
    vorgaenge = fetch_resource("vorgang", api_key)
    print(f"Found {len(vorgaenge)} Vorgänge.")
    
    for item in vorgaenge:
        v_id = item.get("id")
        if not v_id: continue
        
        # Upsert Vorgang
        existing = session.query(Vorgang).filter_by(id=v_id).first()
        if not existing:
            vorgang = Vorgang(
                id=v_id,
                titel=item.get("titel"),
                wahlperiode=item.get("wahlperiode"),
                typ=item.get("typ"),
                vorgangstyp=item.get("vorgangstyp"),
                datum=parse_date(item.get("datum")),
                metadata_json=item
            )
            session.add(vorgang)
        else:
            existing.metadata_json = item
            existing.titel = item.get("titel")

    print("Fetching Dokumente...")
    # Drucksachen und Plenarprotokolle sind beide unter /drucksache bzw /plenarprotokoll oder zusammen?
    # Laut Doku summary: "Drucksachen und Plenarprotokolle".
    # Checking common endpoints: /drucksache, /plenarprotokoll
    # We will try both
    
    for doc_type in ["drucksache", "plenarprotokoll"]:
        docs = fetch_resource(doc_type, api_key)
        print(f"Found {len(docs)} {doc_type}.")
        
        for item in docs:
            d_id = item.get("id")
            if not d_id: continue
            
            existing_doc = session.query(Dokument).filter_by(id=d_id).first()
            if not existing_doc:
                doc = Dokument(
                    id=d_id,
                    drucksachetyp=item.get("drucksachetyp", doc_type),
                    nummer=item.get("dokumentnummer"),
                    datum=parse_date(item.get("datum")),
                    titel=item.get("titel"),
                    pdf_url=item.get("fundstelle", {}).get("pdf_url") if isinstance(item.get("fundstelle"), dict) else None,
                    metadata_json=item
                )
                session.add(doc)
            else:
                existing_doc.metadata_json = item

    print("Fetching Aktivitäten...")
    aktivitaeten = fetch_resource("aktivitaet", api_key)
    print(f"Found {len(aktivitaeten)} Aktivitäten.")
    
    for item in aktivitaeten:
        a_id = item.get("id")
        if not a_id: continue
        
        existing_act = session.query(Aktivitaet).filter_by(id=a_id).first()
        if not existing_act:
            act = Aktivitaet(
                id=a_id,
                datum=parse_date(item.get("datum")),
                titel=item.get("titel"),
                art=item.get("art"),
                metadata_json=item
            )
            session.add(act)
        else:
            existing_act.metadata_json = item

    session.commit()
    print("Ingestion complete.")

if __name__ == "__main__":
    ingest_data()
