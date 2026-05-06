"""
notion_db.py — Notion-API-Integration für Lead-Speicherung

Spalten in der Notion-Datenbank (exakt wie im Screenshot):
  - Firmenname (Title): Firmenname
  - Name (Rich Text): Ansprechpartner
  - Email (Email): E-Mail-Adresse
  - Telefon (Phone Number): Telefonnummer
  - Probleme (Rich Text): Analyse-Ergebnisse
  - Website (URL): Firmenwebsite
  - Status (Status): "Neu"

Error-Handling: Fehlende Felder werden als leerer String/None gesendet,
niemals wird der API-Call abgebrochen.
"""

import logging
import os
import re
from typing import Optional

from notion_client import Client
from tinydb import TinyDB

logger = logging.getLogger("leadbot.notion")

# Globale Notion-Client-Instanz — wird einmalig in init_notion() erstellt
notion_client: Optional[Client] = None

# TinyDB-Referenz für den Sync-Status-Update
LeadsTable = None


def init_notion(db_instance=None):
    """
    Initialisiert den Notion-API-Client mit den Credentials aus .env.
    """
    global notion_client, LeadsTable
    LeadsTable = db_instance

    api_key = os.getenv("NOTION_API_KEY")
    database_id = os.getenv("NOTION_DATABASE_ID")

    if api_key and database_id:
        notion_client = Client(auth=api_key)
        logger.info("Notion-Client initialisiert (Datenbank: %s)", database_id)
    else:
        logger.info(
            "Notion-Credentials fehlen (NOTION_API_KEY / NOTION_DATABASE_ID) — "
            "Sync zu Notion ist deaktiviert"
        )


def _clean_url_for_check(url: str) -> str:
    """
    Bereinigt eine URL für den Duplikat-Check.
    Entfernt Protokoll, www, trailing slash und konvertiert zu lowercase.
    """
    if not url:
        return ""
    cleaned = url.lower().strip()
    cleaned = re.sub(r"^https?://", "", cleaned)
    cleaned = re.sub(r"^www\.", "", cleaned)
    cleaned = cleaned.rstrip("/")
    return cleaned


def check_if_exists(name: str, website: str) -> bool:
    """
    Prüft, ob ein Lead bereits in der Notion-Datenbank existiert.
    Sucht nach bereinigtem Firmennamen ODER bereinigter Website-URL.

    Returns:
        True wenn ein Duplikat gefunden wurde, False sonst.
    """
    if not notion_client:
        return False

    database_id = os.getenv("NOTION_DATABASE_ID")
    if not database_id:
        return False

    cleaned_name = name.lower().strip() if name else ""
    cleaned_website = _clean_url_for_check(website)

    try:
        filters = []

        # 1. Suche nach Firmenname (title)
        if cleaned_name and len(cleaned_name) >= 2:
            filters.append({
                "property": "Firmenname",
                "title": {"contains": cleaned_name}
            })

        # 2. Suche nach Website (url) — mit und ohne www
        if cleaned_website and len(cleaned_website) >= 4:
            filters.append({
                "property": "Website",
                "url": {"contains": cleaned_website}
            })

        if not filters:
            return False

        # OR-Filter: Name OR Website
        query_filter = {"or": filters} if len(filters) > 1 else filters[0]

        response = notion_client.databases.query(
            database_id=database_id,
            filter=query_filter,
            page_size=1,
        )

        results = response.get("results", [])
        if results:
            existing_name = ""
            try:
                existing_name = results[0]["properties"]["Firmenname"]["title"][0]["text"]["content"]
            except (KeyError, IndexError):
                pass
            logger.info(
                "[Skip] Lead existiert bereits in Notion: '%s' (Website: %s)",
                existing_name or name, website or "N/A",
            )
            return True

        return False

    except Exception as e:
        logger.error("Duplikat-Check fehlgeschlagen für '%s': %s", name, e)
        return False


def sync_to_notion(lead: dict):
    """
    Erstellt eine neue Seite in der Notion-Datenbank mit allen Lead-Daten.

    Spalten-Mapping (exakt zum Screenshot):
      - Firmenname  → title
      - Name        → rich_text  (Ansprechpartner)
      - Email       → email
      - Telefon     → phone_number
      - Probleme    → rich_text  (Analyse-Ergebnisse)
      - Website     → url
      - Status      → status     (immer "Neu")

    Fehlende Felder (z.B. Telefon) werden als leerer String/None behandelt,
    niemals bricht der Call ab.
    """
    if not notion_client:
        logger.debug("Notion-Sync übersprungen — Client nicht initialisiert")
        return

    database_id = os.getenv("NOTION_DATABASE_ID")
    if not database_id:
        return

    # Probleme kombinieren: reasons + design_criticism
    problems_parts = []
    reasons = lead.get("reason_string", "")
    if reasons and reasons != "Keine Website":
        problems_parts.append(reasons)
    design_criticism = lead.get("design_criticism_string", "")
    if design_criticism:
        problems_parts.append(design_criticism)
    problems_text = "; ".join(problems_parts) if problems_parts else "Keine Probleme gefunden"

    # Properties-Builder: nur Felder mit Werten hinzufügen
    properties = {
        "Firmenname": {
            "title": [{"text": {"content": lead.get("name", "Unbekannt")}}]
        },
        "Probleme": {
            "rich_text": [{"text": {"content": problems_text}}]
        },
        "Website": {"url": lead.get("website", "") or None},
        "Status": {"status": {"name": "Neu"}},
    }

    # Name (Ansprechpartner) — optional
    contact_name = lead.get("contact_name", "")
    if contact_name:
        properties["Name"] = {
            "rich_text": [{"text": {"content": contact_name}}]
        }
    else:
        properties["Name"] = {
            "rich_text": [{"text": {"content": ""}}]
        }

    # Email — optional, nur wenn vorhanden und gültig
    email = lead.get("email", "")
    if email and email != "Manuell prüfen" and "@" in email:
        properties["Email"] = {"email": email}
    else:
        # Leeres Email-Feld: wir setzen es als leeren String
        # Notion akzeptiert leere email Felder manchmal nicht,
        # deshalb fügen wir es nur hinzu wenn es einen Wert hat
        pass

    # Telefon — optional, nur wenn vorhanden
    phone = lead.get("contact_phone", lead.get("phone", ""))
    if phone:
        # Bereinige Telefonnummer für Notion (nur Zahlen, +, -, Leerzeichen)
        cleaned_phone = str(phone).strip()
        if cleaned_phone and len(cleaned_phone) >= 6:
            properties["Telefon"] = {"phone_number": cleaned_phone}

    try:
        notion_client.pages.create(
            parent={"database_id": database_id},
            properties=properties,
        )

        # Markiere den Lead in TinyDB als erfolgreich synchronisiert
        if LeadsTable:
            doc_ids = [
                doc.doc_id
                for doc in LeadsTable.search(
                    (LeadsTable.fragment({"name": lead.get("name")}))
                    & (LeadsTable.fragment({"timestamp": lead.get("timestamp")}))
                )
            ]
            if doc_ids:
                LeadsTable.update({"notion_synced": True}, doc_ids=doc_ids[:1])

        logger.info("Lead nach Notion synchronisiert: %s", lead.get("name"))

    except Exception as e:
        logger.error(
            "Notion-Sync fehlgeschlagen für '%s': %s",
            lead.get("name"),
            e,
        )
