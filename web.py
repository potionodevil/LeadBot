"""
web.py — FastAPI Web-Dashboard für LeadBot

Dieses Modul stellt ein Web-Interface bereit das alle gespeicherten Leads
als HTML-Tabelle anzeigt. Das Dashboard wird über http://localhost:8000
erreicht und zeigt:

  - Statistiken (aktive Suchen, heutige Leads, Gesamt-Leads, Hunt-Fortschritt)
  - Lead-Tabelle mit allen Details (Name, Website, Telefon, Design-Score, etc.)
  - Screenshot-Vorschau (klickbarer Pfad)
  - Design-Kritik (konkrete Gründe warum die Website Probleme hat)

Zusätzliche API-Endpunkte:
  GET /api/status — JSON-Status für externe Tools
"""

import logging
import os
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse

from scraper import get_all_leads, get_stats, get_hunt_progress

logger = logging.getLogger("leadbot.web")

# FastAPI-Application-Instanz
app = FastAPI(title="LeadBot Dashboard")

# Zähler für aktive Suchen (wird vom Bot-Prozess aktualisiert)
active_searches: int = 0


def set_active_searches(count: int):
    """
    Setzt den Zähler für aktive Suchen.

    Wird vom Bot-Prozess aufgerufen wenn eine Suche startet/endet.

    Args:
        count: Anzahl der gerade laufenden Suchen.
    """
    global active_searches
    active_searches = count


