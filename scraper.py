"""
scraper.py — Ultimativer Lead-Scraper mit Base64-Fix, Deep-Scan & Multi-Tab

Features:
  - Google Maps: Deep-Scan mit aria-label/authority Extraktion
  - Gelbe Seiten: Base64-dekodierte data-webseitelink Attribute
  - Das Örtliche: Direkte URL mit Paginierung
  - Email-Extraktion: Startseite → Impressum → Kontakt (neuer Tab pro Website)
  - Anti-Blocking: Cookie-Buster, Captcha-Pause, Tab-Close
  - Variablen-Reset: Jede Iteration startet mit leeren Werten
  - Telegram: Sofortige Nachricht pro Lead
  - Notion-Sync über notion_db.py
"""

import asyncio
import base64
import csv
import json
import logging
import os
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse, unquote

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth.stealth import Stealth

logger = logging.getLogger("leadbot.scraper")

# ─── Pfade ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.resolve()
DB_PATH = BASE_DIR / "db" / "leads.json"
PROGRESS_PATH = BASE_DIR / "db" / "hunt_progress.json"
BACKUP_CSV_PATH = BASE_DIR / "backup_leads.csv"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
BROWSER_SESSION_DIR = BASE_DIR / "browser_session"

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
BROWSER_SESSION_DIR.mkdir(parents=True, exist_ok=True)

# ─── Datenbank ───────────────────────────────────────────────────────────────

from tinydb import TinyDB

db = TinyDB(str(DB_PATH))
LeadsTable = db.table("leads")

# ─── Branchen ────────────────────────────────────────────────────────────────

HUNT_BRANCHES = [
    "dachdecker", "sanitär", "elektriker", "maler",
    "tischler", "schreiner", "klempner", "fliesenleger",
    "gärtner", "gartenbau", "zimmerer", "stuckateur",
    "bodenleger", "glaserei", "metallbau", "trockenbau",
    "gerüstbau", "heizungsbau", "solartechnik", "gaLaBau",
]

# ─── Globale Zustände ────────────────────────────────────────────────────────

browser_manager: Optional["BrowserManager"] = None
captcha_callback: Optional[Callable] = None
SEARCH_RADIUS_KM: int = 10


# ==============================================================================
# BrowserManager — Persistent Context + Stealth + Multi-Tab Support
# ==============================================================================

class BrowserManager:
    def __init__(self, headless: bool = False):
        self.playwright = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.headless = headless

    async def launch(self):
        logger.info(
            "Starte Browser (headless=%s) mit persistentem Session-Store: %s",
            self.headless, BROWSER_SESSION_DIR,
        )
        self.playwright = await async_playwright().start()
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_SESSION_DIR),
            headless=self.headless,
            viewport={"width": 1920, "height": 1080},
            locale="de-DE",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-extensions",
                "--disable-gpu",
            ],
        )
        self.page = await self.context.new_page()
        await Stealth().apply_stealth_async(self.page)
        logger.info("Browser gestartet")

    async def new_tab(self) -> Page:
        page = await self.context.new_page()
        await Stealth().apply_stealth_async(page)
        return page

    async def close(self):
        if self.context:
            await self.context.close()
        if self.playwright:
            await self.playwright.stop()
        self.page = None
        self.context = None
        logger.info("Browser geschlossen")

    async def random_delay(self, min_sec: float = 2.0, max_sec: float = 5.0):
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)

    async def detect_captcha(self, page: Page) -> bool:
        try:
            captcha_selectors = [
                "div.g-recaptcha",
                "iframe[src*='recaptcha']",
                "iframe[src*='google.com/recaptcha']",
                "#recaptcha",
                "div[class*='captcha']",
            ]
            for selector in captcha_selectors:
                if await page.query_selector(selector):
                    return True
            title = (await page.title()).lower()
            if "captcha" in title or "robot" in title:
                return True
            url = page.url.lower()
            if any(p in url for p in ["sorry", "unusual traffic", "security check", "verify"]):
                return True
            body = (await page.text_content("body") or "").lower()
            if any(p in body for p in ["prove you're not a robot", "roboter bestätigen", "ich bin kein robot"]):
                return True
            return False
        except Exception:
            return False

    async def handle_captcha(self):
        logger.warning("CAPTCHA erkannt")
        if captcha_callback:
            try:
                result = captcha_callback("⚠️ CAPTCHA! Bitte am PC lösen.")
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                pass
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: input("Drücke Enter im Terminal, wenn du das Captcha gelöst hast..."),
        )
        logger.info("Captcha gelöst — fahre fort")

    async def handle_cookie_consent(self, page: Page):
        try:
            keywords = [
                "Alle akzeptieren", "Ich stimme zu", "Accept all",
                "Zustimmen", "Einverstanden", "OK", "Agree",
                "akzeptieren", "accept", "consent", "zustimmen",
            ]
            clicked_any = False
            for keyword in keywords:
                try:
                    buttons = await page.query_selector_all(
                        f'button:has-text("{keyword}"), '
                        f'a:has-text("{keyword}"), '
                        f'[role="button"]:has-text("{keyword}")'
                    )
                    for btn in buttons:
                        try:
                            if await btn.is_visible():
                                await btn.click()
                                clicked_any = True
                                await page.wait_for_timeout(800)
                        except Exception:
                            continue
                except Exception:
                    continue
            if clicked_any:
                await page.wait_for_timeout(1000)
                logger.info("Cookie-Banner geschlossen")
        except Exception:
            pass

    async def scroll_sidebar(self, page: Page, sidebar_selector: str = "div[role='feed']", scrolls: int = 12, pause_ms: int = 800):
        try:
            sidebar = await page.query_selector(sidebar_selector)
            if sidebar:
                box = await sidebar.bounding_box()
                if box:
                    await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    await page.wait_for_timeout(300)
                    for i in range(scrolls):
                        await page.mouse.wheel(0, random.randint(300, 700))
                        jitter = random.randint(-200, 300)
                        await page.wait_for_timeout(pause_ms + jitter)
                        if (i + 1) % 4 == 0:
                            await page.mouse.wheel(0, -random.randint(50, 150))
                            await page.wait_for_timeout(random.randint(200, 500))
            else:
                await page.mouse.wheel(0, 600)
                await page.wait_for_timeout(pause_ms)
        except Exception:
            pass


# ==============================================================================
# EMAIL-Extraktion — Website-Besuch + Impressum/Kontakt-Fallback
# ==============================================================================

_BAD_EMAIL_DOMAINS = frozenset([
    "example.com", "test.com", "domain.com", "email.com",
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "web.de", "gmx.de", "gmx.net", "t-online.de",
])

_BAD_EMAIL_KEYWORDS = frozenset([
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    "noreply", "no-reply", "info@", "kontakt@", "contact@",
    "support@", "service@", "admin@", "webmaster@",
    "postmaster@", "hostmaster@", "abuse@", "sales@",
    "marketing@", "office@", "team@", "mail@",
    "vertrieb@", "geschaeftsfuehrung@", "gf@",
])


def _is_valid_business_email(email: str) -> bool:
    email_lower = email.lower().strip()
    if any(bad in email_lower for bad in _BAD_EMAIL_KEYWORDS):
        return False
    domain = email_lower.split("@")[-1]
    if domain in _BAD_EMAIL_DOMAINS:
        return False
    return True


