"""
plugins/examples/notifications.py — Erweiterte Benachrichtigungen Plugin.

Fügt zusätzliche Telegram-Commands hinzu:
  /notify on|off  — Toggle Hunt-Benachrichtigungen
  /summary        — Tägliche Zusammenfassung
  /export         — CSV-Export der Leads

Demonstriert: get_commands(), get_message_handlers()
"""

import csv
import io
import logging
from datetime import datetime
from typing import Optional

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from plugins.base import LeadBotPlugin, PluginContext
from scraper import LeadsTable, get_stats

logger = logging.getLogger("leadbot.plugins.notifications")

NOTIFY_ENABLED: dict[int, bool] = {}


class NotificationsPlugin(LeadBotPlugin):
    """
    Erweiterte Benachrichtigungen und Export-Funktionen.

    Bietet zusätzliche Commands für:
      - Benachrichtigungs-Steuerung
      - Lead-Zusammenfassungen
      - CSV-Export
    """

    name = "notifications"
    version = "1.0.0"
    description = "Erweiterte Benachrichtigungen und CSV-Export"
    author = "LeadBot Team"

    def __init__(self):
        self._authorized_user_id: Optional[int] = None

    async def on_init(self, ctx: PluginContext):
        """Liest die autorisierte User-ID aus der Config."""
        import os
        user_id = os.getenv("AUTHORIZED_USER_ID")
        if user_id:
            self._authorized_user_id = int(user_id)
        logger.info("Notifications-Plugin initialisiert")

    def get_commands(self) -> list:
        """Registriert die Plugin-Commands."""
        return [
            CommandHandler("notify", self.cmd_notify),
            CommandHandler("summary", self.cmd_summary),
            CommandHandler("export", self.cmd_export),
        ]

    async def cmd_notify(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle Hunt-Benachrichtigungen."""
        user_id = update.effective_user.id

        if not context.args:
            current = NOTIFY_ENABLED.get(user_id, True)
            await update.message.reply_text(
                f"Benachrichtigungen sind {'AN' if current else 'AUS'}.\n"
                f"Usage: /notify on|off"
            )
            return

        action = context.args[0].lower()
        if action in ("on", "an", "enable"):
            NOTIFY_ENABLED[user_id] = True
            await update.message.reply_text("Benachrichtigungen aktiviert.")
        elif action in ("off", "aus", "disable"):
            NOTIFY_ENABLED[user_id] = False
            await update.message.reply_text("Benachrichtigungen deaktiviert.")
        else:
            await update.message.reply_text("Usage: /notify on|off")

    async def cmd_summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Zeigt eine detaillierte Lead-Zusammenfassung."""
        stats = get_stats()
        all_leads = LeadsTable.all()

        branches = {}
        cities = {}
        for lead in all_leads:
            branche = lead.get("branche", "unknown")
            stadt = lead.get("stadt", "unknown")
            branches[branche] = branches.get(branche, 0) + 1
            cities[stadt] = cities.get(stadt, 0) + 1

        top_branch = max(branches.items(), key=lambda x: x[1]) if branches else ("N/A", 0)
        top_city = max(cities.items(), key=lambda x: x[1]) if cities else ("N/A", 0)

        avg_score = 0
        if all_leads:
            scores = [l.get("design_score", 0) for l in all_leads]
            avg_score = sum(scores) / len(scores)

        await update.message.reply_text(
            f"📊 *Lead-Zusammenfassung*\n\n"
            f"📈 Gesamt: {stats['total']} Leads\n"
            f"📅 Heute: {stats['today']} Leads\n"
            f"🏆 Top-Branche: {top_branch[0]} ({top_branch[1]} Leads)\n"
            f"📍 Top-Stadt: {top_city[0]} ({top_city[1]} Leads)\n"
            f"⚠️ Ø Design-Score: {avg_score:.1f}\n"
            f"🔧 Branchen gescannt: {len(branches)}",
            parse_mode="Markdown",
        )

    async def cmd_export(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Exportiert Leads als CSV-Datei."""
        all_leads = LeadsTable.all()
        if not all_leads:
            await update.message.reply_text("Keine Leads zum Exportieren.")
            return

        output = io.StringIO()
        fieldnames = [
            "name", "website", "phone", "contact_name", "contact_phone",
            "contact_email", "branche", "stadt", "google_rating",
            "design_score", "reason_string", "timestamp",
        ]

        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for lead in all_leads:
            row = {k: lead.get(k, "") for k in fieldnames}
            writer.writerow(row)

        csv_data = output.getvalue()
        output.close()

        filename = f"leads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        await update.message.reply_document(
            document=io.BytesIO(csv_data.encode("utf-8")),
            filename=filename,
            caption=f"📁 {len(all_leads)} Leads exportiert",
        )
