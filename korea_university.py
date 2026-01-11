from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo
import google.generativeai as genai  # [변경] 구글 AI 라이브러리 추가

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("korea_university")

BASE_URL_DEFAULT = "https://info.korea.ac.kr/info/board/"
TIMEZONE = ZoneInfo("Asia/Seoul")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))

SENDER_KEY = "1763d8030dde5f5f369ea0a088598c2fb4c792ab"
SECRET_KEY = "PuyyHGNZ"
APP_KEY = "LROcHEW7abBbFhzc"
TEMPLATE_CODE = "send-article"

# [변경] OpenAI 설정 제거 및 Gemini 설정 추가
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    LOG.warning("GEMINI_API_KEY is missing; alignment scoring disabled")
    model = None

RECIPIENTS_DEFAULT = [
    {"name": "고려대 학부생 김수겸", "contact": "01068584123"},
    {"name": "고려대 학부생 고연오", "contact": "01026570090"},
]

BOARDS_DEFAULT = [
    {"name": "학부공지", "category": "notice_under"},
    {"name": "학부장학", "category": "scholarship_under"},
    {"name": "정보대소식", "category": "news"},
    {"name": "취업정보", "category": "course_job"},
    {"name": "프로그램", "category": "course_program"},
    {"name": "인턴십", "category": "course_intern"},
    {"name": "공모전", "category": "course_competition"},
]

session = requests.Session()


def normalize_base(url: str | None) -> str:
    if not url:
        return BASE_URL_DEFAULT
    trimmed = url.strip()
    if trimmed.endswith(".do"):
        trimmed = trimmed[: trimmed.rfind("/") + 1]
    return f"{trimmed.rstrip('/')}/"


def score_notice(profile_text: str, title: str, link: str) -> tuple[bool, str]:
    """[변경] Gemini를 사용하여 공지사항 적합도 평가"""
    if not profile_text:
        return False, "no-profile"
    if not model:
        return False, "gemini-disabled"
    
    user_prompt = f"""
Candidate profile text:
{profile_text}

Notice title: {title}
Notice link: {link}

Does this notice strongly align with the candidate’s interests and background? Reply with exactly YES or NO.
"""
    try:
        # [변경] Gemini API 호출
        response = model.generate_content(user_prompt)
        answer_text = response.text.strip().upper()
        
        if answer_text.startswith("YES"):
            return True, answer_text
        if answer_text.startswith("NO"):
            return False, answer_text
            
        LOG.warning("Gemini response not YES/NO: %s", answer_text)
        return False, answer_text or "no-answer"

    except Exception as exc:
        LOG.error("Gemini scoring failed: %s", exc)
        return False, "gemini-error"


def send_kakao(contact: str, template_code: str, template_param: dict[str, str]) -> dict[str, Any]:
    payload = {
        "senderKey": SENDER_KEY,
        "templateCode": template_code,
        "recipientList": [{"recipientNo": contact, "templateParameter": template_param}],
    }
    headers = {"X-Secret-Key": SECRET_KEY, "Content-Type": "application/json;charset=UTF-8"}
    url = f"https://api-alimtalk.cloud.toast.com/alimtalk/v2.2/appkeys/{APP_KEY}/messages"
    
    try:
        resp = session.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            LOG.error("Kakao send failed (%s) %s", resp.status_code, resp.text)
            resp.raise_for_status()
        if resp.headers.get("Content-Type", "").startswith("application/json"):
            return resp.json()
        return {"status": resp.status_code}
    except Exception as e:
        LOG.error("Kakao connection error: %s", e)
        return {"error": str(e)}