def _extract_emails_from_text(text: str) -> list[str]:
    pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    found = re.findall(pattern, text)
    cleaned = []
    for e in found:
        if any(e.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp']):
            continue
        if 'cdn' in e.lower() or 'assets' in e.lower() or 'wp-content' in e.lower():
            continue
        cleaned.append(e)
    return list(dict.fromkeys(cleaned))


async def extract_email_from_website(url: str) -> str:
    """
    Besucht eine Website in einem neuen Tab und extrahiert E-Mails.
    Schließt den Tab sofort nach dem Scan.
    """
    if not url or not url.startswith("http"):
        return ""
    if not browser_manager or not browser_manager.context:
        return ""

    page = None
    try:
        page = await browser_manager.new_tab()
        await browser_manager.random_delay(2, 3)

        logger.info("[Email] Besuche: %s", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(2500)

        # 1. Startseite
        html = await page.content()
        emails = _extract_emails_from_text(html)
        business_emails = [e for e in emails if _is_valid_business_email(e)]
        if business_emails:
            logger.info("[Email] Startseite: %s", business_emails[0])
            return business_emails[0]
        if emails:
            return emails[0]

        # 2. Impressum
        impressum_link = None
        for a in await page.query_selector_all("a[href]"):
            try:
                text = (await a.inner_text()).strip().lower()
                href = (await a.get_attribute("href")) or ""
                if any(kw in text for kw in ["impressum", "imprint", "legal notice"]):
                    impressum_link = urljoin(url, href)
                    break
                if any(kw in href.lower() for kw in ["impressum", "imprint"]):
                    impressum_link = urljoin(url, href)
                    break
            except Exception:
                continue

        if impressum_link:
            try:
                logger.debug("[Email] Impressum: %s", impressum_link)
                await page.goto(impressum_link, wait_until="domcontentloaded", timeout=10000)
                await page.wait_for_timeout(1500)
                html = await page.content()
                emails = _extract_emails_from_text(html)
                business_emails = [e for e in emails if _is_valid_business_email(e)]
                if business_emails:
                    logger.info("[Email] Impressum: %s", business_emails[0])
                    return business_emails[0]
                if emails:
                    return emails[0]
            except Exception:
                pass

        # 3. Kontakt
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=10000)
            await page.wait_for_timeout(1000)
        except Exception:
            pass

        kontakt_link = None
        for a in await page.query_selector_all("a[href]"):
            try:
                text = (await a.inner_text()).strip().lower()
                href = (await a.get_attribute("href")) or ""
                if any(kw in text for kw in ["kontakt", "contact", "anfrage"]):
                    kontakt_link = urljoin(url, href)
                    break
                if any(kw in href.lower() for kw in ["kontakt", "contact"]):
                    kontakt_link = urljoin(url, href)
                    break
            except Exception:
                continue

        if kontakt_link:
            try:
                logger.debug("[Email] Kontakt: %s", kontakt_link)
                await page.goto(kontakt_link, wait_until="domcontentloaded", timeout=10000)
                await page.wait_for_timeout(1500)
                html = await page.content()
                emails = _extract_emails_from_text(html)
                business_emails = [e for e in emails if _is_valid_business_email(e)]
                if business_emails:
                    logger.info("[Email] Kontakt: %s", business_emails[0])
                    return business_emails[0]
                if emails:
                    return emails[0]
            except Exception:
                pass

        logger.info("[Email] Keine E-Mail auf %s", url)
        return ""
    except Exception:
        return ""
    finally:
        if page:
            try:
                await page.close()
                logger.debug("[Email] Tab geschlossen")
            except Exception:
                pass


# ==============================================================================
# Google Maps Deep-Scan
# ==============================================================================

async def _find_maps_search_field(page: Page) -> Optional[object]:
    selectors = [
        "input#searchboxinput", "input.gsfi", 'textarea[name="q"]',
        'input[name="q"]', "input[placeholder*='Suchen']",
        "input[placeholder*='Search']", "input[aria-label*='Suchen']",
        "input[aria-label*='Search']", "#searchbox input", ".searchbox input",
    ]
    for selector in selectors:
        try:
            el = await page.query_selector(selector)
            if el and await el.is_visible():
                return el
        except Exception:
            continue
    return None


async def scrape_google_maps(
    branch: str, city: str, max_results: int = 20,
    page: Page = None, radius_km: int = None,
) -> list[dict]:
    if not browser_manager or not browser_manager.context:
        return []
    if page is None:
        page = await browser_manager.new_tab()
    if radius_km is None:
        radius_km = SEARCH_RADIUS_KM

    query = f"{branch} {city} + {radius_km}km"
    logger.info("[Maps] Suche: %s (Radius: %dkm)", query, radius_km)

    results = []
    seen_names = set()

    for attempt in range(1, 3):
        try:
            await browser_manager.random_delay(2, 5)
            await page.goto("https://www.google.de/maps", wait_until="domcontentloaded", timeout=30000)

            # Consent-Buster
            for sel in [
                'button:has-text("Alle akzeptieren")',
                'button:has-text("Ich stimme zu")',
                'button:has-text("Zustimmen")',
                'button:has-text("Accept all")',
                'a:has-text("Alle akzeptieren")',
                '[role="button"]:has-text("Alle akzeptieren")',
                'form button',
            ]:
                try:
                    await page.click(sel, timeout=5000)
                    await page.wait_for_timeout(1200)
                except Exception:
                    pass
            await page.wait_for_timeout(1500)

            if await browser_manager.detect_captcha(page):
                await browser_manager.handle_captcha()

            search_field = await _find_maps_search_field(page)
            if not search_field:
                if attempt == 1:
                    await page.wait_for_timeout(5000)
                    search_field = await _find_maps_search_field(page)
                if not search_field:
                    error_path = str(SCREENSHOTS_DIR / "maps_error.png")
                    await page.screenshot(path=error_path, full_page=True)
                    logger.error("[Maps] Suchfeld nicht gefunden! Screenshot: %s", error_path)
                    return []

            await search_field.click()
            await page.wait_for_timeout(random.uniform(200, 500))
            for _ in range(3):
                await search_field.press("Control+a")
                await page.wait_for_timeout(50)
            await search_field.press("Backspace")
            await page.wait_for_timeout(random.uniform(100, 300))
            for char in query:
                await search_field.press(char)
                await page.wait_for_timeout(random.uniform(80, 180))
            await page.wait_for_timeout(random.uniform(400, 800))
            await page.keyboard.press("Enter")
            await page.wait_for_load_state("domcontentloaded", timeout=30000)
            await browser_manager.random_delay(3, 6)

            if await browser_manager.detect_captcha(page):
                await browser_manager.handle_captcha()

            try:
                await page.wait_for_selector("div[role='feed']", state="visible", timeout=15000)
            except Exception:
                pass
            await page.wait_for_timeout(random.uniform(1000, 2000))

            await browser_manager.scroll_sidebar(page, "div[role='feed']", scrolls=14, pause_ms=650)
            await page.wait_for_timeout(2000)
            await browser_manager.scroll_sidebar(page, "div[role='feed']", scrolls=8, pause_ms=500)
            await page.wait_for_timeout(1500)

            # Zähle Karten via JS (nie stale)
            total_cards = await page.evaluate(
                """() => {
                    const cards = document.querySelectorAll('a.hfpxzc, a.qBF1Pd, div[role="feed"] a[href*="/maps/place/"]');
                    return cards.length;
                }"""
            )
            if not total_cards:
                total_cards = await page.evaluate(
                    """() => document.querySelectorAll('div[role="feed"] > div').length"""
                )

            logger.info("[Maps] %d Ergebnis-Karten gefunden", total_cards)

            for idx in range(min(total_cards, max_results * 2)):
                if len(results) >= max_results:
                    break

                # ===== VARIABLEN-RESET =====
                name = ""
                website = ""
                phone = ""
                rating = 0
                address = ""
                email = ""

                try:
                    # Klicke per JS-Index (nie stale)
                    clicked = await page.evaluate(
                        """(idx) => {
                            const sels = ['a.hfpxzc', 'a.qBF1Pd', 'div[role="feed"] a[href*="/maps/place/"]'];
                            for (const sel of sels) {
                                const cards = document.querySelectorAll(sel);
                                if (cards[idx]) {
                                    cards[idx].scrollIntoView({behavior: 'instant', block: 'center'});
                                    cards[idx].click();
                                    return true;
                                }
                            }
                            return false;
                        }""",
                        idx,
                    )
                    if not clicked:
                        continue

                    await page.wait_for_timeout(random.uniform(2000, 3500))

                    if await browser_manager.detect_captcha(page):
                        await browser_manager.handle_captcha()

                    # Detail-Panel warten
                    detail_loaded = False
                    for detail_sel in ["[data-item-id='authority']", "div[role='main']", "section", "h1[data-item-id]", "h1"]:
                        try:
                            await page.wait_for_selector(detail_sel, timeout=4000)
                            detail_loaded = True
                            break
                        except Exception:
                            continue
                    if not detail_loaded:
                        continue

                    # Name
                    name_el = await page.query_selector("h1[data-item-id]")
                    if not name_el:
                        name_el = await page.query_selector("h1.timmcc")
                    if not name_el:
                        name_el = await page.query_selector("div.fontHeadlineLarge")
                    if not name_el:
                        name_el = await page.query_selector("h1")
                    if not name_el:
                        continue

                    name = (await name_el.inner_text()).strip()
                    if not name or len(name) < 2 or name in seen_names:
                        continue
                    seen_names.add(name)

                    # Website: aria-label="Website" oder [data-item-id="authority"]
                    website = await _extract_maps_website(page)
                    phone = await _extract_maps_phone(page)
                    rating = await _extract_maps_rating(page)
                    address = await _extract_maps_address(page)

                    # Email von Website
                    if website:
                        email = await extract_email_from_website(website)
                        await browser_manager.random_delay(2, 3)

                    results.append({
                        "name": name,
                        "phone": phone,
                        "website": website,
                        "email": email,
                        "source": "google_maps",
                        "rating": rating,
                        "address": address,
                    })

                    logger.info(
                        "  [Maps] [%d/%d] %s | Web: %s | Email: %s | Tel: %s",
                        len(results), max_results, name,
                        website or "—", email or "—", phone or "—",
                    )

                    # Panel schließen
                    try:
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(600)
                    except Exception:
                        pass
                    await page.mouse.move(random.randint(100, 300), random.randint(100, 300))
                    await page.wait_for_timeout(random.uniform(800, 1500))

                except Exception as e:
                    logger.debug("[Maps] Eintrag #%d Fehler: %s", idx, e)
                    try:
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(500)
                    except Exception:
                        pass
                    continue

            if len(results) == 0:
                logger.warning("[Maps] 0 Ergebnisse")
            else:
                logger.info("[Maps] %d Ergebnisse", len(results))
            break
        except Exception as e:
            logger.error("[Maps] Fehlgeschlagen (Versuch %d): %s", attempt, e)
            if attempt == 1:
                await page.wait_for_timeout(5000)
            else:
                try:
                    await page.screenshot(path=str(SCREENSHOTS_DIR / "maps_error.png"), full_page=True)
                except Exception:
                    pass

    return results


async def _extract_maps_website(page: Page) -> str:
    """
    Extrahiert Website aus Maps Detail-Panel.
    1. aria-label*="website"
    2. [data-item-id="authority"]
    3. Text-Match
    """
    def _clean(href: str) -> str:
        if not href:
            return ""
        if "/url?q=" in href:
            href = href.split("/url?q=")[1].split("&")[0]
        decoded = unquote(href)
        if "google" not in decoded.lower() and "maps" not in decoded.lower():
            return decoded.split("?")[0]
        return ""

    try:
        # 1. aria-label*="website"
        for sel in ['a[aria-label*="website" i]', 'a[aria-label*="Webseite" i]', 'a[aria-label*="Website" i]']:
            el = await page.query_selector(sel)
            if el:
                href = await el.get_attribute("href")
                cleaned = _clean(href)
                if cleaned:
                    logger.debug("[Maps] Website via aria-label: %s", cleaned)
                    return cleaned

        # 2. [data-item-id="authority"]
        for sel in ['[data-item-id="authority"]', 'a[data-item-id="authority"]', 'button[data-item-id="authority"]']:
            el = await page.query_selector(sel)
            if el:
                href = await el.get_attribute("href")
                cleaned = _clean(href)
                if cleaned:
                    logger.debug("[Maps] Website via authority: %s", cleaned)
                    return cleaned

        # 3. data-item-id*="website"
        el = await page.query_selector("a[data-item-id*='website']")
        if el:
            cleaned = _clean(await el.get_attribute("href"))
            if cleaned:
                return cleaned

        # 4. Text-basiert
        all_links = await page.query_selector_all("div[role='main'] a[href], section a[href], a[href^='http']")
        for link in all_links:
            try:
                href = await link.get_attribute("href")
                text = (await link.inner_text()).strip().lower()
                if any(kw in text for kw in ["website", "webseite", "besuchen", "homepage", "zur webseite"]):
                    cleaned = _clean(href)
                    if cleaned:
                        return cleaned
            except Exception:
                continue

        # 5. Erster externer Link
        for link in all_links:
            try:
                href = await link.get_attribute("href")
                cleaned = _clean(href)
                if cleaned and cleaned.startswith(("http://", "https://")):
                    return cleaned
            except Exception:
                continue
    except Exception:
        pass
    return ""


async def _extract_maps_phone(page: Page) -> str:
    try:
        for sel in ["a[href^='tel:']", "button[data-item-id*='phone']", "div[data-item-id*='phone'] span"]:
            el = await page.query_selector(sel)
            if el:
                if sel.startswith("a[href^='tel:']"):
                    href = await el.get_attribute("href")
                    return href.replace("tel:", "").strip() if href else ""
                return (await el.inner_text()).strip()
    except Exception:
        pass
    return ""


async def _extract_maps_rating(page: Page) -> float:
    try:
        for sel in [
            "span.fontBodyMedium[aria-label*='Sterne']",
            "div[data-item-id*='rating'] span[aria-label]",
            "span[aria-label*='rated']",
        ]:
            el = await page.query_selector(sel)
            if el:
                aria = await el.get_attribute("aria-label") or ""
                m = re.search(r"(\d[\.,]?\d?)", aria)
                if m:
                    return float(m.group(1).replace(",", "."))
                text = await el.inner_text()
                m = re.search(r"(\d[\.,]?\d?)", text)
                if m:
                    return float(m.group(1).replace(",", "."))
    except Exception:
        pass
    return 0


async def _extract_maps_address(page: Page) -> str:
    try:
        for sel in ["button[data-item-id*='address']", "div[data-item-id*='address']"]:
            el = await page.query_selector(sel)
            if el:
                spans = await el.query_selector_all("span")
                if spans:
                    texts = [await s.inner_text() for s in spans]
                    return " ".join(t.strip() for t in texts if t.strip())
                return (await el.inner_text()).strip()
    except Exception:
        pass
    return ""


# ==============================================================================
# Gelbe Seiten — mit Base64-Fix & Paginierung
# ==============================================================================

async def scrape_gelbe_seiten(
    branch: str, city: str, max_results: int = 20,
    page: Page = None, radius_km: int = None,
) -> list[dict]:
    if not browser_manager or not browser_manager.context:
        return []
    if page is None:
        page = await browser_manager.new_tab()
    if radius_km is None:
        radius_km = SEARCH_RADIUS_KM

    branch_encoded = branch.replace(" ", "-")
    city_encoded = city.replace(" ", "-")
    umkreis = radius_km * 1000  # in Metern

    results = []
    seen_names = set()
    social_media_domains = [
        "facebook.com", "instagram.com", "twitter.com", "x.com",
        "linkedin.com", "youtube.com", "tiktok.com", "pinterest.de",
        "wa.me", "whatsapp.com",
    ]

    for page_num in range(1, 6):
        if len(results) >= max_results:
            break

        if page_num == 1:
            url = f"https://www.gelbeseiten.de/suche/{branch_encoded}/{city_encoded}?umkreis={umkreis}"
        else:
            url = f"https://www.gelbeseiten.de/suche/{branch_encoded}/{city_encoded}?umkreis={umkreis}&page={page_num}"

        logger.info("[GelbeSeiten] Seite %d: %s", page_num, url)

        for attempt in range(1, 3):
            try:
                await browser_manager.random_delay(2, 4)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                if await browser_manager.detect_captcha(page):
                    await browser_manager.handle_captcha()

                await page.wait_for_timeout(2500)
                html = await page.content()
                soup = BeautifulSoup(html, "lxml")

                entries = soup.select(".modulTeaser, [class*='teaser'], [class*='entry'], article, .result-item")
                if not entries:
                    logger.info("[GelbeSeiten] Seite %d: keine Einträge", page_num)
                    break

                for entry in entries:
                    if len(results) >= max_results:
                        break

                    try:
                        name_el = entry.select_one("h2, h3, .name, [class*='name'], .business-name")
                        if not name_el:
                            continue
                        name = name_el.get_text(strip=True)
                        if len(name) < 2 or name in seen_names:
                            continue
                        seen_names.add(name)

                        phone_el = entry.select_one("[class*='phone'], .tel, a[href^='tel:']")
                        phone = phone_el.get_text(strip=True) if phone_el else ""

                        website = ""

                        # ===== BASE64-FIX =====
                        # data-webseitelink Attribut in <span> Tags
                        webseite_span = entry.select_one('span[data-webseitelink]')
                        if webseite_span:
                            b64_val = webseite_span.get("data-webseitelink", "")
                            if b64_val:
                                try:
                                    decoded = base64.b64decode(b64_val).decode('utf-8')
                                    if decoded.startswith("http") and not any(social in decoded.lower() for social in social_media_domains):
                                        website = decoded
                                        logger.debug("[GelbeSeiten] Base64-Link dekodiert: %s", website)
                                except Exception as e:
                                    logger.debug("[GelbeSeiten] Base64-Dekodierung fehlgeschlagen: %s", e)

                        # Fallback: data-zve-ad-click-element="website"
                        if not website:
                            zve = entry.select_one('a[data-zve-ad-click-element="website"]')
                            if zve:
                                href = zve.get("href", "")
                                if href.startswith("http"):
                                    website = href

                        # Fallback: Text-Match
                        if not website:
                            for link in entry.select("a"):
                                text = link.get_text(strip=True).lower()
                                href = link.get("href", "")
                                if any(kw in text for kw in ["webseite", "website", "zur webseite", "homepage"]):
                                    if href.startswith("http") and not any(social in href.lower() for social in social_media_domains):
                                        if "gelbeseiten.de" not in href.lower():
                                            website = href
                                            break

                        # Fallback: generische Links
                        if not website:
                            for link in entry.select("a[href*='http']"):
                                href = link.get("href", "")
                                if not href.startswith("http"):
                                    continue
                                if any(social in href.lower() for social in social_media_domains):
                                    continue
                                if "gelbeseiten.de" in href.lower():
                                    continue
                                website = href
                                break

                        address_el = entry.select_one("[class*='address'], [class*='street'], .address")
                        address = address_el.get_text(strip=True) if address_el else ""

                        # Email extrahieren
                        email = ""
                        if website:
                            email = await extract_email_from_website(website)
                            await browser_manager.random_delay(2, 3)

                        results.append({
                            "name": name,
                            "phone": phone,
                            "website": website,
                            "email": email,
                            "source": "gelbe_seiten",
                            "rating": 0,
                            "address": address,
                        })

                        logger.info(
                            "  [GelbeSeiten] [%d/%d] %s | Web: %s | Email: %s",
                            len(results), max_results, name,
                            website or "—", email or "—",
                        )
                    except Exception:
                        continue

                logger.info("[GelbeSeiten] Seite %d: %d Einträge, gesamt %d", page_num, len(entries), len(results))
                break

            except Exception as e:
                logger.error("[GelbeSeiten] Seite %d Fehlgeschlagen (Versuch %d): %s", page_num, attempt, e)
                if attempt == 1:
                    await page.wait_for_timeout(5000)
                else:
                    break

        # Prüfe nächste Seite
        has_next = False
        try:
            next_btn = await page.query_selector(
                '.gs_seitenweiter_link, a.paging__next, [class*="next"], [class*="weiter"], a[title*="Nächste"]'
            )
            if next_btn and await next_btn.is_visible():
                has_next = True
        except Exception:
            pass
        if not has_next and page_num < 5:
            logger.info("[GelbeSeiten] Keine weitere Seite")
            break

    if len(results) == 0:
        logger.warning("[GelbeSeiten] 0 Ergebnisse")
    else:
        logger.info("[GelbeSeiten] %d Ergebnisse gesamt", len(results))

    return results


# ==============================================================================
# Das Örtliche — mit Paginierung
# ==============================================================================

async def scrape_das_oertliche(
    branch: str, city: str, max_results: int = 20,
    page: Page = None, radius_km: int = None,
) -> list[dict]:
    if not browser_manager or not browser_manager.context:
        return []
    if page is None:
        page = await browser_manager.new_tab()
    if radius_km is None:
        radius_km = SEARCH_RADIUS_KM

    branch_encoded = branch.replace(" ", "+")
    city_encoded = city.replace(" ", "+")

    results = []
    seen_names = set()
    social_media_domains = [
        "facebook.com", "instagram.com", "twitter.com", "x.com",
        "linkedin.com", "youtube.com", "tiktok.com", "pinterest.de",
        "wa.me", "whatsapp.com",
    ]

    for page_num in range(1, 6):
        if len(results) >= max_results:
            break

        if page_num == 1:
            url = f"https://www.dasoertliche.de/?form_name=search_nat&zvo_ok=0&worte={branch_encoded}&ort={city_encoded}"
        else:
            url = f"https://www.dasoertliche.de/?form_name=search_nat&zvo_ok=0&worte={branch_encoded}&ort={city_encoded}&page={page_num}"

        logger.info("[DasOertliche] Seite %d", page_num)

        for attempt in range(1, 3):
            try:
                await browser_manager.random_delay(2, 4)
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)

                if await browser_manager.detect_captcha(page):
                    await browser_manager.handle_captcha()

                await page.wait_for_timeout(2000)
                html = await page.content()
                soup = BeautifulSoup(html, "lxml")

                entries = soup.select(".result, .mod, [class*='result'], [class*='entry'], article, .hit")
                if not entries:
                    break

                for entry in entries:
                    if len(results) >= max_results:
                        break

                    try:
                        name_el = entry.select_one("h2, h3, .name, [class*='name'], .business-name")
                        if not name_el:
                            continue
                        name = name_el.get_text(strip=True)
                        if len(name) < 2 or name in seen_names:
                            continue
                        seen_names.add(name)

                        phone_el = entry.select_one("[class*='phone'], .tel, a[href^='tel:']")
                        phone = phone_el.get_text(strip=True) if phone_el else ""

                        website = ""
                        for link in entry.select("a[href*='http']"):
                            href = link.get("href", "")
                            if not href.startswith("http"):
                                continue
                            if any(social in href.lower() for social in social_media_domains):
                                continue
                            if "dasoertliche.de" in href.lower():
                                continue
                            website = href
                            break

                        if not website:
                            website_el = entry.select_one("a[class*='website'], a[class*='homepage']")
                            if website_el:
                                href = website_el.get("href", "")
                                if href.startswith("http"):
                                    website = href

                        address_el = entry.select_one("[class*='address'], [class*='street'], .address")
                        address = address_el.get_text(strip=True) if address_el else ""

                        email = ""
                        if website:
                            email = await extract_email_from_website(website)
                            await browser_manager.random_delay(2, 3)

                        results.append({
                            "name": name,
                            "phone": phone,
                            "website": website,
                            "email": email,
                            "source": "das_oertliche",
                            "rating": 0,
                            "address": address,
                        })

                        logger.info(
                            "  [DasOertliche] [%d/%d] %s | Web: %s | Email: %s",
                            len(results), max_results, name,
                            website or "—", email or "—",
                        )
                    except Exception:
                        continue

                logger.info("[DasOertliche] Seite %d: %d Einträge, gesamt %d", page_num, len(entries), len(results))
                break

            except Exception as e:
                logger.error("[DasOertliche] Seite %d Fehlgeschlagen (Versuch %d): %s", page_num, attempt, e)
                if attempt == 1:
                    await page.wait_for_timeout(5000)
                else:
                    break

        has_next = False
        try:
            next_btn = await page.query_selector('.next, [class*="next"], a[title*="Nächste"], a[title*="Weiter"]')
            if next_btn and await next_btn.is_visible():
                has_next = True
        except Exception:
            pass
        if not has_next and page_num < 5:
            break

    if len(results) == 0:
        logger.warning("[DasOertliche] 0 Ergebnisse")
    else:
        logger.info("[DasOertliche] %d Ergebnisse gesamt", len(results))

    return results


