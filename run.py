"""
run.py — Prozess-Manager für LeadBot

Dieses Skript startet und verwaltet die beiden LeadBot-Prozesse:
  1. Telegram-Bot (bot.py) — Steuert den Bot über Telegram
  2. FastAPI Webserver (web.py) — Dashboard unter http://localhost:8000

Beide Prozesse werden als Subprozesse gestartet und überwacht.
Bei einem Absturz eines Prozesses wird der andere sauber beendet.

Signal-Handling:
  - SIGINT (Ctrl+C): Sauberer Shutdown beider Prozesse
  - SIGTERM: Sauberer Shutdown beider Prozesse

Die .env-Datei wird beim Start geladen und alle erforderlichen
Umgebungsvariablen werden geprüft.
"""

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Lade Umgebungsvariablen aus .env-Datei
load_dotenv()

# Logging-Konfiguration für den Prozess-Manager
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("leadbot")

# ─── Pfade ───────────────────────────────────────────────────────────────────

# Projekt-Verzeichnis (wo run.py liegt)
PROJECT_DIR = Path(__file__).parent.resolve()
# Pfad zum Bot-Skript
BOT_SCRIPT = PROJECT_DIR / "bot.py"
# Pfad zum Web-Skript
WEB_SCRIPT = PROJECT_DIR / "web.py"
# Ordner für Screenshots
SCREENSHOTS_DIR = PROJECT_DIR / "leads_screenshots"
# Datenbank-Verzeichnis
DB_DIR = PROJECT_DIR / "db"

# Globale Referenzen auf die Subprozesse
bot_process: subprocess.Popen | None = None
web_process: subprocess.Popen | None = None


def ensure_directories():
    """
    Erstellt alle erforderlichen Verzeichnisse falls sie nicht existieren.

    Verzeichnisse:
      - db/ — Für die TinyDB-Datenbank
      - leads_screenshots/ — Für Website-Screenshots
    """
    os.makedirs(str(DB_DIR), exist_ok=True)
    os.makedirs(str(SCREENSHOTS_DIR), exist_ok=True)
    logger.info("Verzeichnisse geprüft: db/, leads_screenshots/")


def check_env():
    """
    Prüft ob alle erforderlichen Umgebungsvariablen gesetzt sind.

    Erforderliche Variablen:
      - TELEGRAM_BOT_TOKEN: Token des Telegram-Bots
      - AUTHORIZED_USER_ID: User-ID des autorisierten Benutzers

    Optionale Variablen (Warnung wenn fehlend):
      - NOTION_API_KEY: Notion-API-Key
      - NOTION_DATABASE_ID: Notion-Datenbank-ID

    Returns:
        True wenn alle erforderlichen Variablen gesetzt sind.
    """
    required_vars = ["TELEGRAM_BOT_TOKEN", "AUTHORIZED_USER_ID"]
    missing = [v for v in required_vars if not os.getenv(v)]

    if missing:
        logger.warning(
            "Fehlende Umgebungsvariablen: %s — "
            "Bitte in .env-Datei eintragen",
            ", ".join(missing),
        )
        return False

    # Optionale Variablen prüfen
    optional_vars = ["NOTION_API_KEY", "NOTION_DATABASE_ID"]
    missing_optional = [v for v in optional_vars if not os.getenv(v)]
    if missing_optional:
        logger.info(
            "Optionale Variablen nicht gesetzt: %s — "
            "Notion-Sync ist deaktiviert",
            ", ".join(missing_optional),
        )

    return True


def start_bot(background: bool = False):
    """
    Startet den Telegram-Bot als separaten Subprozess.

    Der Bot läuft in einem eigenen Python-Prozess damit er unabhängig
    vom Webserver arbeiten kann. Der Prozess wird überwacht und bei
    einem Absturz wird das Hauptskript benachrichtigt.

    Der Bot-Prozess:
      - Lädt .env-Variablen
      - Initialisiert den Playwright-Browser
      - Startet den Telegram-Polling-Loop

    Args:
        background: Wenn True, wird der Browser im Headless-Modus gestartet.
    """
    global bot_process

    if background:
        logger.info("Starte Telegram-Bot-Prozess (BACKGROUND-MODUS / headless)...")
    else:
        logger.info("Starte Telegram-Bot-Prozess...")

    cmd = [sys.executable, str(BOT_SCRIPT)]
    if background:
        cmd.append("--background")

    bot_process = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_DIR),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    logger.info("Bot-Prozess gestartet (PID: %s)", bot_process.pid)


