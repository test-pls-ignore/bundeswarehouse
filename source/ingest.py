import requests
import sys
import os
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from models import init_db, Vorgang, Dokument, Aktivitaet
from datetime import datetime

API_BASE = "https://search.dip.bundestag.de/api/v1"
REQUEST_TIMEOUT = 30  # seconds

def load_api_key():
    """
    Load API key from api_key.txt file.
    
    Returns:
        str: The API key
        
    Exits:
        1: If file is not found or key is invalid
    """
    try:
        with open("api_key.txt", "r") as f:
            api_key = f.read().strip()
            
        # Validate API key format (basic check)
        if not api_key or len(api_key) < 10:
            print("Error: Invalid API key format.", file=sys.stderr)
            sys.exit(1)
            
        return api_key
    except FileNotFoundError:
        print("Error: api_key.txt not found.", file=sys.stderr)
        sys.exit(1)
    except IOError as e:
        print(f"Error reading api_key.txt: {e}", file=sys.stderr)
        sys.exit(1)

def fetch_resource(resource_name, api_key, limit=10):
    """
    Fetch resources from the Bundestag API.
    
    Args:
        resource_name: Name of the resource endpoint
        api_key: API key for authentication
        limit: Maximum number of results to fetch
        
    Returns:
        list: List of documents or empty list on error
    """
    headers = {"Authorization": f"ApiKey {api_key}"}
    url = f"{API_BASE}/{resource_name}"
    params = {
        "format": "json",
        "limit": limit
    }
    
    try:
        # Enable SSL verification and set timeout
        response = requests.get(
            url, 
            headers=headers, 
            params=params,
            timeout=REQUEST_TIMEOUT,
            verify=True
        )
        
        if response.status_code != 200:
            print(f"Error fetching {resource_name}: {response.status_code}", file=sys.stderr)
            return []
        
        data = response.json()
        return data.get("documents", [])
        
    except requests.exceptions.Timeout:
        print(f"Timeout fetching {resource_name}", file=sys.stderr)
        return []
    except requests.exceptions.RequestException as e:
        print(f"Network error fetching {resource_name}: {e}", file=sys.stderr)
        return []
    except ValueError as e:
        print(f"Invalid JSON response for {resource_name}: {e}", file=sys.stderr)
        return []

def parse_date(date_str):
    """
    Safely parse date string.
    
    Args:
        date_str: Date string in YYYY-MM-DD format
        
    Returns:
        date object or None if parsing fails
    """
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None

def ingest_data():
    """
    Main ingestion function to fetch and store Bundestag data.
    """
    api_key = load_api_key()
    
    try:
        engine = init_db()
        session = Session(engine)
    except SQLAlchemyError as e:
        print(f"Database initialization error: {e}", file=sys.stderr)
        sys.exit(1)
    
    try:
        print("Fetching Vorgänge...")
        vorgaenge = fetch_resource("vorgang", api_key)
        print(f"Found {len(vorgaenge)} Vorgänge.")
        
        for item in vorgaenge:
            v_id = item.get("id")
            if not v_id: 
                continue
            
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
        # Fetch both document types
        for doc_type in ["drucksache", "plenarprotokoll"]:
            docs = fetch_resource(doc_type, api_key)
            print(f"Found {len(docs)} {doc_type}.")
            
            for item in docs:
                d_id = item.get("id")
                if not d_id: 
                    continue
                
                existing_doc = session.query(Dokument).filter_by(id=d_id).first()
                if not existing_doc:
                    # Safely extract PDF URL from nested structure
                    fundstelle = item.get("fundstelle", {})
                    pdf_url = fundstelle.get("pdf_url") if isinstance(fundstelle, dict) else None
                    
                    doc = Dokument(
                        id=d_id,
                        drucksachetyp=item.get("drucksachetyp", doc_type),
                        nummer=item.get("dokumentnummer"),
                        datum=parse_date(item.get("datum")),
                        titel=item.get("titel"),
                        pdf_url=pdf_url,
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
            if not a_id: 
                continue
            
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
        
    except SQLAlchemyError as e:
        session.rollback()
        print(f"Database error during ingestion: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        session.rollback()
        print(f"Unexpected error during ingestion: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        session.close()

if __name__ == "__main__":
    ingest_data()