def fetch_board(base_url: str, board: dict[str, str]) -> tuple[str, str]:
    page_url = f"{base_url}{board['category']}.do"
    resp = session.get(page_url, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return page_url, resp.text


def parse_posts(html: str, page_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    today = datetime.now(TIMEZONE).date()
    cutoff = today - timedelta(days=LOOKBACK_DAYS - 1)
    posts: list[dict[str, str]] = []
    
    for row in soup.select("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        
        # 날짜 파싱 (고려대 형식: YYYY.MM.DD)
        date_text = cells[-1].get_text(strip=True)
        try:
            row_date = datetime.strptime(date_text, "%Y.%m.%d").date()
        except ValueError:
            continue
            
        if row_date < cutoff:
            continue
            
        link_tag = row.select_one("a.article-title")
        if not link_tag:
            continue
            
        href = (link_tag.get("href") or "").replace("amp;", "")
        title = link_tag.get_text(strip=True)
        posts.append({"title": title, "link": urljoin(page_url, href)})
        
    return posts


def evaluate_posts(profile_text: str, board_name: str, posts: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    aligned: list[dict[str, Any]] = []
    evaluated: list[dict[str, Any]] = []
    
    for post in posts:
        post_copy = dict(post)
        decision, rationale = score_notice(profile_text, post_copy["title"], post_copy["link"])
        
        post_copy["reason"] = rationale
        post_copy["aligned"] = decision
        evaluated.append(post_copy)
        
        if decision:
            aligned.append(post_copy)
            
    return aligned, evaluated


def notify(board: dict[str, str], posts: list[dict[str, Any]], recipients: list[dict[str, str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for post in posts:
        title_prefix = "[적합]" if post.get("aligned") else ""
        title = f"{title_prefix} 고려대 정보대 공지 ({board['name']})\n\n{post['title']}"
        
        for target in recipients:
            params = {
                "korean-title": title,
                "customer-name": target["name"],
                "article-link": post["link"],
            }
            try:
                data = send_kakao(target["contact"], TEMPLATE_CODE, params)
                results.append({
                    "board": board["name"],
                    "title": post["title"],
                    "recipient": target["contact"],
                    "status": data,
                })
            except Exception as exc:
                LOG.exception("Kakao send error: %s", exc)
                results.append({
                    "board": board["name"],
                    "title": post["title"],
                    "recipient": target["contact"],
                    "error": str(exc),
                })
    return results


def process_board(board: dict[str, str], base_url: str, profile_text: str, recipients: list[dict[str, str]]) -> dict[str, Any]:
    try:
        page_url, html = fetch_board(base_url, board)
        posts = parse_posts(html, page_url)
        aligned, evaluated = evaluate_posts(profile_text, board["name"], posts)
    except Exception as exc:
        LOG.exception("Board fetch error for %s: %s", board["name"], exc)
        return {"board": board["name"], "error": str(exc), "posts": [], "sent": [], "evaluated": []}
    
    sent = notify(board, aligned, recipients)
    return {"board": board["name"], "posts": aligned, "sent": sent, "evaluated": evaluated}


def run(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    payload = event or {}
    profile_text = payload.get("user_profile")
    
    # [참고] 로컬 실행 시 user_profile이 없으면 파일에서 읽어오도록 fallback 처리 가능
    if not profile_text:
        # profile_path = os.getenv("PROFILE_PATH", "user_profile.json")
        # ... (기존 로직 유지) ...
        pass

    base_candidate = payload.get("base_url") or payload.get("url")
    base_url = normalize_base(base_candidate)
    
    recipients = payload.get("recipients")
    boards = payload.get("boards")
    recipients = recipients if isinstance(recipients, list) and recipients else RECIPIENTS_DEFAULT
    boards = boards if isinstance(boards, list) and boards else BOARDS_DEFAULT
    
    report = []
    
    # 프로필 텍스트가 꼭 필요하므로 체크
    if not profile_text:
        # 이벤트에 없으면 로컬 파일 시도 (Lambda 환경 고려)
        try:
            with open("user_profile.json", "r", encoding="utf-8") as f:
                profile_text = f.read()
        except:
            pass
            
    if not profile_text:
        return {"error": "user_profile is required"}

    for board in boards:
        report.append(process_board(board, base_url, profile_text, recipients))
        
    total_posts = sum(len(entry["posts"]) for entry in report)
    return {"totalPosts": total_posts, "boards": report}


if __name__ == "__main__":
    profile_path = os.getenv("PROFILE_PATH", "user_profile.json")
    if os.path.isfile(profile_path):
        with open(profile_path, "r", encoding="utf-8") as profile_file:
            profile_text = profile_file.read()
        # 로컬 테스트용 실행
        print(json.dumps(run({"user_profile": profile_text, "base_url": BASE_URL_DEFAULT}), ensure_ascii=False, indent=2))
    else:
        # 파일이 없으면 더미 프로필로 테스트하거나 에러 발생
        print("user_profile.json not found, skipping run.")