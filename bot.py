"""
bot.py — Telegram-Bot für LeadBot-Steuerung

Verfügbare Commands:
  /find [Branche] [Stadt]  — Startet eine gezielte Suche
  /hunt [Stadt]            — Startet den automatischen 20-Branchen-Hunt
  /stats                   — Zeigt Lead-Statistiken an
  /best                    — Zeigt den Lead mit den meisten Problemen

Sicherheit: Alle Commands prüfen die AUTHORIZED_USER_ID aus .env.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import sys
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from scraper import (
    init_scraper_async,
    shutdown_scraper_async,
    hunt_leads,
    analyze_website_async,
    run_hunt_async,
    save_lead,
    HUNT_BRANCHES,
    get_stats,
    get_best_lead,
    LeadsTable,
    SEARCH_RADIUS_KM as SCRAPER_RADIUS,
)

logger = logging.getLogger("leadbot.bot")

# ─── Globale Zustandsvariablen ───────────────────────────────────────────────

AUTHORIZED_USER_ID: Optional[int] = None
active_searches: int = 0
hunt_in_progress: bool = False
hunt_progress_data: dict = {"completed": 0, "total": 0, "leads": 0}
hunt_chat_id: Optional[int] = None
hunt_app: Optional[Application] = None

RADIUS_CONFIG_PATH = Path(__file__).parent / "db" / "radius.json"
SEARCH_RADIUS_KM: int = 10

# Background-Mode Flag (gesetzt via argparse --background)
_HEADLESS_MODE: bool = False


def _load_radius() -> int:
    """Lädt den gespeicherten Radius aus der JSON-Datei."""
    global SEARCH_RADIUS_KM
    try:
        if RADIUS_CONFIG_PATH.exists():
            data = json.loads(RADIUS_CONFIG_PATH.read_text())
            SEARCH_RADIUS_KM = int(data.get("radius_km", 10))
    except Exception:
        SEARCH_RADIUS_KM = 10
    return SEARCH_RADIUS_KM


def _save_radius(radius: int):
    """Speichert den Radius in die JSON-Datei."""
    global SEARCH_RADIUS_KM
    SEARCH_RADIUS_KM = radius
    try:
        RADIUS_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        RADIUS_CONFIG_PATH.write_text(json.dumps({"radius_km": radius}))
    except Exception as e:
        logger.error("Radius speichern fehlgeschlagen: %s", e)


def init_bot():
    global AUTHORIZED_USER_ID
    user_id = os.getenv("AUTHORIZED_USER_ID")
    if user_id:
        AUTHORIZED_USER_ID = int(user_id)
        logger.info("Autorisierte User-ID gesetzt: %s", AUTHORIZED_USER_ID)
    else:
        logger.warning(
            "AUTHORIZED_USER_ID nicht gesetzt — "
            "der Bot wird auf ALLE Benutzer antworten (unsicher!)"
        )

    _load_radius()
    # Scraper-Modul synchronisieren
    import scraper
    scraper.SEARCH_RADIUS_KM = SEARCH_RADIUS_KM
    logger.info("Radius geladen: %dkm", SEARCH_RADIUS_KM)


def is_authorized(update: Update) -> bool:
    if AUTHORIZED_USER_ID is None:
        return True
    user_id = update.effective_user.id
    if user_id != AUTHORIZED_USER_ID:
        logger.warning("Unautorisierter Zugriffsversuch von User-ID %s", user_id)
        return False
    return True


async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_searches

    if not is_authorized(update):
        await update.message.reply_text("Zugriff verweigert.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: /find [Branche] [Stadt]\n"
            "Beispiel: /find dachdecker Berlin"
        )
        return

    branche = args[0]
    stadt = " ".join(args[1:])
    active_searches += 1

    await update.message.reply_text(
        f"Suche nach {branche} in {stadt}...\n"
        f"📍 Radius: {SEARCH_RADIUS_KM}km\n"
        f"(Google Maps + Verzeichnisse) Bitte warten."
    )

    try:
        results = await hunt_leads(branche, stadt, max_results=15, radius_km=SEARCH_RADIUS_KM)

        if not results:
            await update.message.reply_text("Keine Ergebnisse gefunden.")
            active_searches -= 1
            return

        await update.message.reply_text(f"🔍 {len(results)} Unternehmen gefunden. Analysiere jetzt...")
        processed = 0

        for biz in results:
            website = biz.get("website", "")

            if website:
                quality = await analyze_website_async(website, biz.get("name", ""))
            else:
                quality = {
                    "has_ssl": False, "has_viewport": False, "has_copyright": False,
                    "copyright_year": 0, "copyright_age": 0, "is_perfect": False,
                    "qualifies": True,
                    "issues": ["Keine Website gefunden"],
                    "reasons": ["Keine Website gefunden"],
                    "design_criticism": ["Keine Website vorhanden"],
                    "uses_table_layout": False, "uses_deprecated_html": False,
                    "uses_system_fonts": False,
                    "phone": "", "screenshot_path": "",
                    "impressum_data": {"name": "", "phone": "", "email": "", "impressum_url": ""},
                }

            impressum = quality.get("impressum_data", {})

            lead_data = {
                "name": biz.get("name", ""),
                "website": website,
                "phone": biz.get("phone", ""),
                "email": biz.get("email", ""),
                "address": biz.get("address", ""),
                "contact_name": impressum.get("name", ""),
                "contact_phone": impressum.get("phone", ""),
                "contact_email": impressum.get("email", ""),
                "impressum_url": impressum.get("impressum_url", ""),
                "google_rating": biz.get("rating", 0),
                "branche": branche,
                "stadt": stadt,
                "source": biz.get("source", "google"),
                "quality": quality,
            }

            saved = save_lead(lead_data)
            if saved:
                processed += 1
                reasons_str = ", ".join(quality.get("reasons", []))
                email_display = biz.get('email', '') or impressum.get('email', '') or 'N/A'

                # SOFORTIGE Telegram-Nachricht pro Lead
                message_text = (
                    f"🚀 *Neuer Lead gefunden!*\n\n"
                    f"🏢 *Firma:* {biz.get('name', 'Unbekannt')}\n"
                    f"🌐 *Web:* {website or 'N/A'}\n"
                    f"✉️ *Email:* {email_display}\n"
                    f"📞 *Tel:* {impressum.get('phone') or biz.get('phone', 'N/A')}\n"
                    f"👤 *Kontakt:* {impressum.get('name', 'N/A')}\n"
                    f"⭐ *Bewertung:* {biz.get('rating', 0)}\n"
                    f"⚠️ *Probleme:* {reasons_str}\n"
                    f"📍 *Quelle:* {biz.get('source', 'unknown')}"
                )
                await update.message.reply_text(message_text, parse_mode="Markdown")

        if processed == 0:
            await update.message.reply_text(
                "Alle gefundenen Websites sind einwandfrei oder haben "
                "zu wenige Probleme — keine qualifizierten Leads entdeckt."
            )
        else:
            await update.message.reply_text(f"✅ Fertig! {processed} Leads gespeichert.")

    except Exception as e:
        logger.error("Suche fehlgeschlagen: %s", e)
        await update.message.reply_text(f"Fehler bei der Suche: {e}")

    finally:
        active_searches -= 1


async def cmd_hunt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global hunt_in_progress, hunt_progress_data, hunt_chat_id, hunt_app

    if not is_authorized(update):
        await update.message.reply_text("Zugriff verweigert.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /hunt [Stadt]\n"
            "Beispiel: /hunt Berlin\n\n"
            f"Scannt {len(HUNT_BRANCHES)} Branchen nacheinander."
        )
        return

    if hunt_in_progress:
        await update.message.reply_text("Hunt läuft bereits. Bitte warten.")
        return

    stadt = " ".join(args)
    hunt_in_progress = True
    hunt_progress_data = {"completed": 0, "total": len(HUNT_BRANCHES), "leads": 0}
    hunt_chat_id = update.effective_chat.id
    hunt_app = context.application

    await update.message.reply_text(
        f"🔪 HUNT MODE gestartet für {stadt}\n"
        f"📍 Radius: {SEARCH_RADIUS_KM}km\n"
        f"Scanne {len(HUNT_BRANCHES)} Branchen...\n"
        f"Update nach jeder Branche."
    )

    async def progress_callback(completed, total, leads, branche, branch_leads, branch_skipped):
        global hunt_progress_data
        hunt_progress_data["completed"] = completed
        hunt_progress_data["total"] = total
        hunt_progress_data["leads"] = leads

        if hunt_chat_id and hunt_app:
            try:
                await hunt_app.bot.send_message(
                    chat_id=hunt_chat_id,
                    text=(
                        f"✅ Branche {completed}/{total}: {branche}\n"
                        f"  Leads: {branch_leads} Problemfälle\n"
                        f"  Aussortiert: {branch_skipped} (gute Website)\n"
                        f"  Gesamt: {leads} Leads"
                    ),
                )
            except Exception as e:
                logger.error("Hunt-Progress-Update fehlgeschlagen: %s", e)

    async def lead_callback(lead):
        """SOFORTIGE Telegram-Nachricht für jeden einzelnen Lead im Hunt-Mode."""
        if hunt_chat_id and hunt_app:
            try:
                reasons_str = lead.get("reason_string", "")
                email_display = lead.get("email", "") or lead.get("contact_email", "") or "N/A"
                await hunt_app.bot.send_message(
                    chat_id=hunt_chat_id,
                    text=(
                        f"🚀 *Neuer Lead im Hunt!*\n\n"
                        f"🏢 *Firma:* {lead.get('name', 'Unbekannt')}\n"
                        f"🌐 *Web:* {lead.get('website', 'N/A')}\n"
                        f"✉️ *Email:* {email_display}\n"
                        f"📞 *Tel:* {lead.get('contact_phone', lead.get('phone', 'N/A'))}\n"
                        f"👤 *Kontakt:* {lead.get('contact_name', 'N/A')}\n"
                        f"⭐ *Bewertung:* {lead.get('google_rating', 0)}\n"
                        f"⚠️ *Probleme:* {reasons_str}\n"
                        f"📍 *Branche:* {lead.get('branche', 'N/A')} | {lead.get('stadt', 'N/A')}"
                    ),
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error("Hunt-Lead-Benachrichtigung fehlgeschlagen: %s", e)

    try:
        result = await run_hunt_async(
            stadt,
            progress_callback=progress_callback,
            lead_callback=lead_callback,
            radius_km=SEARCH_RADIUS_KM,
        )

        await update.message.reply_text(
            f"🔪 HUNT ABGESCHLOSSEN\n\n"
            f"Branchen gescannt: {result['branches_completed']}/{result['branches_total']}\n"
            f"Problemfälle: {result['leads_exported']}\n"
            f"Aussortiert: {result['leads_skipped']} (gute Website)\n"
            f"Alle Leads in Notion exportiert."
        )

    except Exception as e:
        logger.error("Hunt fehlgeschlagen: %s", e)
        await update.message.reply_text(f"Hunt fehlgeschlagen: {e}")

    finally:
        hunt_in_progress = False
        hunt_chat_id = None
        hunt_app = None


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Zugriff verweigert.")
        return

    stats = get_stats()
    hunt_status = " (läuft)" if hunt_in_progress else ""

    await update.message.reply_text(
        f"📊 *Lead-Statistiken*\n"
        f"Heute: {stats['today']}\n"
        f"Gesamt: {stats['total']}\n"
        f"Hunt: {hunt_progress_data['completed']}/{hunt_progress_data['total']}{hunt_status}",
        parse_mode="Markdown",
    )


async def cmd_best(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Zugriff verweigert.")
        return

    best = get_best_lead()
    if not best:
        await update.message.reply_text("Keine Leads in der Datenbank.")
        return

    reasons = best.get("reason_string", best.get("reasons", []))
    if isinstance(reasons, list):
        reasons_str = ", ".join(reasons)
    else:
        reasons_str = reasons

    await update.message.reply_text(
        f"🎯 *Lead mit höchstem Potenzial*\n\n"
        f"🏢 {best.get('name', 'Unbekannt')}\n"
        f"🌐 {best.get('website', 'N/A')}\n"
        f"✉️ {best.get('email', 'N/A')}\n"
        f"📞 {best.get('contact_phone', best.get('phone', 'N/A'))}\n"
        f"👤 {best.get('contact_name', 'N/A')}\n"
        f"⭐ {best.get('google_rating', 0)}\n"
        f"⚠️ Score: {best.get('design_score', 0)}\n"
        f"📍 {best.get('stadt', 'N/A')} | {best.get('branche', 'N/A')}\n\n"
        f"Gründe: {reasons_str}",
        parse_mode="Markdown",
    )


async def cmd_radius(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Zugriff verweigert.")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            f"Aktueller Radius: {SEARCH_RADIUS_KM}km\n\n"
            "Usage: /radius {km}\n"
            "Beispiel: /radius 25"
        )
        return

    try:
        new_radius = int(args[0])
        if new_radius < 1 or new_radius > 100:
            await update.message.reply_text(
                "Radius muss zwischen 1 und 100 km liegen."
            )
            return

        _save_radius(new_radius)
        # Scraper-Modul synchronisieren
        import scraper
        scraper.SEARCH_RADIUS_KM = new_radius

        await update.message.reply_text(
            f"✅ Radius auf {new_radius}km gesetzt.\n"
            f"Nächste Suche nutzt diesen Umkreis."
        )

    except ValueError:
        await update.message.reply_text(
            "Ungültiger Wert. Bitte eine Zahl eingeben.\n"
            "Beispiel: /radius 25"
        )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        await update.message.reply_text("Zugriff verweigert.")
        return

    await update.message.reply_text(
        "Willkommen beim LeadBot!\n\n"
        "Commands:\n"
        "/find [Branche] [Stadt] — Google Maps + Verzeichnisse\n"
        "/hunt [Stadt] — Scannt 20 Branchen automatisch\n"
        "/radius {km} — Umkreis ändern (Standard: 10km)\n"
        "/stats — Statistiken anzeigen\n"
        "/best — Lead mit höchstem Potenzial\n\n"
        "Du kannst auch natürlich fragen, z.B.:\n"
        '"Wie viele Leads heute?"\n'
        '"Hunt Berlin"\n'
        '"Suche Elektriker in München"'
    )


async def handle_natural_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_authorized(update):
        return

    text = update.message.text.lower().strip()

    stats_patterns = [
        r"wie viele.*heute", r"leads.*heute", r"heute.*leads",
        r"statistik", r"stats", r"wieviele.*heute",
    ]
    for pattern in stats_patterns:
        if re.search(pattern, text):
            stats = get_stats()
            await update.message.reply_text(
                f"Heute: {stats['today']} Leads\nGesamt: {stats['total']} Leads"
            )
            return

    best_patterns = [
        r"bester.*lead", r"lead.*meisten.*fehler",
        r"highest.*potential", r"top.*lead", r"beste.*akquise",
    ]
    for pattern in best_patterns:
        if re.search(pattern, text):
            best = get_best_lead()
            if not best:
                await update.message.reply_text("Keine Leads in der Datenbank.")
                return
            reasons = best.get("reason_string", best.get("reasons", []))
            if isinstance(reasons, list):
                reasons_str = ", ".join(reasons)
            else:
                reasons_str = reasons
            await update.message.reply_text(
                f"Bester Lead: {best.get('name')}\n"
                f"Score: {best.get('design_score')} Fehler\n"
                f"Website: {best.get('website', 'N/A')}\n"
                f"Gründe: {reasons_str}"
            )
            return

    hunt_patterns = [r"hunt\s+([a-zäöüß\s]+)", r"jage\s+([a-zäöüß\s]+)", r"scan\s+([a-zäöüß\s]+)"]
    for pattern in hunt_patterns:
        match = re.search(pattern, text)
        if match:
            stadt = match.group(1).strip()
            context.args = stadt.split()
            await cmd_hunt(update, context)
            return

    search_patterns = [
        r"suche?\s+([a-zäöüß]+)\s+in\s+([a-zäöüß\s]+)",
        r"find\s+([a-zäöüß]+)\s+([a-zäöüß\s]+)",
    ]
    for pattern in search_patterns:
        match = re.search(pattern, text)
        if match:
            branche = match.group(1)
            stadt = match.group(2).strip()
            context.args = [branche] + stadt.split()
            await cmd_find(update, context)
            return


# ─── PTB v20+ Lifecycle Callbacks ────────────────────────────────────────────

async def on_bot_start(application: Application):
    """
    Wird von application.run_polling() aufgerufen NACHDEM der Event-Loop
    gestartet wurde aber BEVOR das Polling beginnt.

    Hier initialisieren wir den Browser — sicher im laufenden Loop.
    """
    global hunt_app
    hunt_app = application

    async def on_captcha(message: str):
        if hunt_chat_id and hunt_app:
            try:
                await hunt_app.bot.send_message(
                    chat_id=hunt_chat_id,
                    text=f"{message}",
                )
            except Exception as e:
                logger.error("Captcha-Benachrichtigung fehlgeschlagen: %s", e)

    await init_scraper_async(captcha_cb=on_captcha, headless=_HEADLESS_MODE)
    init_bot()
    logger.info("Browser initialisiert (headless=%s) — Bot bereit", _HEADLESS_MODE)


async def on_bot_stop(application: Application):
    """
    Wird von application.run_polling() aufgerufen BEVOR der Event-Loop
    geschlossen wird. Garantiert sauberer Shutdown.
    """
    await shutdown_scraper_async()
    logger.info("LeadBot heruntergefahren")


# ─── PID-Lock ────────────────────────────────────────────────────────────────

PID_FILE = Path(__file__).parent / ".bot.pid"


def acquire_pid_lock() -> bool:
    """
    Verhindert Doppelstart durch PID-Datei.

    Returns:
        True wenn Lock erfolgreich, False wenn bereits ein Bot läuft.
    """
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            os.kill(old_pid, 0)
            logger.error(
                "Bot läuft bereits (PID %s). "
                "PID-Datei löschen oder laufenden Prozess beenden.",
                old_pid,
            )
            return False
        except (ProcessLookupError, ValueError):
            logger.info("Stale PID-Datei gefunden (Prozess %s nicht aktiv) — überschreibe", old_pid)

    PID_FILE.write_text(str(os.getpid()))
    logger.info("PID-Lock gesetzt: %s", os.getpid())
    return True


def release_pid_lock():
    """Entfernt die PID-Datei beim sauberen Beenden."""
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
            logger.info("PID-Lock entfernt")
    except Exception as e:
        logger.debug("PID-Lock entfernen fehlgeschlagen: %s", e)


# ─── Entry Point ─────────────────────────────────────────────────────────────

def build_application() -> Application:
    """
    Baut die Application mit allen Handlern.

    Keine async-Operationen hier — nur Konfiguration.
    Browser-Init und Shutdown erfolgen über post_init/post_shutdown.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN nicht gesetzt")

    application = (
        Application.builder()
        .token(token)
        .post_init(on_bot_start)
        .post_shutdown(on_bot_stop)
        .build()
    )

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("find", cmd_find))
    application.add_handler(CommandHandler("hunt", cmd_hunt))
    application.add_handler(CommandHandler("radius", cmd_radius))
    application.add_handler(CommandHandler("stats", cmd_stats))
    application.add_handler(CommandHandler("best", cmd_best))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_natural_language)
    )

    logger.info("Telegram-Bot-Handler registriert")
    return application


