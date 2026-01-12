from __future__ import annotations

import pytesseract
from PIL import Image
from io import BytesIO
import json

import logging
import sys
import os
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from zoneinfo import ZoneInfo
import numpy as np
import cv2
from google import genai  # 신형 라이브러리

def preprocess_for_ocr(pil_img: Image.Image) -> Image.Image:
    img = np.array(pil_img)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    return Image.fromarray(thresh)

pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'
def extract_text_from_image(img_url: str, parent_link: str) -> str:
    try:
        resp = session.get(img_url, timeout=HTTP_TIMEOUT)
        # 로그에 원본 게시글 링크(parent_link)를 포함하여 출력
        LOG.info(f"📸 이미지 다운로드 시도: {img_url} (출처: {parent_link})")
        LOG.info(f"   └ 응답: {resp.status_code}, 타입: {resp.headers.get('Content-Type')}")

        if "image" not in resp.headers.get("Content-Type", "").lower():
            LOG.error(f"   └ 실패: 이미지가 아님 ({img_url})")
            return ""

        img = Image.open(BytesIO(resp.content))
        processed = preprocess_for_ocr(img)

        text = pytesseract.image_to_string(
            processed,
            lang="kor+eng",
            config="--oem 3 --psm 6"
        )
        LOG.info(f"   └ OCR 처리 완료 (글자 수: {len(text.strip())})")
        return text.strip()
    except Exception as e:
        LOG.error(f"   └ OCR 실패 ({img_url}): {e}")
        return ""

# --- 추가된 2차 크롤링 함수 ---
def fetch_post_content(link: str) -> tuple[str, list[str]]:
    try:
        resp = session.get(link, timeout=HTTP_TIMEOUT)
        resp.encoding = 'utf-8'
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 1. 본문 영역 탐색 (가장 정확한 선택자 순서)
        # 정보대 게시물은 보통 .view-con 안에 .fr-view가 들어있는 구조입니다.
        content_area = (
            soup.select_one(".view-con") or 
            soup.select_one(".fr-view") or 
            soup.select_one(".article-view") or
            soup.select_one(".re-view")
        )
        
        if content_area:
            text = content_area.get_text(" ", strip=True)
            
            # 2. 이미지 추출 (보여주신 태그 구조 반영)
            img_tags = content_area.find_all("img")
            img_urls = []
            
            for img in img_tags:
                # src와 data-path를 모두 확인
                src = img.get("src") or img.get("data-path")
                
                if src:
                    # 필터링: 에디터 아이콘이나 아주 작은 이미지는 제외 (OCR 효율성)
                    if any(x in src for x in ["/icon/", "base64", "emoji"]):
                        continue
                    
                    # 상대 경로(/_res/...)를 절대 경로로 결합
                    # urljoin은 link가 https://info.korea.ac.kr/... 이므로 알아서 합쳐줍니다.
                    full_url = urljoin(link, src)
                    img_urls.append(full_url)
            
            LOG.info(f"✅ 이미지 감지 성공: {len(img_urls)}개 발견 (URL: {link})")
            return text, img_urls
            
        LOG.warning(f"⚠️ 본문 영역 탐색 실패: {link}")
        return "본문을 찾을 수 없습니다.", []
        
    except Exception as e:
        LOG.error(f"❌ 2차 크롤링 에러: {e}")
        return f"에러 발생: {e}", []
    
BASE_URL_DEFAULT = "https://info.korea.ac.kr/info/board/"
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
SENDER_KEY = os.getenv("KAKAO_SENDER_KEY")
SECRET_KEY = os.getenv("KAKAO_SECRET_KEY")
APP_KEY = os.getenv("KAKAO_APP_KEY")
TEMPLATE_CODE = "send-article"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)] 
)
LOG = logging.getLogger(__name__)

# 환경 변수 및 설정
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))
TIMEZONE = ZoneInfo("Asia/Seoul")

# [핵심 수정] 신형 라이브러리 설정 방식
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    LOG.warning("GEMINI_API_KEY is missing!")
    client = None

