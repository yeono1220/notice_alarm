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
    LOG.info("📥 [데이터 수신] 크롤링 프로세스 시작")
    
    # 1. 인풋 데이터 파싱
    user_profile = event.get("userProfile", {})
    major = user_profile.get("major", "컴퓨터학과")  # 기본값 설정
    interest_list = user_profile.get("interestFields", [])

    if not interest_list:
        interest_list = ["AI", "채용", "장학금", "인턴십"] # 기본 관심사 설정

    interests = ", ".join(interest_list)
    combined_profile = f"전공: {major}, 관심분야: {interests}"

    LOG.info(f"👤 분석용 프로필 생성 완료: {combined_profile}") # 로그로 확인 필수!    
    # 설정값 로드
    interval = user_profile.get("intervalDays", 30)
    raw_url = event.get("targetUrl") or BASE_URL_DEFAULT
    # 주소를 무조건 '.../board/' 형태로 정규화
    base_url = normalize_base(raw_url)
    
    all_board_results = []
    all_final_data = [] # 모든 게시판의 추천 공지를 모을 리스트
    total_found_posts = 0
    total_scanned = 0
    # 2. 각 게시판을 '배치 방식'으로 한 번만 순회
    for board in BOARDS_DEFAULT:
        try:
            LOG.info(f"🚀 {board['name']} 게시판 배치 크롤링 시작 (기간: {interval}일)")
            
            # [수정] 신규 배치 함수만 호출합니다. 
            # (함수 내부에서 fetch_board, parse_posts, 배치 AI 분석, 알림까지 한 번에 처리하도록 설계)
            result = process_board_batch(board, base_url, combined_profile, RECIPIENTS_DEFAULT, interval)
            
            all_board_results.append(result)
            if result.get("status") == "SUCCESS":
                all_final_data.extend(result.get("data", []))
                total_scanned += 1            # 검색된 포스트 수 합산 (결과 메시지용)
                
        except Exception as exc:
            LOG.error(f"❌ {board['name']} 처리 중 오류: {exc}")
            continue

    # 3. 최종 상태 반환
    if not all_final_data:
        return {
            "status": "NO_MATCHING_POSTS",
            "message": f"최근 {interval}일 동안 분석을 완료했으나, 추천할만한 새 공지가 없습니다."
        }

    return {
        "status": "SUCCESS",
        "total_boards": total_scanned,
        "recommend_count": len(all_final_data),
        "data": all_final_data # 여기에 AI가 요약한 진짜 데이터가 담깁니다!
    }
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

def extract_text_from_image(img_url: str) -> str:
    """이미지 URL에서 텍스트 추출 (인자 1개로 통일)"""
    try:
        resp = session.get(img_url, timeout=10)
        img = Image.open(BytesIO(resp.content))
        return pytesseract.image_to_string(img, lang="kor+eng").strip()
    except Exception:
        return ""

def fetch_post_content(link: str) -> str:
    """본문과 OCR 텍스트를 합쳐서 반환"""
    try:
        resp = session.get(link, timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, "html.parser")
        content_area = soup.select_one(".view-con") or soup.select_one(".fr-view")
        
        if not content_area: return ""
        
        basic_text = content_area.get_text(" ", strip=True)
        img_tags = content_area.find_all("img")
        ocr_text = ""
        for img in img_tags:
            src = img.get("src")
            if src:
                ocr_text += "\n" + extract_text_from_image(urljoin(link, src))
        
        return (basic_text + ocr_text).strip()
    except Exception as e:
        LOG.error(f"❌ 본문 추출 실패: {e}")
        return ""

