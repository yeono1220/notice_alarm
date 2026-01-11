from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

import requests
from zoneinfo import ZoneInfo

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("sogang_university")

API_URL = "https://www.sogang.ac.kr/api/api/v1/mainKo/BbsData/boardList?pageNum=1&pageSize=200&bbsConfigFk=2"
POST_URL = "https://www.sogang.ac.kr/ko/academic-support/notices"
TIMEZONE = ZoneInfo("Asia/Seoul")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))

SENDER_KEY = os.getenv("KAKAO_SENDER_KEY")
SECRET_KEY = os.getenv("KAKAO_SECRET_KEY")
APP_KEY = os.getenv("KAKAO_APP_KEY")
TEMPLATE_CODE = "send-article"
OPENAI_MODEL = "gpt-5-nano-2025-08-07"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")
OPENAI_TIMEOUT = float(os.getenv("OPENAI_TIMEOUT", "20"))

RECIPIENTS_DEFAULT = [
    {"name": "고려대 학부생 김수겸", "contact": "01068584123"},
    {"name": "고려대 학부생 고연오", "contact": "01026570090"},
]

session = requests.Session()
if not OPENAI_API_KEY:
    LOG.warning("OPENAI_API_KEY is missing; Sogang alignment disabled")


def score_notice(profile_text: str, title: str, link: str) -> tuple[bool, str]:
    if not profile_text:
        return False, "no-profile"
    if not OPENAI_API_KEY:
        return False, "openai-disabled"
    prompt = f"""
Candidate profile text:
{profile_text}

Notice title: {title}
Notice link: {link}

Does this notice strongly align with the candidate’s interests and background? Reply with exactly YES or NO.
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
                    {"role": "system", "content": "Respond only YES or NO."},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=OPENAI_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as exc:
        body = exc.response.text if getattr(exc, "response", None) else ""
        code = exc.response.status_code if getattr(exc, "response", None) else "no-status"
        LOG.error("OpenAI scoring failed (%s): %s", code, body[:300])
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
    return resp.json() if resp.headers.get("Content-Type", "").startswith("application/json") else {"status": resp.status_code}


def fetch_posts() -> list[dict[str, str]]:
    resp = session.get(API_URL, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    entries: list[dict[str, Any]] = []
    if isinstance(data, dict):
        if isinstance(data.get("data"), dict):
            entries = data["data"].get("list") or []
        elif isinstance(data.get("list"), list):
            entries = data["list"]
    today = datetime.now(TIMEZONE).date()
    cutoff = today - timedelta(days=LOOKBACK_DAYS - 1)
    posts: list[dict[str, str]] = []
    for row in entries:
        date_raw = str(row.get("regDate") or "")
        if not date_raw:
            continue
        try:
            row_date = datetime.strptime(date_raw[:8], "%Y%m%d").date()
        except ValueError:
            continue
        if row_date < cutoff:
            continue
        pk = row.get("pkId")
        link = f"{POST_URL}/{pk}" if pk else POST_URL
        title = row.get("title") or "제목 없음"
        posts.append({"title": title, "link": link})
    return posts


def evaluate_posts(profile_text: str, posts: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    aligned: list[dict[str, Any]] = []
    evaluated: list[dict[str, Any]] = []
    for post in posts:
        data = dict(post)
        ok, reason = score_notice(profile_text, data["title"], data["link"])
        data["aligned"] = ok
        data["reason"] = reason
        evaluated.append(data)
        if ok:
            aligned.append(data)
    return aligned, evaluated


def notify(posts: list[dict[str, Any]], recipients: list[dict[str, str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for post in posts:
        title = f"[서강대 공지] {post['title']}"
        for target in recipients:
            params = {
                "korean-title": title,
                "customer-name": target["name"],
                "article-link": post["link"],
            }
            try:
                info = send_kakao(target["contact"], TEMPLATE_CODE, params)
                results.append({"title": post["title"], "recipient": target["contact"], "status": info})
            except Exception as exc:
                LOG.exception("Kakao send error: %s", exc)
                results.append({"title": post["title"], "recipient": target["contact"], "error": str(exc)})
    return results


def run(event: dict[str, Any] | None = None, context: Any | None = None) -> dict[str, Any]:
    payload = event or {}
    profile_text = payload.get("user_profile")
    if not profile_text:
        raise ValueError("user_profile is required")
    recipients = payload.get("recipients")
    recipients = recipients if isinstance(recipients, list) and recipients else RECIPIENTS_DEFAULT
    posts = fetch_posts()
    aligned, evaluated = evaluate_posts(profile_text, posts)
    sent = notify(aligned, recipients)
    return {"source": "sogang_university", "count": len(evaluated), "aligned": len(aligned), "posts": evaluated, "sent": sent}


if __name__ == "__main__":
    sample_profile = os.getenv(
        "SOGANG_PROFILE",
        "Demo profile: student tracking Sogang posts.",
    )
    print(run({"user_profile": sample_profile}))