# ==============================================================================
# hunt_leads — Multi-Tab Parallele Suche
# ==============================================================================

async def hunt_leads(
    branch: str, city: str, max_results: int = 20, radius_km: int = None,
) -> list[dict]:
    if radius_km is None:
        radius_km = SEARCH_RADIUS_KM

    logger.info("=== hunt_leads PARALLEL: %s in %s (Radius: %dkm) ===", branch, city, radius_km)

    maps_page = None
    directory_page = None

    try:
        maps_page = await browser_manager.new_tab()
        directory_page = await browser_manager.new_tab()

        maps_task = scrape_google_maps(branch, city, max_results=max_results, page=maps_page, radius_km=radius_km)
        directories_task = _scrape_directories_sequential(branch, city, max_results=max_results, page=directory_page, radius_km=radius_km)

        maps_results, directory_results = await asyncio.gather(
            maps_task, directories_task, return_exceptions=True,
        )

        if isinstance(maps_results, Exception):
            logger.error("[Maps] Task Exception: %s", maps_results)
            maps_results = []
        if isinstance(directory_results, Exception):
            logger.error("[Directories] Task Exception: %s", directory_results)
            directory_results = []
    except Exception as e:
        logger.error("hunt_leads gather fehlgeschlagen: %s", e)
        maps_results = []
        directory_results = []
    finally:
        if maps_page:
            try:
                await maps_page.close()
            except Exception:
                pass
        if directory_page:
            try:
                await directory_page.close()
            except Exception:
                pass

    all_results = []
    seen_websites = set()
    seen_names = set()

    for biz in maps_results + directory_results:
        website = biz.get("website", "")
        name = biz.get("name", "")
        name_key = name.lower().strip()

        is_duplicate = False
        if website and website in seen_websites:
            is_duplicate = True
        if name_key and name_key in seen_names:
            is_duplicate = True

        if not is_duplicate:
            if website:
                seen_websites.add(website)
            if name_key:
                seen_names.add(name_key)
            all_results.append(biz)

    logger.info("hunt_leads fertig: %d Leads gesamt", len(all_results))
    return all_results