# --- 핵심: 팀장님 스타일의 배치 처리 함수 ---
def process_board_batch(board, base_url, profile_text, recipients, interval):
    try:
        # 1. 1차 크롤링 (목록 수집)
        page_url, html = fetch_board(base_url, board)
        posts = parse_posts(html, page_url, interval) 
        
        if not posts: 
            return {"board": board['name'], "status": "NO_POSTS", "posts_count": 0}

        # 2. [배치 호출 1] 제목 리스트 필터링
        titles_block = "\n".join([f"{i}. {p['title']}" for i, p in enumerate(posts)])
        filter_prompt = f"""
        [경고: 반드시 준수] 
        1. 대화 금지, 설명 금지. 
        2. 오직 JSON 리스트 형식([번호, 번호])만 출력해.
        3. 예: [1, 3]

        사용자 프로필: {profile_text}
        목록:
        {titles_block}

        번호: """

        filter_res_raw = ask_ai(filter_prompt)
        
        # 데이터 타입 방어 (리스트/튜플/문자열 처리)
        if isinstance(filter_res_raw, list):
            selected_indices = filter_res_raw
        else:
            filter_res_str = str(filter_res_raw[0] if isinstance(filter_res_raw, tuple) else filter_res_raw)
            selected_indices = [int(i) for i in re.findall(r'\d+', filter_res_str)]

        if not selected_indices:
            return {"board": board['name'], "status": "NO_MATCH", "posts_count": len(posts)}

        # 3. 선택된 공지만 2차 크롤링 (본문/OCR 수집)
        targeted_data = []
        for idx in selected_indices:
            if idx < len(posts):
                # fetch_post_content가 2개의 값을 반환한다고 가정 (내용, 이미지목록)
                # 만약 에러가 난다면 content = fetch_post_content(...)로 수정하세요.
                content_res = fetch_post_content(posts[idx]['link'])
                content = content_res[0] if isinstance(content_res, tuple) else content_res
                
                targeted_data.append({
                    "title": posts[idx]['title'], 
                    "link": posts[idx]['link'], 
                    "content": content
                })

        # 4. [배치 호출 2] 통합 요약
        summary_input = ""
        for i, d in enumerate(targeted_data):
            summary_input += f"\n[ID:{i}]\n제목: {d['title']}\n본문: {d['content']}\n"

        summary_prompt = f"""
        사용자 프로필({profile_text})에 맞춰 다음 공지들을 각각 요약해줘. 
        반드시 아래 JSON 리스트 형식으로 응답해.
        [
          {{"id": 번호, "summary": "요약내용", "title": "원본제목"}}
        ]
        내용:
        {summary_input}
        """
        
        summaries = ask_ai(summary_prompt)

        # 문자열로 왔을 경우를 대비한 파싱 방어
        if isinstance(summaries, str):
            try:
                match = re.search(r'(\[.*\]|\{.*\})', summaries, re.DOTALL)
                summaries = json.loads(match.group(1)) if match else []
            except:
                summaries = []

        # 5. 개별 알림 발송
        sent_count = 0
        if isinstance(summaries, list):
            for s in summaries:
                # 제목 매칭으로 원본 링크 찾기
                target_title = s.get('title', '')
                original_post = next((p for p in targeted_data if target_title in p['title']), None)
                article_link = original_post['link'] if original_post else ""
                
                for target in recipients:
                    params = {
                        "korean-title": f"[{board['name']}] {target_title}",
                        "customer-name": target["name"],
                        "article-link": article_link,
                        "summary": s.get('summary', '내용 요약 실패')
                    }
                    send_kakao(target["contact"], TEMPLATE_CODE, params)
                    sent_count += 1

        return {
            "board": board['name'], 
            "status": "SUCCESS", 
            "posts_count": len(posts), 
            "matched_count": len(summaries) if isinstance(summaries, list) else 0,
            "sent_count": sent_count
        }

    except Exception as e:
        LOG.exception(f"❌ {board['name']} 배치 처리 중 치명적 오류: {e}")
        return {"board": board['name'], "status": "ERROR", "error": str(e), "posts_count": 0}
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
def ask_ai(prompt: str) -> list | dict | str:
    """
    Google GenAI SDK 전용 ask_ai (튜플 및 JSON 마크다운 완벽 방어)
    """
    try:
        # 1. 모델 호출 (본인의 모델명에 맞게 수정하세요. 예: 'gemini-2.0-flash')
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt
        )

        # 2. 텍스트 추출 (response가 튜플로 올 경우를 대비한 방어 로직)
        if isinstance(response, tuple):
            full_text = str(response[0].text)
        else:
            full_text = str(response.text)
        
        full_text = full_text.strip()

        # 3. JSON 추출 (마크다운 ```json ... ``` 제거)
        if '[' in full_text or '{' in full_text:
            # 정규표현식으로 JSON 블록만 추출
            match = re.search(r'(\[.*\]|\{.*\})', full_text, re.DOTALL)
            if match:
                clean_json = match.group(1)
                try:
                    return json.loads(clean_json)
                except json.JSONDecodeError:
                    LOG.error(f"❌ JSON 파싱 실패 (원문): {full_text[:100]}")
                    return full_text # 실패 시 텍스트라도 반환
            
        return full_text

    except Exception as e:
        LOG.error(f"💥 ask_ai 호출 중 에러 발생: {e}")
        # 리스트가 필요한 프롬프트면 빈 리스트, 아니면 빈 문자열 반환
        return [] if "리스트" in prompt or "[" in prompt else ""# 점수가 높은 게시물의 상세 페이지에 접속하여 본문 텍스트와 이미지 URL 목록을 추출합니다.
