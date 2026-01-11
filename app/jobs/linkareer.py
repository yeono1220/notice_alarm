from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urljoin

import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("linkareer")

DEFAULT_URL = (
    "https://linkareer.com/list/intern"
    "?filterBy_activityTypeID=5&filterBy_categoryIDs=58"
    "&filterBy_jobTypes=INTERN&filterBy_regionIDs=2"
    "&filterBy_status=OPEN&orderBy_direction=DESC"
    "&orderBy_field=RECENT&page=1"
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
)
PAGE_LOAD_TIMEOUT = int(os.getenv("LINKAREER_NAV_TIMEOUT_SEC", "30"))
WAIT_TIMEOUT = int(os.getenv("LINKAREER_WAIT_TIMEOUT_SEC", "20"))
BROWSER_EXECUTABLE = os.getenv("LINKAREER_BROWSER_PATH", "/opt/chrome/chrome")
CHROMEDRIVER_PATH = os.getenv("LINKAREER_CHROMEDRIVER_PATH", "/opt/chromedriver")
OPENAI_MODEL = "gpt-5-nano-2025-08-07"
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "20"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
SENDER_KEY = os.getenv("KAKAO_SENDER_KEY")
SECRET_KEY = os.getenv("KAKAO_SECRET_KEY")
APP_KEY = os.getenv("KAKAO_APP_KEY")
TEMPLATE_CODE = "send-article"
RECIPIENTS_DEFAULT = [
    {"name": "고려대 학부생 김수겸", "contact": "01068584123"},
    {"name": "고려대 학부생 고연오", "contact": "01026570090"},
]

session = requests.Session()


def _build_driver() -> webdriver.Chrome:
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-setuid-sandbox")
    chrome_options.add_argument("--single-process")
    chrome_options.add_argument("--no-zygote")
    chrome_options.add_argument("--disable-software-rasterizer")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-infobars")
    chrome_options.add_argument("--remote-debugging-port=9222")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument(f"--user-agent={USER_AGENT}")
    chrome_options.add_argument("--window-size=1280,900")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("prefs", {"profile.default_content_setting_values.notifications": 2})

    if BROWSER_EXECUTABLE and os.path.exists(BROWSER_EXECUTABLE):
        chrome_options.binary_location = BROWSER_EXECUTABLE
    else:
        LOG.warning("Browser binary %s not found; relying on system Chrome", BROWSER_EXECUTABLE)

    if CHROMEDRIVER_PATH and os.path.exists(CHROMEDRIVER_PATH):
        service = Service(executable_path=CHROMEDRIVER_PATH)
    else:
        service = Service()
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver


def _normalize_text(element) -> str:
    if not element:
        return ""
    raw = element.get_attribute("innerText") or element.text or ""
    return " ".join(raw.split()).strip()


def _parse_rows(driver: webdriver.Chrome, base_url: str) -> list[dict[str, Any]]:
    rows = driver.find_elements(By.CSS_SELECTOR, "table.recruit-list-table tbody tr")
    listings: list[dict[str, Any]] = []

    for row in rows:
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) < 2:
            continue
        try:
            link_el = cells[1].find_element(By.CSS_SELECTOR, "a[href]")
        except Exception:
            continue
        href = link_el.get_attribute("href") or ""
        if not href:
            continue
        title = _normalize_text(link_el)
        if not title:
            continue

        category_nodes = cells[1].find_elements(By.CSS_SELECTOR, ".recruit-category")
        categories = _normalize_text(category_nodes[0]) if category_nodes else ""

        listing = {
            "company": _normalize_text(cells[0]),
            "title": title,
            "link": urljoin(base_url, href),
            "categories": categories,
            "employmentType": _normalize_text(cells[2]) if len(cells) > 2 else "",
            "region": _normalize_text(cells[3]) if len(cells) > 3 else "",
            "deadline": _normalize_text(cells[4]) if len(cells) > 4 else "",
            "views": _normalize_text(cells[5]) if len(cells) > 5 else "",
            "scraps": _normalize_text(cells[6]) if len(cells) > 6 else "",
        }
        listings.append(listing)

    return listings