async def _scrape_directories_sequential(
    branch: str, city: str, max_results: int = 20,
    page: Page = None, radius_km: int = None,
) -> list[dict]:
    all_results = []
    gs = await scrape_gelbe_seiten(branch, city, max_results=max_results, page=page, radius_km=radius_km)
    all_results.extend(gs)
    if len(all_results) < max_results:
        await asyncio.sleep(random.uniform(2, 4))
        do = await scrape_das_oertliche(branch, city, max_results=max_results, page=page, radius_km=radius_km)
        all_results.extend(do)
    return all_results


# ==============================================================================
# Website-Analyse (SSL, Viewport, Copyright, Design)
# ==============================================================================

async def analyze_website_async(url: str, firmenname: str = "", page: Page = None) -> dict:
    result = {
        "has_ssl": False,
        "has_viewport": False,
        "has_copyright": False,
        "copyright_year": 0,
        "copyright_age": 0,
        "is_perfect": False,
        "qualifies": False,
        "issues": [],
        "reasons": [],
        "design_criticism": [],
        "phone": "",
        "screenshot_path": "",
        "impressum_data": {"name": "", "phone": "", "email": "", "impressum_url": ""},
        "uses_table_layout": False,
        "uses_deprecated_html": False,
        "uses_system_fonts": False,
    }

    if not url:
        result["issues"].append("Keine Website gefunden")
        result["reasons"].append("Keine Website gefunden")
        result["design_criticism"].append("Keine Website vorhanden")
        result["qualifies"] = True
        return result

    if not browser_manager or not browser_manager.context:
        result["issues"].append("Browser nicht verfügbar")
        result["reasons"].append("Browser-Fehler")
        return result

    own_page = page
    should_close = False
    if own_page is None:
        own_page = await browser_manager.new_tab()
        should_close = True

    try:
        parsed = urlparse(url)
        result["has_ssl"] = parsed.scheme == "https"
        if not result["has_ssl"]:
            result["issues"].append("Kein SSL-Zertifikat (nur HTTP)")
            result["reasons"].append("Kein SSL (HTTP)")

        await browser_manager.random_delay(2, 5)

        if await browser_manager.detect_captcha(own_page):
            await browser_manager.handle_captcha()

        logger.info("Besuche Website: %s", url)
        await own_page.goto(url, wait_until="domcontentloaded", timeout=15000)

        screenshot_path = await _take_screenshot(own_page, firmenname)
        result["screenshot_path"] = screenshot_path

        html_content = await own_page.content()
        soup = BeautifulSoup(html_content, "lxml")

        viewport = soup.find("meta", attrs={"name": "viewport"})
        result["has_viewport"] = viewport is not None
        if not result["has_viewport"]:
            result["issues"].append("Nicht mobil-optimiert (kein Viewport-Meta-Tag)")
            result["reasons"].append("Nicht mobil-optimiert (kein Viewport)")

        current_year = datetime.now().year
        copyright_match = re.search(r"(?:&copy;|©|Copyright)\s*[\-–—]?\s*(\d{4})", html_content)
        if copyright_match:
            year = int(copyright_match.group(1))
            result["has_copyright"] = True
            result["copyright_year"] = year
            result["copyright_age"] = current_year - year
            if year < current_year:
                result["issues"].append(f"Veraltetes Copyright-Jahr ({year})")
                result["reasons"].append(f"Veraltetes Copyright ({year})")
        else:
            result["issues"].append("Kein Copyright-Hinweis gefunden")
            result["reasons"].append("Kein Copyright-Hinweis")

        result["phone"] = _extract_phone_from_html(soup)

        layout_issues = _check_layout(soup)
        result["design_criticism"].extend(layout_issues)
        result["issues"].extend(layout_issues)
        if any("Tabellen-Layout" in i for i in layout_issues):
            result["uses_table_layout"] = True
        if any("veraltet" in i.lower() for i in layout_issues):
            result["uses_deprecated_html"] = True

        font_issues = _check_fonts(soup, html_content)
        result["design_criticism"].extend(font_issues)
        result["issues"].extend(font_issues)
        if any("Schrift" in i or "Times" in i for i in font_issues):
            result["uses_system_fonts"] = True

        width_issues = _check_fixed_width(soup, html_content)
        result["design_criticism"].extend(width_issues)
        result["issues"].extend(width_issues)

        color_issues = _check_color_contrast(soup, html_content)
        result["design_criticism"].extend(color_issues)
        result["issues"].extend(color_issues)

        result["impressum_data"] = await _extract_impressum_async(own_page, soup, url)

        result["qualifies"] = _qualify_lead(result)

        result["is_perfect"] = (
            result["has_ssl"]
            and result["has_viewport"]
            and result["has_copyright"]
            and result["copyright_year"] >= current_year
            and len(result["design_criticism"]) == 0
        )

        logger.info(
            "Analyse %s: qualifiziert=%s, perfekt=%s, Probleme=%d",
            url, result["qualifies"], result["is_perfect"], len(result["issues"]),
        )
    except Exception as e:
        logger.error("Website-Analyse fehlgeschlagen für %s: %s", url, e)
        result["issues"].append(f"Analyse-Fehler: {str(e)}")
        result["reasons"].append("Analyse fehlgeschlagen")
        result["design_criticism"].append("Analyse fehlgeschlagen")
    finally:
        if should_close and own_page:
            try:
                await own_page.close()
            except Exception:
                pass

    return result


