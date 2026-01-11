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
import google.generativeai as genai

# 로깅 설정
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("korea_university_crawler")

# 기본 설정
BASE_URL_DEFAULT = "https://info.korea.ac.kr/info/board/"
TIMEZONE = ZoneInfo("Asia/Seoul")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "3")) # 테스트를 위해 3일로 조정 (필요시 변경)

# 카카오톡 설정
SENDER_KEY = "1763d8030dde5f5f369ea0a088598c2fb4c792ab"
SECRET_KEY = "PuyyHGNZ"
APP_KEY = "LROcHEW7abBbFhzc"
TEMPLATE_CODE = "send-article"

# Gemini 설정
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # Gemini 안전성 설정 (BLOCK_NONE으로 설정하여 거부 방지)
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    model = genai.GenerativeModel('gemini-1.5-flash', safety_settings=safety_settings)
else:
    LOG.warning("⚠️ GEMINI_API_KEY가 없습니다. AI 판별 기능이 비활성화됩니다.")
    model = None

# 수신자 목록
RECIPIENTS_DEFAULT = [
    {"name": "고려대 학부생 김수겸", "contact": "01068584123"},
    {"name": "고려대 학부생 고연오", "contact": "01026570090"},
]

# 크롤링 대상 게시판
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
    """Gemini를 사용하여 공지사항 적합도 평가"""
    if not profile_text:
        return False, "no-profile"
    if not model:
        return False, "gemini-disabled"
    
    user_prompt = f"""
    Candidate profile:
    {profile_text}

    Notice title: {title}
    Notice link: {link}

    Analyze if this notice is HIGHLY RELEVANT to the candidate.
    Respond with exactly 'YES' or 'NO'.
    """
    try:
        response = model.generate_content(user_prompt)
        answer_text = response.text.strip().upper()
        
        if "YES" in answer_text:
            return True, "YES"
        if "NO" in answer_text:
            return False, "NO"
            
        LOG.warning(f"Gemini 모호한 응답: {answer_text} -> (제목: {title})")
        return False, "ambiguous"

    except Exception as exc:
        LOG.error(f"Gemini 호출 에러 (제목: {title}): {exc}")
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
            LOG.error(f"카카오 전송 실패: {resp.status_code} {resp.text}")
            return {"status": resp.status_code, "error": resp.text}
        return resp.json()
    except Exception as e:
        LOG.error(f"카카오 연결 에러: {e}")
        return {"error": str(e)}

def fetch_board(base_url: str, board: dict[str, str]) -> tuple[str, str]:
    page_url = f"{base_url}{board['category']}.do"
    resp = session.get(page_url, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return page_url, resp.text

def parse_posts(html: str, page_url: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    today = datetime.now(TIMEZONE).date()
    cutoff = today - timedelta(days=LOOKBACK_DAYS) # 오늘 포함 과거 N일까지
    posts: list[dict[str, str]] = []
    
    for row in soup.select("tr"):
        cells = row.find_all("td")
        if not cells: continue
        
        # 날짜 파싱 (YYYY.MM.DD)
        date_text = cells[-1].get_text(strip=True)
        try:
            row_date = datetime.strptime(date_text, "%Y.%m.%d").date()
        except ValueError:
            continue
            
        if row_date < cutoff:
            continue # 날짜 지난 것은 패스
            
        link_tag = row.select_one("a.article-title")
        if not link_tag: continue
            
        href = (link_tag.get("href") or "").replace("amp;", "")
        title = link_tag.get_text(strip=True)
        posts.append({"title": title, "link": urljoin(page_url, href)})
        
    return posts

def process_board(board: dict[str, str], base_url: str, profile_text: str, recipients: list[dict[str, str]]) -> dict[str, Any]:
    try:
        page_url, html = fetch_board(base_url, board)
        posts = parse_posts(html, page_url)
        
        aligned_posts = []
        evaluated_log = []
        
        LOG.info(f"[{board['name']}] 감지된 최신 글: {len(posts)}개")

        for post in posts:
            decision, reason = score_notice(profile_text, post["title"], post["link"])
            evaluated_log.append({"title": post["title"], "decision": decision, "reason": reason})
            
            if decision:
                aligned_posts.append(post)
                
    except Exception as exc:
        LOG.exception(f"[{board['name']}] 처리 중 에러: {exc}")
        return {"board": board["name"], "error": str(exc), "sent_count": 0}
    
    # 알림 발송
    sent_results = []
    for post in aligned_posts:
        title_msg = f"[적합] 고려대 정보대 공지 ({board['name']})\n\n{post['title']}"
        for target in recipients:
            params = {
                "korean-title": title_msg,
                "customer-name": target["name"],
                "article-link": post["link"],
            }
            res = send_kakao(target["contact"], TEMPLATE_CODE, params)
            sent_results.append({"recipient": target["contact"], "title": post["title"], "result": res})
            
    return {"board": board["name"], "total_posts": len(posts), "aligned_posts": len(aligned_posts), "sent": sent_results}

def run(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    payload = event or {}
    
    # 1. 프로필 확보 (Payload -> Env Var -> File 순서)
    profile_text = payload.get("user_profile")
    if not profile_text:
        profile_text = os.getenv("USER_PROFILE") # [중요] 환경변수에서 로드
    
    if not profile_text:
        # 최후의 수단: 파일 읽기
        try:
            with open("user_profile.json", "r", encoding="utf-8") as f:
                profile_text = f.read()
        except FileNotFoundError:
            pass

    if not profile_text:
        error_msg = "CRITICAL: 'user_profile'이 없습니다. 환경변수 USER_PROFILE을 설정하세요."
        LOG.error(error_msg)
        return {"error": error_msg}

    recipients = payload.get("recipients") or RECIPIENTS_DEFAULT
    boards = payload.get("boards") or BOARDS_DEFAULT
    base_url = normalize_base(payload.get("base_url"))
    
    report = []
    for board in boards:
        report.append(process_board(board, base_url, profile_text, recipients))
        
    return {"status": "completed", "details": report}

if __name__ == "__main__":
    # 로컬 테스트용 (또는 디버깅용)
    print("=== 로컬/디버그 실행 시작 ===")
    
    # 가짜 프로필 (테스트용)
    dummy_profile = """
    저는 컴퓨터학과 학생으로 장학금과 AI 관련 해커톤, 
    그리고 백엔드 개발 인턴십에 관심이 매우 많습니다.
    단순한 인문학 특강이나 일반 행사는 관심 없습니다.
    """
    
    # 환경변수가 없으면 더미 사용
    if not os.getenv("USER_PROFILE"):
        os.environ["USER_PROFILE"] = dummy_profile
        print("DEBUG: 테스트용 더미 프로필을 환경변수에 설정했습니다.")

    result = run({})
    print(json.dumps(result, ensure_ascii=False, indent=2))