def get_active_searches() -> int:
    """
    Gibt die Anzahl der aktiven Suchen zurück.

    Returns:
        Anzahl der gerade laufenden Suchen.
    """
    return active_searches


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    """
    Hauptseite des Dashboards.

    Rendert eine vollständige HTML-Seite mit:
      - Header mit LeadBot-Titel und Zeitstempel
      - Statistik-Karten (aktive Suchen, heute, gesamt, Hunt)
      - Lead-Tabelle mit allen Spalten

    Returns:
        HTML-Antwort mit dem Dashboard.
    """
    leads = get_all_leads()
    stats = get_stats()
    hunt = get_hunt_progress()
    hunt_active = hunt.get("active", False)

    # HTML-Zeilen für jede Lead generieren
    rows = ""
    for lead in leads:
        # Design-Score: grün wenn 0, rot wenn > 0
        score_color = "green" if lead.get("design_score", 0) == 0 else "red"
        # Notion-Sync-Status
        synced = "Ja" if lead.get("notion_synced") else "Nein"
        # Zeitstempel kürzen
        timestamp = lead.get("timestamp", "")[:19]
        # Technische Gründe (SSL, Viewport, Copyright)
        reasons = lead.get("reason_string", "")
        # Visuelle Design-Kritik
        design_crit = lead.get("design_criticism_string", "")
        # Quelle der Lead-Findung
        source = lead.get("source", "")
        source_badge = "Google" if source == "google" else "Maps" if source == "google_maps" else "OSM"
        # Screenshot-Pfad
        screenshot_path = lead.get("screenshot_path", "")
        screenshot_display = (
            f'<a href="file://{screenshot_path}" target="_blank">📷 Screenshot</a>'
            if screenshot_path and os.path.exists(screenshot_path)
            else "—"
        )
        # Google-Bewertung
        rating = lead.get("google_rating", 0)
        rating_display = f"⭐ {rating}" if rating > 0 else "—"

        rows += f"""
        <tr>
            <td>{lead.get('name', '')}</td>
            <td><a href="{lead.get('website', '')}" target="_blank">{lead.get('website', '')}</a></td>
            <td>{lead.get('contact_phone', lead.get('phone', ''))}</td>
            <td>{lead.get('contact_name', '')}</td>
            <td>{lead.get('stadt', '')}</td>
            <td>{lead.get('branche', '')}</td>
            <td>{rating_display}</td>
            <td style="color: {score_color}; font-weight: bold;">{lead.get('design_score', 0)}</td>
            <td>{reasons}</td>
            <td style="font-size:0.8rem;color:#aaa;max-width:300px;">{design_crit}</td>
            <td>{screenshot_display}</td>
            <td><span style="opacity:0.5;font-size:0.75rem;">{source_badge}</span></td>
            <td>{synced}</td>
            <td>{timestamp}</td>
        </tr>
        """

    # Vollständige HTML-Seite zusammenbauen
    html = f"""
    <!DOCTYPE html>
    <html lang="de">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>LeadBot Dashboard</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: #0a0a0a;
                color: #e5e5e5;
                padding: 2rem;
            }}
            .header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 2rem;
                padding-bottom: 1rem;
                border-bottom: 1px solid #333;
            }}
            .header h1 {{
                font-size: 1.5rem;
                font-weight: 600;
            }}
            .stats {{
                display: flex;
                gap: 2rem;
                margin-bottom: 2rem;
                flex-wrap: wrap;
            }}
            .stat-card {{
                background: #1a1a1a;
                border: 1px solid #333;
                border-radius: 8px;
                padding: 1rem 1.5rem;
                min-width: 150px;
            }}
            .stat-card .label {{
                font-size: 0.75rem;
                color: #888;
                text-transform: uppercase;
                letter-spacing: 0.05em;
            }}
            .stat-card .value {{
                font-size: 2rem;
                font-weight: 700;
                margin-top: 0.25rem;
            }}
            .stat-card .value.active {{ color: #22c55e; }}
            .stat-card .value.total {{ color: #3b82f6; }}
            .stat-card .value.today {{ color: #f59e0b; }}
            table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 0.875rem;
            }}
            th {{
                text-align: left;
                padding: 0.75rem;
                border-bottom: 2px solid #333;
                color: #888;
                font-weight: 500;
                text-transform: uppercase;
                font-size: 0.7rem;
                letter-spacing: 0.05em;
                white-space: nowrap;
            }}
            td {{
                padding: 0.75rem;
                border-bottom: 1px solid #222;
                vertical-align: top;
            }}
            tr:hover {{ background: #111; }}
            a {{ color: #3b82f6; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
            .empty {{
                text-align: center;
                padding: 4rem;
                color: #666;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>LeadBot Dashboard</h1>
            <span style="color: #666; font-size: 0.875rem;">{stats['last_updated'][:19]}</span>
        </div>

        <div class="stats">
            <div class="stat-card">
                <div class="label">Aktive Suchen</div>
                <div class="value active">{get_active_searches()}</div>
            </div>
            <div class="stat-card">
                <div class="label">Heute</div>
                <div class="value today">{stats['today']}</div>
            </div>
            <div class="stat-card">
                <div class="label">Gesamt-Leads</div>
                <div class="value total">{stats['total']}</div>
            </div>
            <div class="stat-card">
                <div class="label">Hunt-Fortschritt</div>
                <div class="value {'today' if hunt_active else ''}">{hunt.get('completed', 0)}/{hunt.get('total', 0)}</div>
            </div>
        </div>

        {"<table><thead><tr><th>Name</th><th>Website</th><th>Telefon</th><th>Kontakt</th><th>Stadt</th><th>Branche</th><th>Bewertung</th><th>Score</th><th>Gründe</th><th>Design-Kritik</th><th>Screenshot</th><th>Quelle</th><th>Notion</th><th>Zeit</th></tr></thead><tbody>" + rows + "</tbody></table>" if rows else "<div class='empty'>Noch keine Leads vorhanden. Nutze /find oder /hunt um zu starten.</div>"}
    </body>
    </html>
    """

    return Response(content=html, media_type="text/html")


@app.get("/api/status")
async def status():
    """
    API-Endpunkt für JSON-Status-Abfragen.

    Wird von externen Tools oder dem Bot-Prozess genutzt um den
    aktuellen Status abzufragen.

    Returns:
        JSON mit active_searches, total_leads, today_leads,
        last_updated und hunt-Fortschritt.
    """
    stats = get_stats()
    hunt = get_hunt_progress()
    return {
        "active_searches": get_active_searches(),
        "total_leads": stats["total"],
        "today_leads": stats["today"],
        "last_updated": stats["last_updated"],
        "hunt": hunt,
    }


def get_app():
    """
    Gibt die FastAPI-Application-Instanz zurück.

    Wird von run.py (uvicorn) verwendet um den Webserver zu starten.

    Returns:
        Die FastAPI-Application-Instanz.
    """
    logger.info("FastAPI-App initialisiert")
    return app
