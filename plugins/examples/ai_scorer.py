"""
plugins/examples/ai_scorer.py — AI Lead-Scoring Plugin.

Bewertet Leads mit einem erweiterten Scoring-Algorithmus der:
  - Design-Score mit Gewichtung kombiniert
  - Branchenspezifische Faktoren berücksichtigt
  - Wettbewerbs-Dichte in der Stadt einberechnet

Demonstriert: on_lead_found Hook mit komplexer Logik
"""

import logging
from typing import Optional

from plugins.base import LeadBotPlugin, PluginContext
from scraper import LeadsTable

logger = logging.getLogger("leadbot.plugins.ai_scorer")

BRANCH_PRIORITY = {
    "dachdecker": 1.5,
    "sanitär": 1.4,
    "elektriker": 1.4,
    "heizungsbau": 1.3,
    "solartechnik": 1.3,
    "maler": 1.2,
    "tischler": 1.2,
    "schreiner": 1.2,
    "gärtner": 1.1,
    "gartenbau": 1.1,
    "gaLaBau": 1.1,
    "klempner": 1.1,
    "fliesenleger": 1.0,
    "zimmerer": 1.0,
    "stuckateur": 1.0,
    "bodenleger": 0.9,
    "glaserei": 0.9,
    "metallbau": 0.8,
    "trockenbau": 0.8,
    "gerüstbau": 0.7,
}

CRITICAL_ISSUES = {
    "Kein SSL-Zertifikat": 3.0,
    "Nicht mobil-optimiert": 2.5,
    "Tabellen-Layout": 2.0,
    "veraltetes Copyright": 1.0,
    "Kein Copyright-Hinweis": 1.5,
    "Feste Breite": 1.5,
    "Greller Farbkontrast": 1.0,
    "Times New Roman": 1.0,
    "<center>-Tags": 1.5,
    "<font>-Tags": 1.0,
    "<marquee>-Tag": 2.0,
}


class AIScorerPlugin(LeadBotPlugin):
    """
    Erweiterter Lead-Scoring mit gewichteten Faktoren.

    Berechnet einen AI-Score basierend auf:
      - Design-Probleme (gewichtet nach Schwere)
      - Branchen-Priorität (lukrative Branchen höher)
      - Google-Rating (niedriges Rating = höheres Potenzial)
      - Wettbewerbs-Dichte (weniger Konkurrenz = besser)
    """

    name = "ai_scorer"
    version = "1.0.0"
    description = "AI-gestütztes Lead-Scoring mit gewichteten Faktoren"
    author = "LeadBot Team"

    def __init__(self):
        self._city_competition: dict[str, float] = {}

    async def on_lead_found(self, ctx: PluginContext, lead_data: dict) -> Optional[dict]:
        """
        Berechnet den AI-Score für einen neuen Lead.

        Der Score wird als ai_score im lead_data gespeichert.
        Höhere Scores bedeuten höheres Potenzial.
        """
        quality = lead_data.get("quality", {})
        if not quality:
            return lead_data

        score = self._calculate_score(lead_data, quality)
        lead_data["ai_score"] = score
        lead_data["ai_grade"] = self._score_to_grade(score)

        logger.debug(
            "AI-Score für %s: %.1f (%s)",
            lead_data.get("name", "unknown"),
            score,
            lead_data["ai_grade"],
        )

        return lead_data

    def _calculate_score(self, lead_data: dict, quality: dict) -> float:
        """
        Berechnet den gewichteten AI-Score.

        Faktoren:
          1. Issue-Score: Summe der gewichteten Design-Probleme
          2. Branch-Multiplikator: Lukrative Branchen bekommen Bonus
          3. Rating-Faktor: Niedriges Google-Rating = höheres Potenzial
          4. Competition-Faktor: Weniger Leads in der Stadt = besser
        """
        issue_score = 0.0
        issues = quality.get("issues", [])
        for issue in issues:
            for keyword, weight in CRITICAL_ISSUES.items():
                if keyword.lower() in issue.lower():
                    issue_score += weight
                    break
            else:
                issue_score += 1.0

        branch = lead_data.get("branche", "").lower()
        branch_multiplier = BRANCH_PRIORITY.get(branch, 1.0)

        rating = lead_data.get("google_rating", 0)
        rating_factor = max(1.0, (5.0 - rating) * 0.3 + 1.0)

        city = lead_data.get("stadt", "unknown")
        competition_factor = self._get_competition_factor(city)

        score = issue_score * branch_multiplier * rating_factor * competition_factor
        return round(score, 1)

    def _get_competition_factor(self, city: str) -> float:
        """
        Berechnet den Wettbewerbs-Faktor für eine Stadt.

        Weniger bestehende Leads in der Stadt = höherer Faktor.
        """
        if city in self._city_competition:
            return self._city_competition[city]

        all_leads = LeadsTable.all()
        city_leads = [l for l in all_leads if l.get("stadt", "").lower() == city.lower()]
        lead_count = len(city_leads)

        if lead_count == 0:
            factor = 1.5
        elif lead_count < 10:
            factor = 1.2
        elif lead_count < 50:
            factor = 1.0
        else:
            factor = 0.8

        self._city_competition[city] = factor
        return factor

    def _score_to_grade(self, score: float) -> str:
        """Konvertiert Score in eine Note (S/A/B/C/D/F)."""
        if score >= 15.0:
            return "S"
        elif score >= 10.0:
            return "A"
        elif score >= 7.0:
            return "B"
        elif score >= 4.0:
            return "C"
        elif score >= 2.0:
            return "D"
        else:
            return "F"
