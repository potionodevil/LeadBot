# 🤖 LeadBot — Automatisierte Lead-Generierung für Webagenturen

> Ein intelligenter Telegram-Bot, der automatisch potenzielle Kunden findet, deren Websites analysiert und qualifizierte Leads direkt an Notion sendet.

[![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python)](https://python.org)
[![Playwright](https://img.shields.io/badge/Playwright-1.49+-green?logo=playwright)](https://playwright.dev)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-teal?logo=fastapi)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ✨ Features

| Feature | Beschreibung |
|---------|-------------|
| 🔍 **Multi-Source Scraping** | Durchsucht Google Maps, Gelbe Seiten & Das Örtliche gleichzeitig |
| 🧠 **Website-Analyse** | Erkennt veraltete Designs, fehlendes SSL, mobiles Layout, Copyright-Jahre |
| 🚀 **Sofort-Benachrichtigung** | Jeder gefundene Lead wird sofort per Telegram-Nachricht gemeldet |
| 📝 **Notion-Sync** | Leads landen automatisch in deiner Notion-Datenbank |
| 🛡️ **Duplikat-Schutz** | Prüft vor dem Speichern, ob der Lead bereits in Notion existiert |
| 📊 **Web-Dashboard** | Übersicht aller Leads unter `http://localhost:8000` |
| 🌙 **Background-Modus** | Unsichtbarer Headless-Betrieb via `--background` |
| 🔄 **Hunt-Mode** | Scannt automatisch 20 Handwerker-Branchen in einer Stadt |

---

## 🏗️ Architektur

```
┌─────────────┐     Telegram      ┌─────────────┐
│   Dein Handy │  ─────────────>  │   bot.py    │
│  (Telegram)  │  <─────────────  │  (Commands) │
└─────────────┘                   └──────┬──────┘
                                         │
                    ┌────────────────────┼────────────────────┐
                    ▼                    ▼                    ▼
            ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
            │ Google Maps  │    │Gelbe Seiten  │    │Das Örtliche  │
            │  (Playwright)│    │  (Base64-Fix)│    │  (Playwright)│
            └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
                   │                   │                   │
                   └───────────────────┼───────────────────┘
                                       ▼
                            ┌─────────────────┐
                            │  Website-Analyse │
                            │  (SSL, Viewport, │
                            │   Design, Email) │
                            └────────┬────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
            ┌────────────┐  ┌────────────┐  ┌────────────┐
            │  Telegram  │  │   Notion   │  │   CSV      │
            │  (Sofort)  │  │  (API)     │  │  (Backup)  │
            └────────────┘  └────────────┘  └────────────┘
```

---

## 📋 Voraussetzungen

- **Python 3.12+**
- **Playwright Browser** (Chromium)
- **Telegram Bot Token** (via [@BotFather](https://t.me/botfather))
- **Notion Integration** (optional, für Sync)

---

## 🚀 Installation

### 1. Repository klonen

```bash
git clone <repository-url>
cd LeadBot
```

### 2. Virtuelle Umgebung erstellen

```bash
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# oder: .venv\Scripts\activate  # Windows
```

### 3. Abhängigkeiten installieren

```bash
pip install -r requirements.txt
playwright install chromium
```

### 4. Umgebungsvariablen konfigurieren

```bash
cp .env.example .env
nano .env  # oder dein Editor
```

**Erforderliche Variablen:**

| Variable | Beschreibung | Woher? |
|----------|-------------|--------|
| `TELEGRAM_BOT_TOKEN` | Token deines Telegram-Bots | [@BotFather](https://t.me/botfather) |
| `AUTHORIZED_USER_ID` | Deine Telegram User-ID | [@userinfobot](https://t.me/userinfobot) |

**Optionale Variablen (Notion-Sync):**

| Variable | Beschreibung |
|----------|-------------|
| `NOTION_API_KEY` | Internal Integration Token |
| `NOTION_DATABASE_ID` | ID deiner Notion-Datenbank |

> ⚠️ **Sicherheitshinweis:** Die `.env`-Datei enthält sensible Daten und ist in `.gitignore` aufgeführt. Gib deine Tokens niemals öffentlich preis!

---

## 🎮 Nutzung

### Standard-Start (mit sichtbarem Browser)

```bash
python3 run.py
```

Öffnet ein Chromium-Fenster für den Browser-Scraper. Ideal für:
- Erstes Setup
- Captcha-Lösung
- Debugging

### Background-Start (unsichtbar, headless)

```bash
python3 run.py --background
# oder kurz:
python3 run.py -b
```

Läuft komplett im Hintergrund ohne Browser-Fenster. Perfekt für:
- 24/7-Server
- Automatisierte Hunts
- Headless-Umgebungen

### Nur der Bot (ohne Web-Dashboard)

```bash
python3 bot.py
# oder im Hintergrund:
python3 bot.py --background
```

---

## 🤖 Telegram-Commands

| Command | Beschreibung | Beispiel |
|---------|-------------|----------|
| `/start` | Willkommensnachricht & Hilfe | `/start` |
| `/find` | Gezielte Suche nach Branche & Stadt | `/find dachdecker berlin` |
| `/hunt` | Automatischer 20-Branchen-Hunt | `/hunt münchen` |
| `/radius` | Suchradius ändern (km) | `/radius 25` |
| `/stats` | Lead-Statistiken anzeigen | `/stats` |
| `/best` | Lead mit den meisten Problemen | `/best` |

### Natürliche Sprache

Der Bot versteht auch natürliche Anfragen:

- *"Suche Elektriker in Hamburg"*
- *"Hunt Stuttgart"*
- *"Wie viele Leads heute?"*
- *"Bester Lead"*

---

## 🗃️ Notion-Datenbank

Erstelle eine Notion-Datenbank mit diesen exakten Spaltennamen:

| Spalte | Typ | Beschreibung |
|--------|-----|-------------|
| **Firmenname** | `Title` | Name des Unternehmens |
| **Name** | `Rich Text` | Ansprechpartner aus Impressum |
| **Email** | `Email` | Gefundene E-Mail-Adresse |
| **Telefon** | `Phone Number` | Telefonnummer |
| **Probleme** | `Rich Text` | Analyse-Ergebnisse (SSL, Viewport, etc.) |
| **Website** | `URL` | Firmenwebsite |
| **Status** | `Status` | Automatisch auf `"Neu"` gesetzt |

> **Hinweis:** Der Bot prüft vor jedem Upload, ob der Lead bereits existiert (Duplikat-Schutz via Firmenname + Website).

---

## 📁 Verzeichnisstruktur

```
LeadBot/
├── bot.py              # Telegram-Bot mit Commands & Logik
├── scraper.py          # Playwright-Scraper (Maps, Gelbe Seiten, Örtliche)
├── notion_db.py        # Notion-API-Integration & Duplikat-Check
├── web.py              # FastAPI-Dashboard (localhost:8000)
├── run.py              # Prozess-Manager (Bot + Webserver)
├── requirements.txt    # Python-Abhängigkeiten
├── .env.example        # Beispiel-Konfiguration (ohne echte Tokens!)
├── .gitignore          # Ausgeschlossene Dateien
├── db/                 # TinyDB-Datenbank (lokal, .gitignore)
├── screenshots/        # Website-Screenshots (.gitignore)
├── browser_session/    # Playwright-Cookies & Cache (.gitignore)
└── backup_leads.csv    # Automatisches CSV-Backup (.gitignore)
```

---

## ⚙️ Konfiguration

### Suchradius anpassen

Standardmäßig sucht der Bot im Umkreis von **10 km**. Ändere ihn via Telegram:

```
/radius 50
```

Der Wert wird in `db/radius.json` persistiert.

### Captcha-Handling

Wenn Google ein Captcha anzeigt:
1. Der Bot pausiert automatisch
2. Du erhältst eine Telegram-Nachricht: *"⚠️ CAPTCHA! Bitte am PC lösen."*
3. Löse das Captcha im Browser-Fenster
4. Drücke **Enter** im Terminal, um fortzufahren

> Im Background-Modus (`--background`) ist die Captcha-Lösung über AnyDesk/VNC empfohlen.

---

## 🛠️ Tech Stack

| Technologie | Verwendung |
|-------------|-----------|
| **Python 3.12+** | Kernsprache |
| **Playwright** | Browser-Automatisierung (Chromium) |
| **python-telegram-bot** | Telegram-Bot-Framework |
| **FastAPI** | Web-Dashboard |
| **TinyDB** | Lokale JSON-Datenbank |
| **Notion Client** | Notion-API-Integration |
| **BeautifulSoup4** | HTML-Parsing & Website-Analyse |

---

## 🔒 Sicherheit & Datenschutz

- **Keine Secrets im Repo:** `.env`, `*.csv`, Screenshots und Session-Daten sind in `.gitignore`
- **Duplikat-Schutz:** Verhindert doppelte Einträge in Notion
- **Autorisierung:** Nur deine Telegram User-ID kann Commands ausführen
- **Tab-Isolierung:** Jeder Website-Scan läuft in einem separaten Browser-Tab

---

## 📝 Lizenz

MIT License — siehe [LICENSE](LICENSE) für Details.

---

## 🙋 Support

Bei Problemen oder Fragen:
1. Prüfe die Logs im Terminal
2. Verifiziere deine `.env`-Konfiguration
3. Stelle sicher, dass Playwright installiert ist: `playwright install chromium`

---

<p align="center">
  <sub>Built with caffeine & automation 🤖☕</sub>
</p>