def score_listing(profile_text: str, title: str, link: str) -> tuple[bool, str]:
    if not profile_text:
        return False, "no-profile"
    if not OPENAI_API_KEY:
        return False, "openai-disabled"
    prompt = f"""
Candidate profile text:
{profile_text}

Posting title: {title}
Posting link: {link}

Does this posting strongly align with the candidate’s interests? Reply with exactly YES or NO.
"""
    try:
        resp = session.post(
            OPENAI_API_URL,
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": "You are an alignment checker. Respond only YES or NO."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=OPENAI_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as exc:
        response = getattr(exc, "response", None)
        body = response.text if response is not None else ""
        status = response.status_code if response is not None else "no-status"
        LOG.error("OpenAI scoring failed (%s): %s | %r", status, body[:300], exc)
        return False, "openai-error"
    except requests.RequestException as exc:
        LOG.error("OpenAI scoring failed: %s", exc)
        return False, "openai-error"
    except ValueError:
        LOG.error("OpenAI response not valid JSON")
        return False, "openai-invalid-response"

    answer = ""
    for choice in data.get("choices", []):
        msg = choice.get("message") or {}
        content = msg.get("content")
        if content:
            answer = content
            break
    text = (answer or "").strip().upper()
    if text.startswith("YES"):
        return True, text or "YES"
    if text.startswith("NO"):
        return False, text or "NO"
    LOG.warning("OpenAI response not YES/NO: %s", text)
    return False, text or "no-answer"


def eval_listings(profile_text: str, listings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    aligned: list[dict[str, Any]] = []
    evaluated: list[dict[str, Any]] = []
    for item in listings:
        entry = dict(item)
        decision, reason = score_listing(profile_text, entry["title"], entry["link"])
        entry["aligned"] = decision
        entry["reason"] = reason
        evaluated.append(entry)
        if decision:
            aligned.append(entry)
    return aligned, evaluated


def send_kakao(contact: str, template_code: str, params: dict[str, str]) -> dict[str, Any]:
    payload = {
        "senderKey": SENDER_KEY,
        "templateCode": template_code,
        "recipientList": [{"recipientNo": contact, "templateParameter": params}],
    }
    headers = {"X-Secret-Key": SECRET_KEY, "Content-Type": "application/json;charset=UTF-8"}
    url = f"https://api-alimtalk.cloud.toast.com/alimtalk/v2.2/appkeys/{APP_KEY}/messages"
    resp = session.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
    if resp.status_code != 200:
        LOG.error("Kakao send failed (%s) %s", resp.status_code, resp.text)
        resp.raise_for_status()
    if resp.headers.get("Content-Type", "").startswith("application/json"):
        return resp.json()
    return {"status": resp.status_code}


def notify_listings(listings: list[dict[str, Any]], recipients: list[dict[str, str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for post in listings:
        title = f"[적합] 링크커리어 인턴 공고\n\n{post['title']}"
        for target in recipients:
            params = {
                "korean-title": title,
                "customer-name": target["name"],
                "article-link": post["link"],
            }
            try:
                data = send_kakao(target["contact"], TEMPLATE_CODE, params)
                results.append(
                    {
                        "title": post["title"],
                        "recipient": target["contact"],
                        "status": data,
                    }
                )
            except Exception as exc:
                LOG.exception("Kakao send error: %s", exc)
                results.append(
                    {
                        "title": post["title"],
                        "recipient": target["contact"],
                        "error": str(exc),
                    }
                )
    return results


def run(event: dict[str, Any] | None = None, context: Any | None = None) -> dict[str, Any]:
    payload = event or {}
    profile_text = payload.get("user_profile")
    if not profile_text:
        raise ValueError("user_profile is required")
    url = payload.get("url") or DEFAULT_URL
    recipients = payload.get("recipients")
    recipients = recipients if isinstance(recipients, list) and recipients else RECIPIENTS_DEFAULT
    LOG.info("Fetching Linkareer listings from %s", url)

    driver: webdriver.Chrome | None = None
    try:
        driver = _build_driver()
        driver.get(url)
        try:
            WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "table.recruit-list-table tbody tr a[href]")
                )
            )
        except TimeoutException:
            LOG.warning("Timed out waiting for Linkareer rows")

        listings = _parse_rows(driver, url)
        LOG.info("Linkareer scraped %s listings for %s", len(listings), url)
        if not listings:
            snippet = driver.page_source[:5000]
            LOG.warning("Linkareer page rendered but no listings were parsed. Snippet: %s", snippet)
        aligned, evaluated = eval_listings(profile_text, listings)
        sent = notify_listings(aligned, recipients)
        return {
            "source": "linkareer",
            "url": url,
            "count": len(evaluated),
            "aligned": len(aligned),
            "listings": evaluated,
            "sent": sent,
        }
    except Exception as exc:
        LOG.exception("Linkareer crawl failed: %s", exc)
        return {
            "source": "linkareer",
            "url": url,
            "count": 0,
            "listings": [],
            "error": str(exc),
        }
    finally:
        if driver:
            driver.quit()


if __name__ == "__main__":
    from pprint import pprint

    sample_profile = os.getenv(
        "LINKAREER_PROFILE",
        "Demo profile: Korea University CS student seeking AI/ML internships.",
    )
    pprint(run({"url": DEFAULT_URL, "user_profile": sample_profile}))

