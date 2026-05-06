"""
plugins/__init__.py — Plugin-Registry und Loader für LeadBot.

Entdeckt, lädt und verwaltet Plugins über:
  1. Python Entry Points (pip-installable Plugins)
  2. Dateibasierte Plugins im plugins/ Verzeichnis
  3. Programmatische Registrierung

Plugins werden im Event-Loop initialisiert und erhalten
Zugriff auf Bot, Browser und Datenbank via PluginContext.

Usage:
    from plugins import PluginRegistry

    registry = PluginRegistry()
    await registry.load_builtin_plugins()
    await registry.load_entrypoint_plugins()
    await registry.init_all(context)

    # Handler registrieren
    for handler in registry.get_all_handlers():
        application.add_handler(handler)
"""

import importlib
import logging
import os
import pkgutil
from pathlib import Path
from typing import Optional

from .base import LeadBotPlugin, PluginContext

logger = logging.getLogger("leadbot.plugins")


class PluginRegistry:
    """
    Zentrale Registry für alle LeadBot-Plugins.

    Verantwortlich für:
      - Discovery (Entry Points + Dateisystem)
      - Lifecycle-Management (init/shutdown)
      - Handler-Aggregation für den Bot
      - Hook-Ausführung
    """

    def __init__(self, plugins_dir: Optional[str] = None):
        self._plugins: dict[str, LeadBotPlugin] = {}
        self._plugins_dir = plugins_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "examples"
        )
        self._context: Optional[PluginContext] = None

    @property
    def loaded_plugins(self) -> dict[str, LeadBotPlugin]:
        """Returns alle geladenen Plugins als Dictionary."""
        return dict(self._plugins)

    @property
    def plugin_count(self) -> int:
        """Anzahl geladener Plugins."""
        return len(self._plugins)

    def register(self, plugin: LeadBotPlugin) -> None:
        """
        Registriert ein Plugin programmatisch.

        Args:
            plugin: Eine Instanz einer LeadBotPlugin-Subklasse.
        """
        if plugin.name in self._plugins:
            logger.warning(
                "Plugin '%s' bereits registriert — wird überschrieben",
                plugin.name,
            )
        self._plugins[plugin.name] = plugin
        logger.info(
            "Plugin registriert: %s v%s",
            plugin.name, plugin.version,
        )

    async def load_builtin_plugins(self) -> None:
        """
        Lädt alle Plugins aus dem plugins/examples/ Verzeichnis.

        Durchsucht das Verzeichnis nach Python-Modulen die
        LeadBotPlugin-Subklassen enthalten.
        """
        plugins_path = Path(self._plugins_dir)
        if not plugins_path.exists():
            logger.debug("Plugins-Verzeichnis nicht gefunden: %s", self._plugins_dir)
            return

        logger.info("Lade builtin Plugins aus: %s", self._plugins_dir)

        for module_path in plugins_path.glob("*.py"):
            if module_path.name.startswith("_"):
                continue

            module_name = f"plugins.examples.{module_path.stem}"
            try:
                module = importlib.import_module(module_name)
                plugins_found = self._extract_plugins_from_module(module)
                if plugins_found:
                    logger.info(
                        "  %d Plugin(s) geladen aus: %s",
                        plugins_found, module_name,
                    )
            except Exception as e:
                logger.error("Fehler beim Laden von %s: %s", module_name, e)

    async def load_entrypoint_plugins(self) -> None:
        """
        Lädt Plugins über Python Entry Points.

        Entry Points müssen in der setup.py/pyproject.toml des
        Plugin-Pakets definiert sein:

            [project.entry-points."leadbot.plugins"]
            my_plugin = my_package.plugins:MyPlugin

        Dies ermöglicht pip-installable Plugins die automatisch
        entdeckt werden.
        """
        try:
            from importlib.metadata import entry_points
        except ImportError:
            from importlib_metadata import entry_points

        try:
            eps = entry_points(group="leadbot.plugins")
        except TypeError:
            eps = entry_points().get("leadbot.plugins", [])

        if not eps:
            logger.debug("Keine Entry-Point-Plugins gefunden")
            return

        logger.info("Lade Entry-Point-Plugins...")
        for ep in eps:
            try:
                plugin_class = ep.load()
                if issubclass(plugin_class, LeadBotPlugin):
                    plugin = plugin_class()
                    self.register(plugin)
                    logger.info("  Entry-Point-Plugin geladen: %s", ep.name)
            except Exception as e:
                logger.error("Fehler beim Laden von Entry-Point '%s': %s", ep.name, e)

    async def init_all(self, context: PluginContext) -> None:
        """
        Initialisiert alle registrierten Plugins.

        Ruft on_init() für jedes Plugin auf und übergibt
        den PluginContext.

        Args:
            context: PluginContext mit Bot, Browser, DB Zugriff.
        """
        self._context = context
        logger.info("Initialisiere %d Plugin(s)...", len(self._plugins))

        for name, plugin in self._plugins.items():
            try:
                await plugin.on_init(context)
                logger.info("  Plugin initialisiert: %s v%s", name, plugin.version)
            except Exception as e:
                logger.error("Fehler bei Initialisierung von '%s': %s", name, e)

    async def shutdown_all(self) -> None:
        """
        Fährt alle Plugins sauber herunter.

        Ruft on_shutdown() für jedes Plugin auf.
        """
        if not self._context:
            return

        logger.info("Fahre %d Plugin(s) herunter...", len(self._plugins))

        for name, plugin in self._plugins.items():
            try:
                await plugin.on_shutdown(self._context)
                logger.info("  Plugin heruntergefahren: %s", name)
            except Exception as e:
                logger.error("Fehler beim Shutdown von '%s': %s", name, e)

    def get_all_handlers(self) -> list:
        """
        Sammelt alle Command- und Message-Handler von allen Plugins.

        Returns:
            Liste aller Handler die vom Bot registriert werden sollen.
        """
        handlers = []
        for name, plugin in self._plugins.items():
            try:
                commands = plugin.get_commands()
                handlers.extend(commands)
                if commands:
                    logger.debug(
                        "  %d Command-Handler von: %s",
                        len(commands), name,
                    )

                messages = plugin.get_message_handlers()
                handlers.extend(messages)
                if messages:
                    logger.debug(
                        "  %d Message-Handler von: %s",
                        len(messages), name,
                    )
            except Exception as e:
                logger.error("Fehler beim Laden der Handler von '%s': %s", name, e)

        return handlers

    async def execute_hook(self, hook_name: str, *args, **kwargs):
        """
        Führt einen Hook über alle Plugins aus die ihn implementieren.

        Args:
            hook_name: Name des Hooks (z.B. "on_lead_found").
            *args: Positionale Argumente für den Hook.
            **kwargs: Keyword-Argumente für den Hook.

        Returns:
            Liste der Ergebnisse aller Plugin-Hooks.
        """
        results = []
        for name, plugin in self._plugins.items():
            try:
                hook = getattr(plugin, hook_name, None)
                if hook and callable(hook):
                    result = await hook(self._context, *args, **kwargs)
                    results.append((name, result))
            except Exception as e:
                logger.error("Fehler im Hook '%s' von '%s': %s", hook_name, name, e)

        return results

    def _extract_plugins_from_module(self, module) -> int:
        """
        Extrahiert LeadBotPlugin-Subklassen aus einem Modul.

        Args:
            module: Das importierte Python-Modul.

        Returns:
            Anzahl gefundener und registrierter Plugins.
        """
        count = 0
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, LeadBotPlugin)
                and attr is not LeadBotPlugin
                and not attr_name.startswith("_")
            ):
                try:
                    plugin = attr()
                    self.register(plugin)
                    count += 1
                except Exception as e:
                    logger.error(
                        "Fehler beim Instanziieren von '%s': %s",
                        attr_name, e,
                    )
        return count


# Globale Registry-Instanz für einfachen Zugriff
registry = PluginRegistry()