def start_web():
    """
    Startet den FastAPI-Webserver als separaten Subprozess.

    Der Webserver läuft auf Port 8000 und ist unter
    http://localhost:8000 erreichbar.

    Der Webserver-Prozess:
      - Lädt .env-Variablen
      - Startet uvicorn mit dem FastAPI-App-Objekt aus web.py
      - Bindet an 0.0.0.0:8000 (lokal und im Netzwerk erreichbar)
    """
    global web_process

    logger.info("Starte FastAPI-Webserver auf Port 8000...")

    web_process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "web:app",
            "--host", "0.0.0.0",
            "--port", "8000",
            "--log-level", "info",
        ],
        cwd=str(PROJECT_DIR),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    logger.info("Webserver gestartet (PID: %s)", web_process.pid)


def shutdown(signum, frame):
    """
    Sauberer Shutdown aller Prozesse.

    Wird bei SIGINT (Ctrl+C) oder SIGTERM aufgerufen.
    Beendet zuerst den Bot-Prozess, dann den Webserver-Prozess.
    Jeder Prozess bekommt 5 Sekunden Zeit zum Herunterfahren
    bevor er zwangsweise beendet wird.

    Args:
        signum: Das empfangene Signal.
        frame: Der aktuelle Stack-Frame.
    """
    logger.info(
        "Shutdown-Signal empfangen (%s)...",
        signal.Signals(signum).name,
    )

    global bot_process, web_process

    # Bot-Prozess beenden
    if bot_process:
        logger.info("Beende Bot-Prozess (PID: %s)...", bot_process.pid)
        bot_process.terminate()
        try:
            bot_process.wait(timeout=5)
            logger.info("Bot-Prozess beendet")
        except subprocess.TimeoutExpired:
            bot_process.kill()
            logger.warning("Bot-Prozess zwangsweise beendet")

    # Webserver-Prozess beenden
    if web_process:
        logger.info("Beende Webserver-Prozess (PID: %s)...", web_process.pid)
        web_process.terminate()
        try:
            web_process.wait(timeout=5)
            logger.info("Webserver-Prozess beendet")
        except subprocess.TimeoutExpired:
            web_process.kill()
            logger.warning("Webserver-Prozess zwangsweise beendet")

    logger.info("Alle Prozesse beendet. Auf Wiedersehen.")
    sys.exit(0)


def main():
    """
    Hauptfunktion des Prozess-Managers.

    Ablauf:
      1. CLI-Argumente parsen (--background)
      2. Erforderliche Verzeichnisse erstellen
      3. Umgebungsvariablen prüfen
      4. Signal-Handler registrieren
      5. Bot und Webserver starten
      6. Auf Prozess-Ende warten (mit Überwachung)
      7. Bei Absturz: Sauber herunterfahren
    """
    parser = argparse.ArgumentParser(description="LeadBot — Prozess-Manager")
    parser.add_argument(
        "--background", "-b", action="store_true",
        help="Startet den Bot im Hintergrund (Playwright headless=True, kein Browser-Fenster)",
    )
    args = parser.parse_args()

    # Signal-Handler für sauberen Shutdown registrieren
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("=" * 60)
    if args.background:
        logger.info("LeadBot — Starte alle Dienste (BACKGROUND-MODUS)")
    else:
        logger.info("LeadBot — Starte alle Dienste")
    logger.info("=" * 60)

    # Verzeichnisse erstellen
    ensure_directories()

    # Umgebungsvariablen prüfen
    if not check_env():
        logger.error("Erforderliche Umgebungsvariablen fehlen — Abbruch")
        return

    # Bot-Prozess starten (mit oder ohne --background)
    start_bot(background=args.background)

    # Kurze Pause damit der Bot den Browser starten kann
    time.sleep(3)

    # Webserver-Prozess starten
    start_web()

    logger.info("Alle Dienste laufen. Dashboard: http://localhost:8000")
    logger.info("Drücke Ctrl+C zum Beenden.")

    # Hauptschleife: Überwache beide Prozesse
    try:
        while True:
            time.sleep(1)

            # Prüfe ob Bot-Prozess noch läuft
            if bot_process and bot_process.poll() is not None:
                logger.error(
                    "Bot-Prozess unerwartet beendet (Code: %s)",
                    bot_process.returncode,
                )
                break

            # Prüfe ob Webserver-Prozess noch läuft
            if web_process and web_process.poll() is not None:
                logger.error(
                    "Webserver-Prozess unerwartet beendet (Code: %s)",
                    web_process.returncode,
                )
                break

    except KeyboardInterrupt:
        shutdown(signal.SIGINT, None)


if __name__ == "__main__":
    main()
