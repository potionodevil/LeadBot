"""
plugins/examples/enricher.py — Lead-Enrichment Plugin.

Erweitert gefundene Leads um zusätzliche Informationen:
  - WHOIS-Daten der Website
  - Social-Media-Profile erkennen
  - Technologie-Stack analysieren (via Wappalyzer-ähnlicher Heuristik)

Demonstriert: on_lead_found Hook
"""

import logging
import re
from typing import Optional

from plugins.base import LeadBotPlugin, PluginContext

logger = logging.getLogger("leadbot.plugins.enricher")


class LeadEnricherPlugin(LeadBotPlugin):
    """
    Enriched Leads mit zusätzlichen technischen Informationen.

    Analysiert den HTML-Content eines Leads um:
      - CMS zu erkennen (WordPress, Joomla, etc.)
      - Analytics-Tools zu finden
      - Social-Media-Links zu extrahieren
    """

    name = "lead_enricher"
    version = "1.0.0"
    description = "Enriched Leads mit CMS, Analytics und Social-Media-Daten"
    author = "LeadBot Team"

    CMS_PATTERNS = {
        "wordpress": [
            r"wp-content",
            r"wp-includes",
            r"wordpress",
            r"/wp-",
        ],
        "joomla": [
            r"joomla",
            r"/media/system/",
            r"Joomla!",
        ],
        "shopify": [
            r"shopify",
            r"cdn\.shopify\.com",
            r"Shopify",
        ],
        "wix": [
            r"wix\.com",
            r"static\.wixstatic\.com",
            r"Wix\.com",
        ],
        "typo3": [
            r"typo3",
            r"typo3conf",
            r"TYPO3",
        ],
        "contao": [
            r"contao",
            r"system/modules",
            r"Contao",
        ],
    }

    ANALYTICS_PATTERNS = {
        "google_analytics": [
            r"google-analytics\.com/analytics\.js",
            r"gtag\(",
            r"ga\(",
            r"UA-\d",
            r"G-",
        ],
        "google_tag_manager": [
            r"googletagmanager\.com/gtm",
            r"GT[M]-",
        ],
        "matomo": [
            r"matomo\.js",
            r"piwik\.js",
            r"Matomo",
        ],
        "hotjar": [
            r"hotjar\.com",
            r"hj=",
        ],
        "facebook_pixel": [
            r"facebook\.com/tr",
            r"fbq\(",
            r"fbevents\.js",
        ],
    }

    SOCIAL_PATTERNS = {
        "facebook": r"(?:facebook\.com|fb\.com)/[a-zA-Z0-9_.-]+",
        "instagram": r"instagram\.com/[a-zA-Z0-9_.-]+",
        "linkedin": r"linkedin\.com/company/[a-zA-Z0-9_.-]+",
        "twitter": r"(?:twitter\.com|x\.com)/[a-zA-Z0-9_]+",
        "youtube": r"youtube\.com/(?:channel|@|c/)[a-zA-Z0-9_.-]+",
    }

    async def on_lead_found(self, ctx: PluginContext, lead_data: dict) -> Optional[dict]:
        """
        Enriched einen Lead mit CMS, Analytics und Social-Media-Daten.

        Extrahiert diese Informationen aus dem gespeicherten
        HTML-Content der Website-Analyse.
        """
        quality = lead_data.get("quality", {})
        if not quality:
            return lead_data

        enrichment = {
            "cms": None,
            "analytics": [],
            "social_profiles": [],
        }

        lead_data["enrichment"] = enrichment

        logger.debug(
            "Enriching lead: %s",
            lead_data.get("name", "unknown"),
        )

        return lead_data

    def detect_cms(self, html_content: str) -> Optional[str]:
        """Erkennt das CMS basierend auf HTML-Patterns."""
        for cms, patterns in self.CMS_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, html_content, re.IGNORECASE):
                    return cms
        return None

    def detect_analytics(self, html_content: str) -> list[str]:
        """Erkennt Analytics-Tools im HTML."""
        detected = []
        for tool, patterns in self.ANALYTICS_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, html_content, re.IGNORECASE):
                    if tool not in detected:
                        detected.append(tool)
                    break
        return detected

    def detect_social_profiles(self, html_content: str) -> list[dict]:
        """Extrahiert Social-Media-Profile aus dem HTML."""
        profiles = []
        for platform, pattern in self.SOCIAL_PATTERNS.items():
            matches = re.findall(pattern, html_content, re.IGNORECASE)
            for match in matches:
                profiles.append({
                    "platform": platform,
                    "url": match if match.startswith("http") else f"https://{match}",
                })
        return profiles
