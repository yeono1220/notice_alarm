from __future__ import annotations
import re

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
from dotenv import load_dotenv
RECIPIENTS_DEFAULT = [
    {"name": "관리자", "contact": "01026570090"} 
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
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
})

print("########################################")
print("#  THIS IS  - FINAL CHECK    #")
print("########################################")
load_dotenv() # .env 파일을 읽어서 os.getenv가 값을 찾을 수 있게 해줌

def preprocess_for_ocr(pil_img: Image.Image) -> Image.Image:
    img = np.array(pil_img)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    return Image.fromarray(thresh)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
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
        resp = requests.session.get(link, timeout=HTTP_TIMEOUT)
        resp.encoding = 'utf-8'
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 1. 본문 영역 탐색 (가장 정확한 선택자 순서)
        # 정보대 게시물은 보통 .view-con 안에 .fr-view가 들어있는 구조입니다.
        content_area = (
                soup.select_one(".view-con") or 
                soup.select_one(".fr-view") or 
                soup.select_one("#article_text") or # 추가
                soup.select_one(".board-view-content") # 추가
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
def ask_ai(prompt: str) -> tuple[float, str]:
    try:
        LOG.info("=== [AI CALL START] ===")
        
        # 1. 프롬프트 유니코드 안전화 (UTF-8 강제)
        # 만약 prompt가 유니코드가 아니라면 강제로 utf-8로 변환합니다.
        if isinstance(prompt, bytes):
            safe_prompt = prompt.decode('utf-8')
        else:
            safe_prompt = str(prompt)

        if not client:
            LOG.error("❌ 에러: Gemini Client가 설정되지 않았습니다.")
            return 0.0, "no-client"

        # 2. Gemini 모델 호출 (168라인 부근)
        LOG.info(f"🤖 Calling model: gemini-2.0-flash... (Prompt size: {len(safe_prompt)})")
        # [핵심] 런타임에서 인코딩 에러를 방지하기 위해 
        # 시스템 환경이d 깨져있어도 라이브러리가 UTF-8을 사용하도록 유도합니다.
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=safe_prompt, 
            config={
                'tools': [],
                'automatic_function_calling': {'disable': True}
            }
        )
        print(4)
        # 3. 응답 처리 및 로그 출력 시 인코딩 방어
        # response.text가 한글일 때 LOG.info에서 터지는 것을 repr()로 방어합니다.
        raw_text = response.text if response.text else ""
        LOG.info(f"📥 Raw Response Received: {repr(raw_text)}")

        if not raw_text.strip():
            LOG.warning("⚠️ AI 응답이 비어있습니다.")
            return 0.0, "empty-response"

        # 4. JSON 파싱
        LOG.info("🧩 Parsing JSON from response...")
        json_match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        
        if json_match:
            clean_json = json_match.group(0)
            data = json.loads(clean_json)
            score = float(data.get("score", 0.0))
            reason = data.get("reason", "분석 완료")
            
            # 사유(reason) 출력 시에도 repr() 사용
            LOG.info(f"🎯 Analysis Result - Score: {score}, Reason: {repr(reason)}")
            LOG.info("=== [AI CALL SUCCESS] ===")
            return score, reason
        else:
            LOG.error(f"❌ JSON 패턴을 찾을 수 없습니다. 원문: {repr(raw_text)}")
            raise ValueError("JSON format not found in response")

    except Exception as e:
        # 에러 메시지 자체(예: '본인의_키')를 출력하다 터지지 않게 repr(e) 처리
        LOG.error(f"💥 Critical Error in ask_ai: {repr(e)}")
        import traceback
        LOG.error(traceback.format_exc())
        return 0.0, f"failure: {repr(str(e))}"
def score_notice(profile_text: str, title: str, link: str) -> tuple[float, str]:
    if not profile_text: return 0.0, "no-profile"
    
    # [수정] AI에게 점수(0~1)를 직접 요구하여 relevanceScore 생성
    user_prompt = f"""
    Profile: {profile_text}
    Notice Title: {title}
    Analyze how relevant this notice is to the profile. 
    Respond with a JSON object: {{"score": float, "reason": "short explanation in Korean"}}
    The score must be between 0.0 and 1.0.
    Respond ONLY with a valid JSON object. Do not include markdown code blocks
    """
    
    try:
        response_text = ask_ai(user_prompt)
        # JSON 부분만 추출 (가장 간단한 방식)
        start = response_text.find('{')
        end = response_text.rfind('}') + 1
        res_json = json.loads(response_text[start:end])
        return float(res_json.get("score", 0.0)), res_json.get("reason", "분석 완료")
    except:
        return 0.0, "AI 분석 실패"


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

