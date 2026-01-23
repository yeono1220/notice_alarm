

# 🚀 AI 공지사항 크롤러 서비스 (Crawler Service)

이 프로젝트는 대학교 공지사항을 수집하고, **Gemini AI**를 활용하여 사용자의 전공 및 관심 분야에 맞는 공지사항을 선별하여 요약하는 **GCP Cloud Run** 기반의 마이크로서비스입니다.

## 🏗️ 시스템 아키텍처 및 흐름

1. **Trigger**: Cloud Scheduler가 설정된 주기에 따라 FastAPI 엔드포인트를 호출합니다.
2. **1차 크롤링**: `intervalDays` 설정을 확인하여 기준 날짜 이후의 최신 공지만 목록화합니다.
3. **AI 분석**: 유저의 `interestFields`와 공지 제목을 비교하여 중요도 점수(0~1)를 산출합니다.
4. **2차 크롤링**: 고득점 공지에 한해 상세 본문 및 이미지(OCR)를 추출하고 내용을 요약합니다.
5. **Data Delivery**: 최종 분석된 JSON 결과를 백엔드 서버로 전송합니다.

---

## 📂 주요 파일 구조 및 역할

실행 순서에 따라 함수가 배치되어 있습니다.

* `app/main.py`: 서비스의 입구입니다. Pydantic 모델을 통해 입력 데이터의 유효성을 검증하고 크롤링 작업을 할당합니다.
* `app/jobs/korea_university.py`: 실제 크롤링 및 AI 분석 로직이 포함된 핵심 파일입니다.
* `parse_posts`: 목록 수집 및 날짜 필터링 (중복 수집 방지)
* `score_notice`: 유저 맞춤형 AI 스코어링
* `fetch_post_content`: 상세 본문 및 이미지 URL 추출
* `extract_text_from_image`: Tesseract를 이용한 이미지 내 텍스트 추출(OCR)
* `run`: 전체 프로세스 총괄 및 최종 Output 생성



---

## 🛠️ 설치 및 로컬 실행 방법

### 1. 필수 요구사항

* Python 3.10+
* Tesseract OCR 엔진 (시스템 설치 필요)

### 2. 환경 변수 설정 (`.env`)

프로젝트 루트에 `.env` 파일을 생성하고 다음 정보를 입력하세요.

```env
GEMINI_API_KEY=your_api_key_here
PORT=8080
LOG_LEVEL=INFO

```

### 3. 라이브러리 설치 및 실행

```bash
pip install -r requirements.txt
python app/main.py

```
python app/main.py 안되면 path 설정 - 
(.venv) PS C:\Users\user\crawler-project> 
$env:PYTHONPATH += ";."
python app/main.py
---

## 📡 API 규격 (Interface)

### **POST /crawl**

사용자의 프로필 정보를 받아 맞춤형 크롤링을 수행합니다.

**Request Body 예시:**

```json
{
  "userId": "user_12345",
  "targetUrl": "https://info.korea.ac.kr/info/board/notice_under.do",
  "userProfile": {
    "username": "양은서",
    "major": "컴퓨터공학과",
    "interestFields": ["AI", "BACKEND"],
    "intervalDays": 3,
    "alarmTime": "09:30:00"
  }
}

```

**Response Body 예시:**

```json
{
  "status": "SUCCESS",
  "relevanceScore": 0.95,
  "data": {
    "title": "2026 AI 해커톤 참가자 모집",
    "summary": "AI 분야 역량을 강화할 수 있는 기회로, 백엔드 개발 경험이 있는 학생을 우대합니다.",
    "originalUrl": "https://info.korea.ac.kr/...",
    "timestamp": "2026-01-24T01:18:00Z"
  }
}

```

---

## 💡 협업자 가이드 (Note)

* **비용 효율**: `parse_posts` 함수는 기준 날짜 이전의 글을 발견하면 즉시 루프를 중단하도록 설계되어 불필요한 연산을 방지합니다.
* **알림 발송**: 이 서비스는 분석 결과만 반환합니다. 실제 카카오톡 발송은 백엔드 서버에서 처리해야 합니다.
* **OCR**: 이미지 분석 성능을 높이기 위해 `preprocess_for_ocr`에서 전처리를 수행합니다.

---

