// Type definitions for the Bundestag data models

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type JsonValue = any;

export interface Vorgang {
  id: string;
  wahlperiode: number | null;
  titel: string | null;
  datum: Date | string | null;
  typ: string | null;
  vorgangstyp: string | null;
  aktueller_stand: string | null;
  inhalt: string | null;
  metadata_json: JsonValue;
  created_at: Date | string | null;
  updated_at: Date | string | null;
}

export interface Dokument {
  id: string;
  vorgang_id: string | null;
  drucksachetyp: string | null;
  nummer: string | null;
  datum: Date | string | null;
  titel: string | null;
  autoren: string | null;
  pdf_url: string | null;
  text_content: string | null;
  metadata_json: JsonValue;
  created_at: Date | string | null;
}

export interface Aktivitaet {
  id: string;
  vorgang_id: string | null;
  datum: Date | string | null;
  person: string | null;
  art: string | null;
  titel: string | null;
  metadata_json: JsonValue;
  created_at: Date | string | null;
}

export interface SearchResults {
  vorgaenge: Vorgang[];
  dokumente: Dokument[];
  aktivitaeten: Aktivitaet[];
}