# 크롤링 대상 게시판 정의 (코드 상단에 없다면 추가하세요)

def run(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    global RECIPIENTS_DEFAULT, BOARDS_DEFAULT
    logger.info("--- [CRAWLER START] ---")
    
    # 1. 인풋 데이터 확보
    user_profile = event.get("userProfile", {})
    profile_summary = user_profile.get("summary", "")
    target_url = event.get("targetUrl") or BASE_URL_DEFAULT
    recipients = event.get("recipients", RECIPIENTS_DEFAULT) # 기본 수신자 사용
    
    base_url = normalize_base(target_url)
    
    # 2. 실행 모드 결정
    # targetUrl에 특정 카테고리가 포함되어 있으면 그 게시판만, 아니면 전체 순회
    target_boards = BOARDS_DEFAULT
    for b in BOARDS_DEFAULT:
        if b['category'] in target_url:
            target_boards = [b]
            break

    all_reports = []
    
    # 3. 핵심 엔진(process_board) 실행
    for board in target_boards:
        LOG.info(f"🚀 {board['name']} 크롤링 시작...")
        report = process_board(board, base_url, profile_summary, recipients)
        all_reports.append(report)

    # 4. 최종 아웃풋 규격 조립
    # 여러 게시판 중 'aligned'(적합) 판정된 글이 하나라도 있는지 확인
    aligned_total = []
    for r in all_reports:
        aligned_total.extend(r.get("posts", []))

    if not aligned_total:
        return {
            "status": "SUCCESS",
            "relevanceScore": 0.0,
            "data": {"message": "적합한 새로운 공지가 없습니다.", "timestamp": datetime.now(TIMEZONE).isoformat()}
        }

    # 가장 최신/점수가 높은 공지 하나를 대표로 반환 (규격 준수)
    best_post = aligned_total[0]
    
    response = {
        "status": "SUCCESS",
        "relevanceScore": 1.0, # aligned 리스트에 들어왔다는 건 적합하다는 뜻
        "data": {
            "category": best_post.get("category", "공지"),
            "title": best_post["title"],
            "sourceName": "고려대학교 정보대학",
            "summary": best_post.get("reason", "요약 생성 실패"),
            "fullContent": best_post.get("full_content", ""), # OCR 결과 포함
            "originalUrl": best_post["link"],
            "images": best_post.get("images", []),
            "timestamp": datetime.now(TIMEZONE).isoformat()
        }
    }

    logger.info(f"--- [CRAWLER END] ---")
    return response
if __name__ == "__main__":
    # 1. 로그 설정
    logging.basicConfig(level=logging.INFO)
    LOG.info("🚀 event.json을 이용한 로컬 테스트를 시작합니다...")

    # 2. event.json 파일 읽기
    event_path = "event.json"
    if os.path.exists(event_path):
        with open(event_path, "r", encoding="utf-8") as f:
            try:
                event_data = json.load(f)
                LOG.info("✅ event.json 파일을 성공적으로 로드했습니다.")
            except json.JSONDecodeError:
                LOG.error("❌ event.json 파일 형식이 올바르지 않습니다.")
                sys.exit(1)
    else:
        # 파일이 없을 경우를 대비한 최소한의 더미 데이터
        LOG.warning("⚠️ event.json이 없어 기본 더미 데이터를 생성합니다.")
        event_data = {
            "userId": "test_user",
            "targetUrl": "https://info.korea.ac.kr/info/board/notice_under.do",
            "userProfile": {
                "summary": "고려대학교 컴퓨터학과 학생, AI 해커톤 및 장학금에 관심 있음"
            },
            "config": {"language": "Korean"}
        }

    # 3. 실제 run 함수 실행
    try:
        # 우리가 정의한 인풋/아웃풋 구조를 그대로 사용하는 run 함수 호출
        final_output = run(event_data)
        
        # 4. 최종 결과 출력
        print("\n" + "="*50)
        print("최종 API 응답 결과 (Output):")
        print(json.dumps(final_output, ensure_ascii=False, indent=2))
        print("="*50)
        
    except Exception as e:
        LOG.error(f"❌ 실행 중 오류 발생: {e}")