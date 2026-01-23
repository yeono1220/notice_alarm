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
load_dotenv() # .env 파일을 읽어서 os.getenv가 값을 찾을 수 있게 해줌
logger = logging.getLogger()
logger.setLevel(logging.INFO)
pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'
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
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "70"))
TIMEZONE = ZoneInfo("Asia/Seoul")
# [추가] AI 제공자를 환경변수에서 선택 (기본값: gemini)
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower() 
# OpenAI 키도 필요하면 여기서 불러오기 (나중을 위해)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
# app/jobs/korea_university.py 내 send_kakao 수정
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    LOG.warning("GEMINI_API_KEY is missing!")
    client = None
# 전체 크롤링 프로세스를 제어
# app/jobs/korea_university.py
def run(event: dict[str, Any], context: Any | None = None) -> dict[str, Any]:
    """
    최종 진입점: main.py로부터 JSON을 받아 전 프로세스를 제어합니다.
    """
    LOG.info("📥 [데이터 수신] 크롤링 프로세스 시작")
    
    # 1. 인풋 데이터 파싱 및 프로필 생성
    user_profile = event.get("userProfile", {})
    major = user_profile.get("major", "")
    interests = ", ".join(user_profile.get("interestFields", []))
    combined_profile = f"전공: {major}, 관심분야: {interests}"
    
    # [에러 해결] 사용자가 보낸 intervalDays를 가져와서 하위 함수에 전달 준비
    interval = user_profile.get("intervalDays", 3)
    
    target_url = event.get("targetUrl") or BASE_URL_DEFAULT
    base_url = normalize_base(target_url)
    
    # 대상 게시판 결정
    target_boards = BOARDS_DEFAULT
    for b in BOARDS_DEFAULT:
        if b['category'] in target_url:
            target_boards = [b]
            break

    total_scanned_count = 0 
    aligned_total = []

    # --- [통합] process_board 함수 없이 여기서 직접 루프를 돕니다 ---
    for board in target_boards:
        try:
            LOG.info(f"🔎 {board['name']} 게시판 분석 시작...")
            
            # [Step 1] 게시판 목록 가져오기
            page_url, html = fetch_board(base_url, board)
            
            # [Step 2] 1차 크롤링: 날짜 필터링 적용 (인자 3개 정상 전달)
            # 이제 parse_posts(html, page_url, interval) 형태로 호출됩니다.
            posts = parse_posts(html, page_url, interval) 
            LOG.info(f"수집된 포스트 타입: {type(posts)}") # 로그로 확인용

            # evaluate_posts 호출 시 posts 리스트를 정확히 전달
            aligned, _ = evaluate_posts(combined_profile, board["name"], posts)
            total_scanned_count += len(posts)
            
            # [Step 3] AI 평가 및 상세 크롤링
            aligned_total.extend(aligned)
            
            
        except Exception as exc:
            LOG.error(f"❌ {board['name']} 처리 중 오류: {exc}")
            continue

    # 2. 상태 세분화 및 결과 조립
    if total_scanned_count == 0:
        return {
            "status": "NO_NEW_POSTS",
            "relevanceScore": 0.0,
            "data": None,
            "message": f"최근 {interval}일 동안 새로운 공지가 없습니다."
        }
            
    if not aligned_total:
        return {
            "status": "NO_MATCHING_POSTS",
            "relevanceScore": 0.0,
            "data": None,
            "message": "신규 공지는 있으나 사용자의 관심사와 일치하는 항목이 없습니다."
        }

    # 성공 시 점수 순 정렬 후 반환
    aligned_total.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    best_post = aligned_total[0]

    # [수정 핵심] 상세 본문(1차+2차 크롤링 결과)을 바탕으로 최종 요약 생성
    final_summary = summarize_content(
        user_profile, 
        best_post["title"], 
        best_post.get("full_content", "")
    )
    
    return {
        "status": "SUCCESS",
        "relevanceScore": best_post.get("relevance_score", 0.0),
        "data": {
            "category": "공지사항",
            "title": best_post["title"],
            "sourceName": "고려대학교 정보대학",
            "summary": final_summary,  # 분석 사유 대신 실제 요약문 삽입
            "originalUrl": best_post["link"],
            "timestamp": datetime.now(TIMEZONE).isoformat()
        }
    }# 입력받은 URL을 크롤링하기 적합한 표준형태로 변환