def ask_ai(prompt: str) -> str:
    try:
        if not client: return "ERROR: NO CLIENT"
        
        # [수정] 2026년 표준 모델명과 신규 라이브러리 호출 규격 적용
        response = client.models.generate_content(
            model="gemini-2.0-flash", # 1.5-flash 대신 2.0-flash 권장
            contents=prompt,
            config={
                'tools': [], # AFC 등 불필요한 기능 차단으로 할당량 절약
                'automatic_function_calling': {'disable': True}
            }
        )
        return response.text.strip()
    except Exception as e:
        LOG.error(f"AI 호출 에러: {e}")
        return "ERROR"
def score_notice(profile_text: str, title: str, link: str) -> tuple[bool, str]:
    if not profile_text: return False, "no-profile"
    
    # 테스트를 위해 기준을 조금 완화하거나 명확히 지시
    user_prompt = f"""
    Profile: {profile_text}
    Notice: {title}
    Analyze if this is relevant. Respond ONLY with 'YES' or 'NO'.
    """
    
    answer_text = ask_ai(user_prompt).upper()
    LOG.info(f"🤖 AI 답변 ({title[:20]}...): {answer_text}")
    
    if "YES" in answer_text: return True, "YES"
    return False, "NO"

# ... (나머지 send_kakao, fetch_board 등 기존 함수들은 그대로 유지) ...
# (기존에 잘 돌아가던 파싱 및 알림 로직은 그대로 두셔도 됩니다)
BASE_URL_DEFAULT = "https://info.korea.ac.kr/info/board/"
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))

# [변경] OpenAI 설정 제거 및 Gemini 설정 추가


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

# [추가] AI 제공자를 환경변수에서 선택 (기본값: gemini)
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower() 
# OpenAI 키도 필요하면 여기서 불러오기 (나중을 위해)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
# app/jobs/korea_university.py 내 send_kakao 수정

# app/jobs/korea_university.py 의 send_kakao 함수 수정
def send_kakao(contact: str, template_code: str, template_param: dict[str, str]) -> dict[str, Any]:
    payload = {
        "senderKey": SENDER_KEY,
        "templateCode": template_code,
        "recipientList": [{"recipientNo": contact, "templateParameter": template_param}],
    }
    headers = {"X-Secret-Key": SECRET_KEY, "Content-Type": "application/json;charset=UTF-8"}
    url = f"https://api-alimtalk.cloud.toast.com/alimtalk/v2.2/appkeys/{APP_KEY}/messages"
    
    try:
        # [수정] POST 요청이 먼저 와야 합니다.
        resp = session.post(url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)
        # [수정] 그 후에 로그를 찍어야 NameError가 발생하지 않습니다.
        LOG.info(f"Kakao API 응답 상태: {resp.status_code}")
        LOG.info(f"Kakao API 응답 본문: {resp.text}")
        if resp.status_code != 200:
            LOG.error("Kakao send failed (%s) %s", resp.status_code, resp.text)
            return {"error": "API_STATUS_ERROR", "status": resp.status_code}
            
        return resp.json() if "application/json" in resp.headers.get("Content-Type", "") else {"status": resp.status_code}
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
        
        # 필드 초기화
        post_copy["full_content"] = ""
        post_copy["images"] = []

        if decision: 
            LOG.info(f"🔍 [분석 시작] 제목: {post_copy['title']}")
            full_text, img_urls = fetch_post_content(post_copy["link"])
            
            ocr_combined_text = ""
            for idx, url in enumerate(img_urls):
                # 이미지별로 순번과 링크를 로그에 남김
                ocr_result = extract_text_from_image(url, post_copy["link"])
                if ocr_result:
                    ocr_combined_text += f"\n\n--- [이미지 #{idx+1} 텍스트 시작] ---\n{ocr_result}\n--- [이미지 #{idx+1} 텍스트 끝] ---\n"
            
            # 최종 결합 및 할당
            post_copy["full_content"] = (full_text + ocr_combined_text).strip()
            post_copy["images"] = img_urls

            # 로그로 결합 결과 확인
            LOG.info(f"📊 [결합 완료] {post_copy['title']}")
            LOG.info(f"   └ 본문 텍스트 길이: {len(full_text)}")
            LOG.info(f"   └ 이미지 OCR 텍스트 길이: {len(ocr_combined_text)}")
            LOG.info(f"   └ 최종 full_content 길이: {len(post_copy['full_content'])}")
            
            aligned.append(post_copy)
            
        evaluated.append(post_copy)
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
    
    # [설정] 카카오 전송을 잠시 막고 싶을 때 아래를 주석 처리합니다.
    # sent = notify(board, aligned, recipients) TODO 
    sent = [] 
    LOG.info(f"📢 [전송 스킵] {board['name']} 적합 공지 {len(aligned)}건 수집 완료")
    
    return {"board": board["name"], "posts": aligned, "sent": sent, "evaluated": evaluated}

