"""
Database models for Bundestag data warehouse.

This module defines SQLAlchemy models for storing parliamentary data
including proceedings (Vorgänge), documents (Dokumente), and activities (Aktivitäten).
"""

from sqlalchemy import create_engine, Column, Integer, String, Text, Date, ForeignKey, JSON, DateTime
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()

class Vorgang(Base):
    """
    Parliamentary proceedings/processes model.
    """
    __tablename__ = 'vorgaenge'
    
    id = Column(String, primary_key=True)  # ID from API
    wahlperiode = Column(Integer)
    titel = Column(Text)
    datum = Column(Date)
    typ = Column(String)
    vorgangstyp = Column(String)
    aktueller_stand = Column(String)
    inhalt = Column(Text)  # Abstract/Description
    
    # Metadata as JSON for flexibility
    metadata_json = Column(JSON)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())

    dokumente = relationship("Dokument", back_populates="vorgang", cascade="all, delete-orphan")
    aktivitaeten = relationship("Aktivitaet", back_populates="vorgang", cascade="all, delete-orphan")

class Dokument(Base):
    """
    Parliamentary documents model (Drucksachen and Plenarprotokolle).
    """
    __tablename__ = 'dokumente'
    
    id = Column(String, primary_key=True)
    vorgang_id = Column(String, ForeignKey('vorgaenge.id'), nullable=True)  # Can belong to a Vorgang
    
    drucksachetyp = Column(String)  # e.g. "Drucksache", "Plenarprotokoll"
    nummer = Column(String)  # "19/12345"
    datum = Column(Date)
    titel = Column(Text)
    autoren = Column(String)  # Serialized list or specific table if needed
    
    pdf_url = Column(String)
    text_content = Column(Text)  # Extracted or full text if available
    
    metadata_json = Column(JSON)
    created_at = Column(DateTime, server_default=func.now())
    
    vorgang = relationship("Vorgang", back_populates="dokumente")

class Aktivitaet(Base):
    """
    Parliamentary activities model (speeches, questions, etc.).
    """
    __tablename__ = 'aktivitaeten'
    
    id = Column(String, primary_key=True)
    vorgang_id = Column(String, ForeignKey('vorgaenge.id'))
    
    datum = Column(Date)
    person = Column(String)  # Name of MP/Gov member
    art = Column(String)  # "Rede", "Frage", ...
    titel = Column(Text)
    
    metadata_json = Column(JSON)
    created_at = Column(DateTime, server_default=func.now())
    
    vorgang = relationship("Vorgang", back_populates="aktivitaeten")

def init_db(db_path='warehouse.db'):
    """
    Initialize the database and create all tables.
    
    Args:
        db_path: Path to the SQLite database file
        
    Returns:
        SQLAlchemy Engine instance
    """
    engine = create_engine(f'sqlite:///{db_path}')
    Base.metadata.create_all(engine)
    return engine
