import json
import logging
import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from openai import OpenAI
from typing import Any

# === 설정 영역 ===
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("sogang_crawler")

# 시간대 및 조회 범위
TIMEZONE = ZoneInfo("Asia/Seoul")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
LOOKBACK_DAYS = 7  # 최근 7일 이내 공지만 처리

# API 키 설정
SENDER_KEY = "1763d8030dde5f5f369ea0a088598c2fb4c792ab"
SECRET_KEY = "PuyyHGNZ"
APP_KEY = "LROcHEW7abBbFhzc"
TEMPLATE_CODE = "send-article"
PROFILE_PATH = os.getenv("PROFILE_PATH", "user_profile.json")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-4o-mini"

# 수신자 목록
RECIPIENTS = [
    {"name": "고려대 학부생 김수겸", "contact": "01068584123"},
    {"name": "고려대 학부생 고연오", "contact": "01026570090"},
]

# [서강대 API 설정]
SOGANG_API_URL = "https://www.sogang.ac.kr/api/api/v1/mainKo/BbsData/boardList?pageNum=1&pageSize=200&bbsConfigFk=2"
SOGANG_WEB_BASE = "https://www.sogang.ac.kr/ko/academic-support/notices"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def load_profile() -> Any:
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as f:
            profile = json.load(f)
            LOG.info(f"프로필 로드 완료: {len(str(profile))} chars")
            return profile
    except Exception as e:
        LOG.warning(f"프로필 로드 실패: {e}")
        return {}

def score_notice(profile: dict, title: str, link: str) -> bool:
    """OpenAI를 사용하여 사용자와 관련된 공지인지 판단"""
    if not profile or not openai_client: return False
    try:
        resp = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "Is this notice relevant to the user? Reply YES or NO."},
                {"role": "user", "content": f"Profile: {profile}\nTitle: {title}\nLink: {link}"}
            ]
        )
        ans = resp.choices[0].message.content.strip().upper()
        return ans.startswith("YES")
    except Exception as e:
        LOG.error(f"AI Scoring Error: {e}")
        return False

def send_kakao(contact: str, name: str, title: str, link: str) -> dict:
    url = f"https://api-alimtalk.cloud.toast.com/alimtalk/v2.2/appkeys/{APP_KEY}/messages"
    headers = {"X-Secret-Key": SECRET_KEY, "Content-Type": "application/json;charset=UTF-8"}
    payload = {
        "senderKey": SENDER_KEY,
        "templateCode": TEMPLATE_CODE,
        "recipientList": [{
            "recipientNo": contact,
            "templateParameter": {
                "korean-title": f"[서강대 공지] {title}",
                "customer-name": name,
                "article-link": link
            }
        }]
    }
    try:
        res = session.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
        if res.status_code == 200: return res.json()
        return {"status": res.status_code, "msg": res.text}
    except Exception as e: return {"error": str(e)}

def fetch_sogang_posts() -> list[dict]:
    """서강대 API 호출 및 데이터 파싱"""
    LOG.info("서강대 API 요청 시작...")
    try:
        resp = session.get(SOGANG_API_URL, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        
        # 데이터 위치: data['data']['list']
        # 안전하게 데이터 추출
        items = []
        if isinstance(data, dict):
             if 'data' in data and isinstance(data['data'], dict):
                 items = data['data'].get('list', [])
             elif 'list' in data:
                 items = data['list']
        
        LOG.info(f"API 응답 완료. 게시글 수: {len(items)}개")
        
        posts = []
        cutoff_date = datetime.now(TIMEZONE).date() - timedelta(days=LOOKBACK_DAYS)
        LOG.info(f"날짜 기준일: {cutoff_date} 이후 글만 수집")

        for item in items:
            # 1. 날짜 처리 ("20251120095456" -> 8글자 -> 날짜 객체)
            # [수정 포인트] 날짜를 문자로 바꾼 뒤 앞 8자리만 자릅니다.
            date_raw = str(item.get('regDate', ''))
            if not date_raw: 
                continue

            try:
                post_date = datetime.strptime(date_raw[:8], "%Y%m%d").date()
            except ValueError:
                # 날짜 형식이 이상하면 무시하고 다음 글 처리
                continue

            # 기준일보다 옛날 글이면 패스
            if post_date < cutoff_date:
                continue

            # 2. 제목 및 링크 처리 (pkId 사용)
            title = item.get('title') or "제목 없음"
            pk_id = item.get('pkId')
            link = f"{SOGANG_WEB_BASE}/{pk_id}" if pk_id else SOGANG_WEB_BASE
            
            posts.append({"title": title, "link": link})

        LOG.info(f"필터링 후 유효한 최신 글: {len(posts)}개")
        return posts

    except Exception as e:
        LOG.error(f"서강대 크롤링 중 에러: {e}")
        return []

def lambda_handler(event, context):
    posts = fetch_sogang_posts()
    
    if not posts:
        return {"status": "최신 공지 없음", "found": 0}

    profile = load_profile()
    sent_results = []
    
    LOG.info("AI 분석 및 전송 시작...")
    for post in posts:
        if score_notice(profile, post['title'], post['link']):
            LOG.info(f"전송: {post['title']}")
            for user in RECIPIENTS:
                res = send_kakao(user['contact'], user['name'], post['title'], post['link'])
                sent_results.append({"user": user['name'], "title": post['title'], "result": res})
        else:
            LOG.info(f"미전송(관련없음): {post['title']}")
                
    return {
        "site": "Sogang Univ",
        "found": len(posts),
        "sent": len(sent_results),
        "details": sent_results
    }

if __name__ == "__main__":
    print(json.dumps(lambda_handler(None, None), ensure_ascii=False, indent=2))