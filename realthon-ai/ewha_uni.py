from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo
from openai import OpenAI

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("ewha_uni")

BASE_URL = "https://www.ewha.ac.kr"
LIST_URL = "https://www.ewha.ac.kr/ewha/news/notice.do"
TIMEZONE = ZoneInfo("Asia/Seoul")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
LOOKBACK_DAYS = 7

# 카카오 알림톡 (KU랑 동일)
SENDER_KEY = "1763d8030dde5f5f369ea0a088598c2fb4c792ab"
SECRET_KEY = "PuyyHGNZ"
APP_KEY = "LROcHEW7abBbFhzc"
TEMPLATE_CODE = "send-article"

PROFILE_PATH = os.getenv("PROFILE_PATH", "user_profile.json")
OPENAI_MODEL = "gpt-5-nano-2025-08-07"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

RECIPIENTS = [
    {"name": "고려대 학부생 김수겸", "contact": "01068584123"},
    {"name": "고려대 학부생 고연오", "contact": "01026570090"},
]

BOARDS = [
    {"name": "이화여대 공지사항", "category": "notice"},
]

session = requests.Session()
profile_cache = None

# ------------------------
# OpenAI init
# ------------------------
openai_client: Optional[OpenAI] = None

if OPENAI_API_KEY:
    try:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as exc:
        LOG.warning("Failed to init OpenAI client: %s", exc)
else:
    LOG.warning("OPENAI_API_KEY not set; alignment scoring disabled")


# ------------------------
# Load profile
# ------------------------
def load_profile():
    global profile_cache
    if profile_cache is not None:
        return profile_cache
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            profile_cache = json.load(f)
            LOG.info("Loaded profile: %s", PROFILE_PATH)
    except Exception as e:
        LOG.warning("No profile found: %s", e)
        profile_cache = None
    return profile_cache


# ------------------------
# OpenAI scoring
# ------------------------
def score_notice(profile: dict[str, Any], title: str, link: str):
    if not profile:
        return False, "no-profile"
    if not openai_client:
        return False, "openai-disabled"

    prompt = f"""
Candidate profile:
{json.dumps(profile, ensure_ascii=False)}

Notice title: {title}
Notice link: {link}

Respond ONLY YES or NO.
"""

    try:
        resp = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Respond ONLY YES or NO."},
                {"role": "user", "content": prompt},
            ],
        )
        raw = resp.choices[0].message.content.strip().upper()
        return raw.startswith("YES"), raw
    except Exception as e:
        LOG.error("OpenAI scoring error: %s", e)
        return False, "openai-error"


# ------------------------
# Kakao sending
# ------------------------
def send_kakao(contact: str, template_code: str, template_param: dict):
    payload = {
        "senderKey": SENDER_KEY,
        "templateCode": template_code,
        "recipientList": [{"recipientNo": contact, "templateParameter": template_param}],
    }
    headers = {
        "X-Secret-Key": SECRET_KEY,
        "Content-Type": "application/json;charset=UTF-8",
    }
    url = f"https://api-alimtalk.cloud.toast.com/alimtalk/v2.2/appkeys/{APP_KEY}/messages"

    resp = session.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json() if resp.headers.get("Content-Type","").startswith("application/json") else {}


# ------------------------
# Ewha — Board Fetch
# ------------------------
def fetch_board(board: dict[str, str]):
    params = {"viewPage": 1}
    resp = session.get(LIST_URL, params=params, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return LIST_URL, resp.text


# ------------------------
# Ewha — Parse Posts (핵심)
# ------------------------
def parse_posts(html: str, base_url: str):
    soup = BeautifulSoup(html, "html.parser")
    today = datetime.now(TIMEZONE).date()
    cutoff = today - timedelta(days=LOOKBACK_DAYS - 1)

    posts = []

    # HTML 구조: <tbody> 안의 <tr>
    for row in soup.select("tbody tr"):
        cols = row.find_all("td")
        if len(cols) < 5:
            continue

        # 제목
        a = cols[2].find("a")
        if not a:
            continue
        title = a.get_text(strip=True)
        href = a.get("href")
        url = urljoin(base_url, href)

        # 날짜
        date_text = cols[4].get_text(strip=True)
        try:
            row_date = datetime.strptime(date_text, "%Y.%m.%d").date()
        except:
            continue

        if row_date >= cutoff:
            posts.append({"title": title, "link": url})

    return posts


# ------------------------
# Evaluate with OpenAI
# ------------------------
def evaluate_posts(board_name: str, posts: list[dict[str,str]]):
    profile = load_profile()
    aligned = []
    evaluated = []

    for p in posts:
        yes, reason = score_notice(profile, p["title"], p["link"])
        record = {**p, "reason": reason, "aligned": yes}
        evaluated.append(record)

        LOG.info("[%s] %s -> %s", board_name, p["title"], reason)

        if yes:
            aligned.append(record)

    return aligned, evaluated


# ------------------------
# Send notifications
# ------------------------
def notify(board: dict[str, str], posts: list[dict[str, Any]]):
    results = []

    for post in posts:
        title = f"[이화여대 {board['name']}] {post['title']}"
        for r in RECIPIENTS:
            params = {
                "korean-title": title,
                "customer-name": r["name"],
                "article-link": post["link"],
            }
            try:
                data = send_kakao(r["contact"], TEMPLATE_CODE, params)
                results.append({"recipient": r["contact"], "status": data})
            except Exception as e:
                results.append({"recipient": r["contact"], "error": str(e)})

    return results


# ------------------------
# Process board
# ------------------------
def process_board(board: dict[str, str]):
    try:
        page_url, html = fetch_board(board)
        posts = parse_posts(html, page_url)
        aligned, evaluated = evaluate_posts(board["name"], posts)
    except Exception as e:
        LOG.error("Board error: %s", e)
        return {"board": board["name"], "error": str(e), "posts": [], "sent": [], "evaluated": []}

    sent = notify(board, aligned)

    return {
        "board": board["name"],
        "posts": aligned,
        "sent": sent,
        "evaluated": evaluated,
    }


# ------------------------
# Final Crawl
# ------------------------
def crawl():
    report = [process_board(b) for b in BOARDS]
    total = sum(len(r["posts"]) for r in report)

    return {
        "totalPosts": total,
        "boards": report
    }


# ------------------------
# CLI
# ------------------------
if __name__ == "__main__":
    print(json.dumps(crawl(), ensure_ascii=False, indent=2))