async def _take_screenshot(page: Page, firmenname: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = re.sub(r"[^a-zäöüß0-9\s]", "", firmenname.lower().strip())
    safe_name = safe_name.replace(" ", "_")[:40] or "unknown"
    filename = f"{safe_name}_{timestamp}.png"
    filepath = str(SCREENSHOTS_DIR / filename)
    try:
        await page.screenshot(path=filepath, full_page=True)
        logger.info("Screenshot gespeichert: %s", filepath)
        return filepath
    except Exception as e:
        logger.error("Screenshot fehlgeschlagen: %s", e)
        return ""


async def _extract_impressum_async(page: Page, soup: BeautifulSoup, base_url: str) -> dict:
    result = {"name": "", "phone": "", "email": "", "impressum_url": ""}

    try:
        impressum_link = _find_impressum_link(soup, base_url)
        if not impressum_link:
            return result

        result["impressum_url"] = impressum_link
        logger.info("Besuche Impressum: %s", impressum_link)

        await browser_manager.random_delay(2, 5)
        await page.goto(impressum_link, wait_until="domcontentloaded", timeout=10000)

        impressum_html = await page.content()
        impressum_soup = BeautifulSoup(impressum_html, "lxml")
        text = impressum_soup.get_text()

        name_patterns = [
            r"Gesch[aä]ftsf[uü]hrer(?:in)?[:\s]*([A-Z][a-zäöüß]+ [A-Z][a-zäöüß]+)",
            r"Gesch[aä]ftsf[uü]hrung[:\s]*([A-Z][a-zäöüß]+ [A-Z][a-zäöüß]+)",
            r"(?:Inhaber|Inhaberin|Vorstand)[:\s]*([A-Z][a-zäöüß]+ [A-Z][a-zäöüß]+)",
            r"(?:vertreten durch|vertretungsberechtigt)[:\s]*([A-Z][a-zäöüß]+ [A-Z][a-zäöüß]+)",
        ]
        for pattern in name_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result["name"] = match.group(1).strip()
                break

        phone_patterns = [
            r"Telefon[:\s]*([\+]?[\d\s\/\-\(\)]{7,})",
            r"Tel[:\s]*([\+]?[\d\s\/\-\(\)]{7,})",
            r"Telefonnummer[:\s]*([\+]?[\d\s\/\-\(\)]{7,})",
        ]
        for pattern in phone_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                result["phone"] = match.group(1).strip()
                break

        emails = _extract_emails_from_text(text)
        business_emails = [e for e in emails if _is_valid_business_email(e)]
        if business_emails:
            result["email"] = business_emails[0]
        elif emails:
            result["email"] = emails[0]

        await page.go_back(wait_until="domcontentloaded", timeout=10000)
    except Exception as e:
        logger.error("Impressum-Extraktion fehlgeschlagen: %s", e)

    return result


# ==============================================================================
# Design-Checks (BeautifulSoup, sync)
# ==============================================================================

def _check_layout(soup: BeautifulSoup) -> list[str]:
    issues = []
    center_tags = soup.find_all("center")
    if len(center_tags) > 2:
        issues.append(f"Nutzt <center>-Tags ({len(center_tags)}x) — 90er-Jahre Design")
    for table in soup.find_all("table"):
        if table.get("bgcolor") or table.get("border"):
            issues.append("Nutzt Tabellen-Layout mit bgcolor/border Attributen")
            break
    body = soup.find("body")
    if body and (body.get("bgcolor") or body.get("background")):
        issues.append("Body nutzt veraltete bgcolor/background Attribute")
    font_tags = soup.find_all("font")
    if len(font_tags) > 3:
        issues.append(f"Nutzt <font>-Tags ({len(font_tags)}x) — veraltetes HTML")
    if soup.find_all("marquee"):
        issues.append("Nutzt <marquee>-Tag — veraltetes HTML")
    if soup.find_all("blink"):
        issues.append("Nutzt <blink>-Tag — veraltetes HTML")
    return issues


def _check_fonts(soup: BeautifulSoup, raw_html: str) -> list[str]:
    issues = []
    css_blocks = [s.get_text() for s in soup.find_all("style")]
    full_css = "\n".join(css_blocks)
    times_patterns = [
        r'font-family\s*:\s*["\']?times\s*new\s*roman["\']?\s*;',
        r'font-family\s*:\s*["\']?times["\']?\s*;',
        r'font\s*:\s*[^;]*times\s*new\s*roman',
    ]
    for pattern in times_patterns:
        if re.search(pattern, full_css, re.IGNORECASE):
            has_fallback = re.search(r'times\s*new\s*roman\s*[,;]', full_css, re.IGNORECASE)
            if not has_fallback:
                issues.append("Keine mobilen Schriften (Times New Roman ohne Fallback)")
            else:
                issues.append("Times New Roman als Hauptschriftart")
            break
    body = soup.find("body")
    if body:
        body_style = body.get("style", "").lower()
        body_face = body.get("face", "").lower()
        if "times new roman" in body_style or "times new roman" in body_face:
            if "arial" not in body_style and "sans" not in body_style:
                issues.append("Keine mobilen Schriften (Times New Roman im Body)")
    return issues


def _check_fixed_width(soup: BeautifulSoup, raw_html: str) -> list[str]:
    issues = []
    css_blocks = [s.get_text() for s in soup.find_all("style")]
    full_css = "\n".join(css_blocks)
    fixed_patterns = [
        r'width\s*:\s*(\d{3,4})\s*px',
        r'max-width\s*:\s*(\d{3,4})\s*px',
        r'width\s*=\s*["\']?(\d{3,4})["\']?',
    ]
    found_widths = []
    for pattern in fixed_patterns:
        for w in re.findall(pattern, full_css + " " + raw_html, re.IGNORECASE):
            width_val = int(w)
            if 600 <= width_val <= 1200:
                found_widths.append(width_val)
    if found_widths:
        most_common = max(set(found_widths), key=found_widths.count)
        issues.append(f"Feste Breite ({most_common}px) — nicht responsiv")
    wrapper = soup.find(id=re.compile(r"(wrapper|container|main|content)", re.IGNORECASE))
    if wrapper:
        wrapper_style = wrapper.get("style", "")
        width_match = re.search(r'width\s*:\s*(\d+)\s*px', wrapper_style)
        if width_match:
            w = int(width_match.group(1))
            if 600 <= w <= 1200:
                issues.append(f"Feste Container-Breite ({w}px) — nicht responsiv")
    return issues


def _check_color_contrast(soup: BeautifulSoup, raw_html: str) -> list[str]:
    issues = []
    css_blocks = [s.get_text() for s in soup.find_all("style")]
    full_css = "\n".join(css_blocks)
    full_text = full_css + " " + raw_html
    garish_combos = [
        (r'(?:color|foreground)\s*:\s*(?:lime|limegreen|#00ff00|#32cd32)',
         r'(?:background|bg)\s*:\s*(?:white|#fff|#ffffff)', "limegreen auf weiß"),
        (r'(?:color|foreground)\s*:\s*(?:yellow|#ffff00|#ffd700)',
         r'(?:background|bg)\s*:\s*(?:white|#fff|#ffffff)', "gelb auf weiß"),
        (r'(?:color|foreground)\s*:\s*(?:red|#ff0000)',
         r'(?:background|bg)\s*:\s*(?:green|#00ff00|#008000)', "rot auf grün"),
        (r'(?:color|foreground)\s*:\s*(?:magenta|#ff00ff)',
         r'(?:background|bg)\s*:\s*(?:white|#fff|#ffffff)', "magenta auf weiß"),
        (r'(?:color|foreground)\s*:\s*(?:orange|#ffa500)',
         r'(?:background|bg)\s*:\s*(?:blue|#0000ff|#000080)', "orange auf blau"),
    ]
    for fg_pat, bg_pat, desc in garish_combos:
        if re.search(fg_pat, full_text, re.IGNORECASE) and re.search(bg_pat, full_text, re.IGNORECASE):
            issues.append(f"Greller Farbkontrast ({desc})")
    body = soup.find("body")
    if body:
        bg = body.get("bgcolor", "").lower()
        if bg in ["#ffff00", "yellow", "#00ff00", "lime", "limegreen"]:
            issues.append(f"Body-Hintergrundfarbe ist auffällig ({bg})")
    return issues


def _extract_phone_from_html(soup: BeautifulSoup) -> str:
    header = soup.find("header")
    footer = soup.find("footer")
    combined = "\n".join(el.get_text() for el in [header, footer] if el)
    for pattern in [
        r"Telefon[:\s]*([\+]?[\d\s\/\-\(\)]{7,})",
        r"Tel[:\s]*([\+]?[\d\s\/\-\(\)]{7,})",
        r"Telefonnummer[:\s]*([\+]?[\d\s\/\-\(\)]{7,})",
    ]:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            phone = match.group(1).strip()
            if len(re.sub(r"\D", "", phone)) >= 7:
                return phone
    return ""


def _find_impressum_link(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    keywords = ["impressum", "imprint", "legal", "gesetzliche angaben"]
    for a in soup.find_all("a", href=True):
        if any(kw in a.get_text(strip=True).lower() for kw in keywords):
            href = a["href"]
            return href if href.startswith("http") else urljoin(base_url, href)
    for a in soup.find_all("a", href=True):
        if any(kw in a["href"].lower() for kw in keywords):
            href = a["href"]
            return href if href.startswith("http") else urljoin(base_url, href)
    return None


def _qualify_lead(quality: dict) -> bool:
    criteria = 0
    if not quality.get("has_ssl", True):
        criteria += 1
    if not quality.get("has_viewport", True):
        criteria += 1
    current_year = datetime.now().year
    if quality.get("copyright_year", current_year) < current_year:
        criteria += 1
    if quality.get("uses_table_layout", False):
        criteria += 1
    if quality.get("uses_deprecated_html", False):
        criteria += 1
    if quality.get("uses_system_fonts", False):
        criteria += 1
    return criteria >= 2


# ==============================================================================
# Lead-Speicherung (TinyDB + Notion-Sync)
# ==============================================================================

def save_lead(lead_data: dict) -> Optional[dict]:
    quality = lead_data.get("quality", {})

    if quality.get("is_perfect", False):
        logger.info("Lead '%s' übersprungen — Website einwandfrei", lead_data.get("name"))
        return None

    if not quality.get("qualifies", False):
        logger.info("Lead '%s' übersprungen — zu wenige Kriterien", lead_data.get("name"))
        return None

    design_criticism = quality.get("design_criticism", [])
    criticism_string = "; ".join(design_criticism) if design_criticism else ""

    # Email: aus Lead-Daten oder aus Impressum
    email = lead_data.get("email", "")
    if not email:
        email = quality.get("impressum_data", {}).get("email", "")
    if not email:
        email = "Manuell prüfen"

    # ─── NOTION DUPLIKAT-CHECK ────────────────────────────────────────────────
    from notion_db import check_if_exists as notion_check_exists
    if notion_check_exists(lead_data.get("name", ""), lead_data.get("website", "")):
        logger.info("[Skip] Lead '%s' bereits in Notion — überspringe", lead_data.get("name"))
        return None

    lead_record = {
        "name": lead_data.get("name", ""),
        "website": lead_data.get("website", ""),
        "phone": lead_data.get("phone", ""),
        "email": email,
        "address": lead_data.get("address", ""),
        "contact_name": lead_data.get("contact_name", ""),
        "contact_phone": quality.get("phone", "") or lead_data.get("contact_phone", ""),
        "contact_email": email,
        "impressum_url": lead_data.get("impressum_url", ""),
        "google_rating": lead_data.get("google_rating", 0),
        "has_ssl": quality.get("has_ssl", False),
        "has_viewport": quality.get("has_viewport", False),
        "has_copyright": quality.get("has_copyright", False),
        "copyright_year": quality.get("copyright_year", 0),
        "copyright_age": quality.get("copyright_age", 0),
        "design_score": len(quality.get("issues", [])),
        "design_issues": quality.get("issues", []),
        "design_criticism": design_criticism,
        "design_criticism_string": criticism_string,
        "reasons": quality.get("reasons", []),
        "reason_string": ", ".join(quality.get("reasons", ["Keine Website"])),
        "branche": lead_data.get("branche", ""),
        "stadt": lead_data.get("stadt", ""),
        "source": lead_data.get("source", "unknown"),
        "screenshot_path": quality.get("screenshot_path", ""),
        "timestamp": datetime.now().isoformat(),
        "notion_synced": False,
    }

    LeadsTable.insert(lead_record)
    logger.info("Lead gespeichert: %s — %s", lead_record["name"], lead_record["reason_string"])
    if criticism_string:
        logger.info("  Design-Kritik: %s", criticism_string)

    from notion_db import sync_to_notion as notion_sync
    notion_sync(lead_record)

    # CSV-Backup
    try:
        _append_to_csv_backup(lead_record)
    except Exception as e:
        logger.error("CSV-Backup fehlgeschlagen: %s", e)

    return lead_record


def _append_to_csv_backup(lead: dict):
    """Schreibt einen Lead in die CSV-Backup-Datei. Erstellt Header wenn nötig."""
    fieldnames = [
        "timestamp", "name", "website", "phone", "email",
        "contact_name", "contact_phone", "contact_email",
        "address", "google_rating", "branche", "stadt", "source",
        "has_ssl", "has_viewport", "has_copyright", "copyright_year",
        "design_score", "reason_string", "design_criticism_string",
        "screenshot_path", "impressum_url",
    ]

    file_exists = BACKUP_CSV_PATH.exists()

    with open(BACKUP_CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()

        row = {k: str(lead.get(k, "")) for k in fieldnames}
        writer.writerow(row)

    logger.debug("CSV-Backup geschrieben: %s", lead.get("name"))


# ==============================================================================
# Hunt-Mode
# ==============================================================================

def _write_hunt_progress(completed: int, total: int, leads: int, active: bool):
    try:
        with open(PROGRESS_PATH, "w") as f:
            json.dump({
                "completed": completed, "total": total,
                "leads": leads, "active": active,
                "updated": datetime.now().isoformat(),
            }, f)
    except Exception as e:
        logger.error("Hunt-Fortschritt schreiben fehlgeschlagen: %s", e)


def get_hunt_progress() -> dict:
    try:
        with open(PROGRESS_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"completed": 0, "total": 0, "leads": 0, "active": False}


async def run_hunt_async(
    stadt: str,
    progress_callback=None,
    lead_callback=None,
    radius_km: int = None,
) -> dict:
    if radius_km is None:
        radius_km = SEARCH_RADIUS_KM

    total_branches = len(HUNT_BRANCHES)
    total_leads = 0
    total_skipped = 0
    completed = 0

    _write_hunt_progress(0, total_branches, 0, True)
    logger.info("=== HUNT MODE für '%s' — %d Branchen (Radius: %dkm) ===", stadt, total_branches, radius_km)

    for i, branche in enumerate(HUNT_BRANCHES):
        branch_leads = 0
        branch_skipped = 0

        logger.info("Hunt %d/%d: %s", i + 1, total_branches, branche)

        try:
            results = await hunt_leads(branche, stadt, max_results=15, radius_km=radius_km)
        except Exception as e:
            logger.error("Branche '%s' fehlgeschlagen: %s", branche, e)
            results = []

        for biz in results:
            website = biz.get("website", "")
            if not website:
                continue

            try:
                quality = await analyze_website_async(website, biz.get("name", ""))
            except Exception as e:
                logger.error("Analyse fehlgeschlagen für '%s': %s", biz.get("name"), e)
                quality = {
                    "has_ssl": False, "has_viewport": False, "has_copyright": False,
                    "copyright_year": 0, "copyright_age": 0, "is_perfect": False,
                    "qualifies": True,
                    "issues": ["Analyse abgestürzt"], "reasons": ["Analyse abgestürzt"],
                    "design_criticism": ["Analyse fehlgeschlagen"],
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
                "source": biz.get("source", "google_maps"),
                "quality": quality,
            }

            saved = save_lead(lead_data)
            if saved:
                branch_leads += 1
                total_leads += 1

                # SOFORTIGER Callback pro Lead (z.B. Telegram-Benachrichtigung)
                if lead_callback:
                    try:
                        if asyncio.iscoroutinefunction(lead_callback):
                            await lead_callback(saved)
                        else:
                            lead_callback(saved)
                    except Exception as e:
                        logger.error("Lead-Callback fehlgeschlagen: %s", e)
            else:
                branch_skipped += 1
                total_skipped += 1

        completed += 1
        _write_hunt_progress(completed, total_branches, total_leads, True)

        if progress_callback:
            if asyncio.iscoroutinefunction(progress_callback):
                await progress_callback(
                    completed, total_branches, total_leads,
                    branche, branch_leads, branch_skipped,
                )
            else:
                progress_callback(
                    completed, total_branches, total_leads,
                    branche, branch_leads, branch_skipped,
                )

        logger.info("Branche '%s': %d Leads, %d aussortiert", branche, branch_leads, branch_skipped)
        await asyncio.sleep(2)

    _write_hunt_progress(completed, total_branches, total_leads, False)
    logger.info("=== HUNT ENDE: %d/%d, %d Leads, %d aussortiert ===",
                completed, total_branches, total_leads, total_skipped)

    return {
        "branches_completed": completed,
        "branches_total": total_branches,
        "leads_exported": total_leads,
        "leads_skipped": total_skipped,
    }


# ==============================================================================
# Lifecycle
# ==============================================================================

async def init_scraper_async(captcha_cb=None, headless: bool = False):
    global browser_manager, captcha_callback
    captcha_callback = captcha_cb
    browser_manager = BrowserManager(headless=headless)
    await browser_manager.launch()

    from notion_db import init_notion as init_notion_client
    init_notion_client(LeadsTable)
    logger.info("Scraper initialisiert — Browser und Notion bereit")


async def shutdown_scraper_async():
    global browser_manager
    if browser_manager:
        await browser_manager.close()
        browser_manager = None
    logger.info("Scraper heruntergefahren")


# ==============================================================================
# Statistik
# ==============================================================================

def get_stats() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    all_leads = LeadsTable.all()
    today_leads = [l for l in all_leads if l.get("timestamp", "").startswith(today)]
    return {
        "total": len(all_leads),
        "today": len(today_leads),
        "last_updated": datetime.now().isoformat(),
    }


def get_best_lead() -> Optional[dict]:
    all_leads = LeadsTable.all()
    if not all_leads:
        return None
    return max(all_leads, key=lambda x: x.get("design_score", 0))


def get_all_leads() -> list[dict]:
    return LeadsTable.all()