# app/jobs/korea_university.py 의 run 함수 수정 제안
def run(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    payload = event or {}
    # 1. 우선순위: event 전달값 -> 환경변수 -> 로컬 파일
    profile_text = payload.get("user_profile") or os.getenv("USER_PROFILE")
    
    if not profile_text:
        try:
            # Dockerfile에서 복사된 user_profile.json 확인
            profile_path = os.getenv("PROFILE_PATH", "user_profile.json")
            with open(profile_path, "r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                    profile_text = data.get("summary") or data.get("profile") or str(data)
                except json.JSONDecodeError:
                    f.seek(0)
                    profile_text = f.read()
        except Exception as e:
            LOG.error(f"프로필 로드 실패: {e}")

    if not profile_text:
        return {"error": "user_profile is required and not found"}

    # --- 실제 크롤링 실행 로직 ---
    base_url = payload.get("base_url") or BASE_URL_DEFAULT
    all_results = []

    for board in BOARDS_DEFAULT:
        LOG.info(f"🚀 {board['name']} 크롤링 시작...")
        # process_board 내부에서 fetch_post_content와 이미지 OCR이 실행됨
        result = process_board(board, base_url, profile_text, RECIPIENTS_DEFAULT)
        all_results.append(result)

    LOG.info(f"✅ 총 {len(all_results)}개 게시판 작업 완료")
    return {"status": "success", "results": all_results}

# app/jobs/korea_university.py 하단 수정 제안

if __name__ == "__main__":
    # 1. 로그 레벨을 강제로 INFO로 설정하여 출력 확인
    logging.basicConfig(level=logging.INFO)
    LOG.info("🚀 크롤링 작업을 시작합니다...")

    # 2. 로컬 파일이나 환경 변수에서 프로필 로드 시도
    profile_text = os.getenv("USER_PROFILE")
    if not profile_text:
        profile_path = os.getenv("PROFILE_PATH", "user_profile.json")
        if os.path.exists(profile_path):
            with open(profile_path, "r", encoding="utf-8") as f:
                profile_text = f.read()
    
    # 3. 프로필이 없더라도 테스트용 더미 데이터로라도 실행 강제
    if not profile_text:
        LOG.warning("⚠️ 프로필을 찾을 수 없어 테스트 프로필로 실행합니다.")
        profile_text = "고려대학교 컴퓨터학과 학생, AI 해커톤 및 장학금에 관심 있음"

    # 4. 실제 실행
    event_data = {
        "user_profile": profile_text,
        "base_url": BASE_URL_DEFAULT
    }
    
    try:
        # 모든 게시판 순회 실행
        results = []
        for board in BOARDS_DEFAULT:
            res = process_board(board, BASE_URL_DEFAULT, profile_text, RECIPIENTS_DEFAULT)
            results.append(res)
        
        # 결과 출력 (이 로그가 Cloud Run에 남아야 함)
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
        LOG.info("✅ 모든 작업이 완료되었습니다.")
    except Exception as e:
        LOG.error(f"❌ 실행 중 치명적 오류 발생: {e}")