def normalize_base(url: str | None) -> str: 
    if not url:
        return BASE_URL_DEFAULT
    trimmed = url.strip()
    if trimmed.endswith(".do"):
        trimmed = trimmed[: trimmed.rfind("/") + 1]
    return f"{trimmed.rstrip('/')}/"

# fetch_board(base_url, board): 특정 게시판 카테고리의 URL을 생성하고 해당 페이지의 HTML 소스를 가져옵니다.
def fetch_board(base_url: str, board: dict[str, str]) -> tuple[str, str]:
    page_url = f"{base_url}{board['category']}.do"
    resp = session.get(page_url, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return page_url, resp.text
# HTML에서 공지사항 목록을 추출합니다. interval_days를 기준으로 이전 날짜의 글이 나오면 즉시 중단(break)하여 불필요한 탐색을 방지합니다. 
def parse_posts(html: str, page_url: str, interval_days: int) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    today = datetime.now(TIMEZONE).date()
    
    # LOOKBACK_DAYS 대신 넘겨받은 interval_days 사용
    cutoff = today - timedelta(days=interval_days - 1)
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

# 수집된 목록을 순회하며 AI 점수를 매기고, 기준치(THRESHOLD) 이상인 게시물만 상세 내용을 추출합니다.
def evaluate_posts(profile_text: str, board_name: str, posts: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    LOG.info(f"Evaluating posts for board: {board_name} with {len(posts)} posts")
    aligned: list[dict[str, Any]] = []
    evaluated: list[dict[str, Any]] = []
    THRESHOLD = 0.7
    for post in posts:
        post_copy = dict(post)
        score, rationale = score_notice(profile_text, post_copy["title"], post_copy["link"])
        post_copy["reason"] = rationale
        post_copy["relevance_score"] = score # 실제 점수 저장
        
        # 필드 초기화
        post_copy["full_content"] = ""
        post_copy["images"] = []

        if score >= THRESHOLD:
            LOG.info(f"✅ 적합 판정({score}점): {post_copy['title']}")            
            full_text, img_urls = fetch_post_content(post_copy["link"])
            
            ocr_combined_text = ""
            for idx, url in enumerate(img_urls):
                # 이미지별로 순번과 링크를 로그에 남김
                ocr_result = extract_text_from_image(url)
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
        print('eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', post_copy)
    return aligned, evaluated
# 유저의 전공(major)과 관심 분야(interestFields)를 반영한 프롬프트를 생성하여 AI에게 관련성 점수를 요청합니다.
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
    return ask_ai(user_prompt)
    
    try:
        response_text = ask_ai(user_prompt)
        # JSON 부분만 추출 (가장 간단한 방식)
        start = response_text.find('{')
        end = response_text.rfind('}') + 1
        res_json = json.loads(response_text[start:end])
        return float(res_json.get("score", 0.0)), res_json.get("reason", "분석 완료")
    except:
        return 0.0, "AI 분석 실패"
def summarize_content(user_profile: dict, title: str, full_content: str) -> str:
    """
    [2차 분석] 수집된 본문 전체와 OCR 텍스트를 바탕으로 사용자 맞춤 요약을 생성합니다.
    """
    if not full_content or len(full_content) < 20:
        return "상세 본문 내용이 부족하여 요약을 생성할 수 없습니다."

    interests = ", ".join(user_profile.get("interestFields", []))
    
    summary_prompt = f"""
    당신은 공지사항 요약 전문가입니다. 아래의 공지사항 본문을 읽고, 
    사용자의 관심 분야({interests})를 중심으로 핵심 내용을 3문장 이내로 요약하세요.
    
    공지 제목: {title}
    공지 본문: {full_content}
    
    응답은 요약된 문장만 출력하세요. 마크다운 형식을 사용하지 마세요.
    """
    # ask_ai 함수를 호출하되, 요약문만 받도록 간단히 처리 (또는 전용 호출 로직 작성)
    # 여기서는 기존 ask_ai가 JSON을 기대하므로 요약용은 별도 response.text 추출이 필요할 수 있습니다.
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=summary_prompt
    )
    return response.text.strip()


# genai 클라이언트를 사용하여 Gemini API를 호출하고 결과를 JSON 형태로 파싱하여 반환합니다.
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
# 점수가 높은 게시물의 상세 페이지에 접속하여 본문 텍스트와 이미지 URL 목록을 추출합니다.
def fetch_post_content(link: str) -> tuple[str, list[str]]:
    """본문 텍스트와 이미지 OCR 텍스트를 합쳐서 반환"""
    try:
        # 1. 페이지 요청 (에러 나던 session.get을 requests.get으로 수정)
        resp = session.get(link, timeout=15)
        resp.encoding = 'utf-8'
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # 2. 본문 영역 탐색
        content_area = soup.select_one(".view-con") or soup.select_one(".fr-view")
        
        if not content_area:
            return "본문을 찾을 수 없습니다.", []

        # 3. 기본 텍스트 추출 (BeautifulSoup)
        basic_text = content_area.get_text(" ", strip=True)
        
        # 4. 이미지 태그에서 OCR 텍스트 추출
        img_tags = content_area.find_all("img")
        ocr_combined_text = ""
        img_urls = []
        
        for img in img_tags:
            src = img.get("src") or img.get("data-path")
            if src and not any(x in src for x in ["/icon/", "emoji"]):
                full_url = urljoin(link, src)
                img_urls.append(full_url)
                
                # 이미지에서 글자 읽어오기 (OCR 실행)
                ocr_result = extract_text_from_image(full_url)
                if ocr_result:
                    ocr_combined_text += f"\n[이미지 포함 내용]: {ocr_result}"

        # 5. 일반 텍스트 + OCR 텍스트 합체
        final_full_content = (basic_text + ocr_combined_text).strip()
        
        return final_full_content, img_urls
        
    except Exception as e:
        LOG.error(f"❌ 2차 크롤링(OCR 포함) 에러: {e}")
        return "콘텐츠 로드 실패", []    
def extract_text_from_image(img_url: str) -> str:
    """이미지 URL에서 텍스트를 추출하는 OCR 함수"""
    try:
        # session 대신 requests.get 사용 (에러 방지)
        resp = session.get(img_url, timeout=15)
        if "image" not in resp.headers.get("Content-Type", "").lower():
            return ""

        img = Image.open(BytesIO(resp.content))
        # 과거 코드에 있던 OCR 처리 로직
        text = pytesseract.image_to_string(img, lang="kor+eng", config="--oem 3 --psm 6")
        return text.strip()
    except Exception as e:
        LOG.error(f"❌ OCR 실패: {e}")
        return ""
def preprocess_for_ocr(pil_img: Image.Image) -> Image.Image:
    img = np.array(pil_img)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    return Image.fromarray(thresh)
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


    try:
        page_url, html = fetch_board(base_url, board)
        posts = parse_posts(html, page_url)
        aligned, evaluated = evaluate_posts(profile_text, board["name"], posts)

        print(profile_text,"ddddddddddddddddddddddddddddddddd", board["name"], posts)
        LOG.info(f"📝 {board['name']} 평가 완료: 총 {len(posts)}건 중 {len(aligned)}건 적합")
    except Exception as exc:
        LOG.info("Board fetch error for %s: %s", board["name"], exc)

        return {"board": board["name"], "error": str(exc), "posts": [], "sent": [], "evaluated": []}
    
    # [설정] 카카오 전송을 잠시 막고 싶을 때 아래를 주석 처리합니다.
    # sent = notify(board, aligned, recipients) TODO 
    sent = [] 
    LOG.info(f"📢 [전송 스킵] {board['name']} 적합 공지 {len(aligned)}건 수집 완료")
    
    return {"board": board["name"], "posts": aligned, "sent": sent, "evaluated": evaluated}

# 크롤링 대상 게시판 정의 (코드 상단에 없다면 추가하세요)

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