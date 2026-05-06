"""
plugins/base.py — Abstract base class for LeadBot plugins.

Alle Plugins erben von LeadBotPlugin und implementieren die
async Lifecycle-Methoden. Plugins können:
  - Telegram-Command-Handler registrieren
  - Scraper-Hooks hinzufügen (vor/nach Analyse)
  - Eigene Async-Tasks starten
  - Auf Bot-Events reagieren
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from telegram.ext import Application
    from scraper import AsyncBrowserManager


class LeadBotPlugin(ABC):
    """
    Basis-Klasse für alle LeadBot-Plugins.

    Jedes Plugin muss mindestens `name` und `version` definieren.
    Optional können Lifecycle-Methoden und Hooks implementiert werden.

    Example:
        class MyPlugin(LeadBotPlugin):
            name = "mein_plugin"
            version = "1.0.0"

            async def on_init(self, ctx):
                await ctx.application.bot.send_message(
                    chat_id=123, text="Plugin geladen!"
                )

            def get_commands(self):
                return [CommandHandler("mycmd", self.handle_mycmd)]
    """

    name: str = "unnamed_plugin"
    version: str = "0.0.0"
    description: str = ""
    author: str = ""

    async def on_init(self, ctx: "PluginContext"):
        """
        Wird aufgerufen wenn das Plugin geladen wird.
        Hier können Ressourcen initialisiert werden.

        Args:
            ctx: PluginContext mit Zugriff auf Bot, Browser, DB.
        """
        pass

    async def on_shutdown(self, ctx: "PluginContext"):
        """
        Wird aufgerufen wenn der Bot herunterfährt.
        Hier sollten Ressourcen sauber freigegeben werden.

        Args:
            ctx: PluginContext mit Zugriff auf Bot, Browser, DB.
        """
        pass

    def get_commands(self) -> list:
        """
        Returns eine Liste von CommandHandler-Instanzen die
        vom Bot registriert werden sollen.

        Returns:
            Liste von telegram.ext.Handler-Instanzen.
        """
        return []

    def get_message_handlers(self) -> list:
        """
        Returns eine Liste von MessageHandler-Instanzen für
        nicht-Command-Nachrichten.

        Returns:
            Liste von telegram.ext.Handler-Instanzen.
        """
        return []

    async def on_lead_found(self, ctx: "PluginContext", lead_data: dict) -> Optional[dict]:
        """
        Hook: Wird aufgerufen wenn ein neuer Lead gefunden wurde.
        Kann lead_data modifizieren oder None zurückgeben um
        den Lead zu verwerfen.

        Args:
            ctx: PluginContext.
            lead_data: Die Lead-Daten aus der Analyse.

        Returns:
            Modifizierte lead_data oder None zum Verwerfen.
        """
        return lead_data

    async def on_search_complete(
        self, ctx: "PluginContext", branche: str, stadt: str, results: list[dict]
    ) -> list[dict]:
        """
        Hook: Wird aufgerufen nachdem eine Google-Suche abgeschlossen ist.
        Kann die Ergebnisse filtern, erweitern oder neu sortieren.

        Args:
            ctx: PluginContext.
            branche: Die durchsuchte Branche.
            stadt: Die durchsuchte Stadt.
            results: Liste der gefundenen Unternehmen.

        Returns:
            Modifizierte Liste der Ergebnisse.
        """
        return results

    async def on_hunt_branch_complete(
        self, ctx: "PluginContext", branche: str, stadt: str,
        leads: int, skipped: int
    ):
        """
        Hook: Wird aufgerufen nachdem eine Branche im Hunt-Mode
        abgeschlossen wurde.

        Args:
            ctx: PluginContext.
            branche: Die abgeschlossene Branche.
            stadt: Die Zielstadt.
            leads: Anzahl gefundener Leads.
            skipped: Anzahl aussortierter Websites.
        """
        pass


class PluginContext:
    """
    Kontext-Objekt das Plugins Zugriff auf die LeadBot-Komponenten gibt.

    Attributes:
        application: Die telegram.ext.Application-Instanz.
        browser_manager: Der AsyncBrowserManager für Playwright-Zugriff.
        leads_table: Die TinyDB-Tabelle für Lead-Zugriff.
        config: Plugin-spezifische Konfiguration aus .env oder Config-Datei.
    """

    def __init__(
        self,
        application,
        browser_manager: Optional["AsyncBrowserManager"] = None,
        leads_table=None,
        config: Optional[dict] = None,
    ):
        self.application = application
        self.browser_manager = browser_manager
        self.leads_table = leads_table
        self.config = config or {}