# [korea_university.py] 함수의 첫 줄
def process_board_batch(board, base_url, profile_text, recipients, interval):
    try:
        # 1. 1차 크롤링 (전달받은 interval 사용)
        page_url, html = fetch_board(base_url, board)
        posts = parse_posts(html, page_url, interval) 
        
        # ... (나머지 로직 동일)        posts = parse_posts(html, page_url, interval) 
        
        if not posts: 
            return {"board": board['name'], "status": "NO_POSTS", "data": []}

        # 2. [배치 호출 1] 제목 리스트 필터링
        titles_block = "\n".join([f"{i}. {p['title']}" for i, p in enumerate(posts)])
        filter_prompt = f"프로필: {profile_text}\n목록:\n{titles_block}\n관심 번호만 JSON 리스트로 응답."

        filter_res = ask_ai(filter_prompt)
        
        # 타입 방어
        if isinstance(filter_res, list):
            selected_indices = filter_res
        else:
            filter_res_str = str(filter_res[0] if isinstance(filter_res, tuple) else filter_res)
            selected_indices = [int(i) for i in re.findall(r'\d+', filter_res_str)]

        if not selected_indices:
            return {"board": board['name'], "status": "NO_MATCH", "data": []}

        # 3. 선택된 공지만 2차 크롤링 (본문/OCR 수집)
        targeted_data = []
        for idx in selected_indices:
            if idx < len(posts):
                content_res = fetch_post_content(posts[idx]['link'])
                content = content_res[0] if isinstance(content_res, tuple) else content_res
                targeted_data.append({
                    "title": posts[idx]['title'], 
                    "link": posts[idx]['link'], 
                    "content": content
                })

        # 4. [배치 호출 2] 통합 요약
        summary_input = "".join([f"\n[ID:{i}] 제목:{d['title']}\n본문:{d['content']}\n" for i, d in enumerate(targeted_data)])
        summary_prompt = f"프로필({profile_text})에 맞춰 각 공지를 요약해. JSON 리스트 [{{'title':'', 'summary':''}}] 형식으로 응답.\n내용:\n{summary_input}"
        
        summaries = ask_ai(summary_prompt)

        # 문자열로 왔을 경우 파싱 시도
        if isinstance(summaries, str):
            try:
                match = re.search(r'(\[.*\]|\{.*\})', summaries, re.DOTALL)
                summaries = json.loads(match.group(1)) if match else []
            except:
                summaries = []

        # 5. 결과 조립 (백엔드에 전달할 데이터)
        final_data = []
        if isinstance(summaries, list):
            for s in summaries:
                t_title = s.get('title', '공지')
                orig = next((p for p in targeted_data if t_title in p['title']), None)
                final_data.append({
                    "board_name": board['name'],
                    "title": t_title,
                    "summary": s.get('summary', ''),
                    "link": orig['link'] if orig else ""
                })

        return {
            "board": board['name'],
            "status": "SUCCESS",
            "data": final_data # 백엔드가 가져갈 핵심 데이터
        }

    except Exception as e:
        LOG.exception(f"❌ {board['name']} 처리 실패: {e}")
        return {"board": board['name'], "status": "ERROR", "data": []}
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