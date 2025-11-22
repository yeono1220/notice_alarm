import json
import logging
import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from openai import OpenAI
from typing import Any, Optional

# === 설정 영역 ===
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("sogang_crawler")

# 시간대 및 조회 범위
TIMEZONE = ZoneInfo("Asia/Seoul")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
LOOKBACK_DAYS = 17   # 최근 7일 (필요시 30~90으로 변경)

# API 키 설정
SENDER_KEY = "1763d8030dde5f5f369ea0a088598c2fb4c792ab"
SECRET_KEY = "PuyyHGNZ"
APP_KEY = "LROcHEW7abBbFhzc"
TEMPLATE_CODE = "send-article"
PROFILE_PATH = os.getenv("PROFILE_PATH", "user_profile.json")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
# 고려대 코드와 동일한 모델 혹은 가성비 모델 사용
OPENAI_MODEL = "gpt-4o-mini" 

# 수신자 목록
RECIPIENTS = [
    {"name": "고려대 학부생 김수겸", "contact": "01068584123"},
    {"name": "고려대 학부생 고연오", "contact": "01026570090"},
]

# 서강대 API 설정 (게시글 100개 요청 예시)
SOGANG_API_URL = "https://www.sogang.ac.kr/api/api/v1/mainKo/BbsData/boardList?pageNum=1&pageSize=30&bbsConfigFk=2"
SOGANG_WEB_BASE = "https://www.sogang.ac.kr/ko/academic-support/notices"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
profile_cache: Any | None = None

def load_profile() -> Any:
    global profile_cache
    if profile_cache is not None:
        return profile_cache
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as profile_file:
            profile_cache = json.load(profile_file)
            LOG.info("Loaded profile from %s", PROFILE_PATH)
    except FileNotFoundError:
        LOG.warning("Profile file %s not found", PROFILE_PATH)
        profile_cache = {}
    except json.JSONDecodeError as exc:
        LOG.error("Invalid profile JSON: %s", exc)
        profile_cache = {}
    return profile_cache

def score_notice(profile: dict[str, Any], notice_title: str, notice_link: str) -> tuple[bool, str]:
    """고려대 코드와 완벽하게 동일한 프롬프트 및 로직 사용"""
    if not profile:
        return False, "no-profile"
    if not openai_client:
        return False, "openai-disabled"
    
    profile_block = profile if isinstance(profile, str) else json.dumps(profile, ensure_ascii=False)
    
    # [중요] 고려대 코드와 동일한 프롬프트 구조
    user_prompt = f"""
Candidate profile text:
{profile_block}

Notice title: {notice_title}
Notice link: {notice_link}

Does this notice strongly align with the candidate’s interests and background? Reply with exactly YES or NO.
"""
    try:
        chat_api = getattr(getattr(openai_client, "chat", None), "completions", None)
        if not chat_api:
            return False, "openai-unsupported"
            
        resp = chat_api.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are an alignment checker. Respond only YES or NO."},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = resp.choices[0].message.content if resp.choices else ""
        text = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content) if isinstance(content, list) else (content or "")
        answer = text.strip().upper()
        
        # YES로 시작하면 True
        return answer.startswith("YES"), answer or "no-answer"
        
    except Exception as exc:
        LOG.error("OpenAI scoring failed: %s", exc)
        return False, "openai-error"

def send_kakao(contact: str, template_code: str, template_param: dict) -> dict[str, Any]:
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
    
    try:
        resp = session.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
        if resp.status_code != 200:
            LOG.error("Kakao send failed (%s) %s", resp.status_code, resp.text)
            return {"status": resp.status_code, "msg": resp.text}
        
        LOG.info("Kakao send ok for %s", contact)
        return resp.json()
    except Exception as exc:
        return {"error": str(exc)}