def main():
    """
    Haupteinstiegspunkt mit PID-Lock, Signal-Handling, argparse und Conflict-Prävention.
    """
    global _HEADLESS_MODE

    parser = argparse.ArgumentParser(description="LeadBot — Telegram-gesteuerte Lead-Generierung")
    parser.add_argument(
        "--background", action="store_true",
        help="Startet Playwright im Headless-Modus (kein sichtbares Browser-Fenster)",
    )
    args = parser.parse_args()
    _HEADLESS_MODE = args.background

    if _HEADLESS_MODE:
        logger.info("Background-Mode aktiviert — Browser startet headless")

    if not acquire_pid_lock():
        sys.exit(1)

    application = build_application()

    def handle_signal(signum, frame):
        """
        Sauberer Shutdown bei SIGINT/SIGTERM.
        Ruft application.stop() und application.shutdown() auf.
        """
        sig_name = signal.Signals(signum).name
        logger.info("Signal %s empfangen — fahre Bot herunter...", sig_name)
        try:
            application.stop()
        except Exception as e:
            logger.debug("application.stop() Exception: %s", e)
        try:
            application.shutdown()
        except Exception as e:
            logger.debug("application.shutdown() Exception: %s", e)
        release_pid_lock()
        logger.info("Bot heruntergefahren")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info("Starte Telegram-Bot...")
    try:
        application.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error("Bot abgestürzt: %s", e)
    finally:
        release_pid_lock()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    main()