def fetch_sogang_posts() -> list[dict[str, str]]:
    """서강대 API에서 데이터를 가져와 표준 포맷(title, link)으로 변환"""
    try:
        LOG.info(f"서강대 API 요청: {SOGANG_API_URL}")
        resp = session.get(SOGANG_API_URL, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        
        # 데이터 위치 탐색 (data.data.list 또는 data.list)
        items = []
        if isinstance(data, dict):
             if 'data' in data and isinstance(data['data'], dict):
                 items = data['data'].get('list', [])
             elif 'list' in data:
                 items = data['list']
        
        posts = []
        cutoff_date = datetime.now(TIMEZONE).date() - timedelta(days=LOOKBACK_DAYS)
        
        for item in items:
            # 1. 날짜 파싱 (서강대 전용: YYYYMMDD...)
            date_raw = str(item.get('regDate', ''))
            if not date_raw: continue

            try:
                post_date = datetime.strptime(date_raw[:8], "%Y%m%d").date()
            except ValueError: continue

            if post_date < cutoff_date: continue

            # 2. 링크 생성
            title = item.get('title') or "제목 없음"
            pk_id = item.get('pkId')
            link = f"{SOGANG_WEB_BASE}/{pk_id}" if pk_id else SOGANG_WEB_BASE
            
            posts.append({"title": title, "link": link})
            
        LOG.info(f"API Fetch 완료: {len(posts)}개 글 수집됨 (최근 {LOOKBACK_DAYS}일)")
        return posts

    except Exception as exc:
        LOG.error("Sogang fetch error: %s", exc)
        return []

def evaluate_posts(board_name: str, posts: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """고려대 코드와 동일한 평가 로직"""
    profile = load_profile()
    aligned: list[dict[str, Any]] = []
    evaluated: list[dict[str, Any]] = []
    
    for post in posts:
        post_copy = dict(post)
        # AI 판단 호출
        decision, rationale = score_notice(profile, post_copy["title"], post_copy["link"])
        
        post_copy["reason"] = rationale
        post_copy["aligned"] = decision
        evaluated.append(post_copy)
        
        LOG.info(
            "[%s] %s -> %s (aligned=%s)",
            board_name,
            post_copy["title"],
            rationale,
            decision,
        )
        
        if decision:
            aligned.append(post_copy)
            
    return aligned, evaluated

def notify(board_name: str, posts: list[dict[str, str]]) -> list[dict[str, Any]]:
    """고려대 코드와 동일한 알림 로직"""
    results: list[dict[str, Any]] = []
    for post in posts:
        # 제목 앞에 [적합] 태그 추가 여부는 aligned 값에 따라 (여기선 이미 aligned 된 것만 옴)
        title = f"[서강대] {board_name}\n{post['title']}"
        
        for target in RECIPIENTS:
            params = {
                "korean-title": title,
                "customer-name": target["name"],
                "article-link": post["link"],
            }
            try:
                data = send_kakao(target["contact"], TEMPLATE_CODE, params)
                results.append({
                    "board": board_name,
                    "title": post["title"],
                    "recipient": target["contact"],
                    "status": data,
                })
            except Exception as exc:
                LOG.exception("Kakao send error: %s", exc)
                results.append({
                    "board": board_name,
                    "title": post["title"],
                    "recipient": target["contact"],
                    "error": str(exc),
                })
    return results

def process_sogang() -> dict[str, Any]:
    """전체 프로세스 오케스트레이션"""
    board_name = "서강대 학사공지"
    
    # 1. Fetch
    posts = fetch_sogang_posts()
    
    # 2. Evaluate
    aligned_posts, evaluated_posts = evaluate_posts(board_name, posts)
    
    # 3. Notify
    sent = notify(board_name, aligned_posts)
    
    # 4. Return Structure (고려대 코드와 동일한 구조)
    return {
        "board": board_name,
        "posts": aligned_posts,     # 최종 전송 대상
        "sent": sent,               # 전송 결과 상세
        "evaluated": evaluated_posts # 전체 평가 로그 (YES/NO 포함)
    }

def crawl() -> dict[str, Any]:
    # 리포트 생성
    report_entry = process_sogang()
    
    # 고려대 코드와 동일한 최종 JSON 루트 구조
    return {
        "totalPosts": len(report_entry["posts"]),
        "boards": [report_entry]
    }

def lambda_handler(event: dict[str, Any] | None = None, context: Any | None = None) -> dict[str, Any]:
    LOG.info("Lambda trigger: %s", json.dumps(event or {}))
    return crawl()

if __name__ == "__main__":
    # 결과 출력 (JSON 형태)
    print(json.dumps(crawl(), ensure_ascii=False, indent=2))