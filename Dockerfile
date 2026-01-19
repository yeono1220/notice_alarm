# 1. 파이썬 환경 설정
FROM python:3.11-slim

# 2. 한글 및 로그 버퍼링 설정 (ASCII 에러 방지)
ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUNBUFFERED=1

# 3. 작업 디렉토리 설정

# 4. 필수 라이브러리 설치 (Tesseract OCR 포함)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-kor \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
# 5. 종속성 파일 복사 및 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. [중요] 현재 폴더의 모든 코드를 컨테이너로 복사
WORKDIR /app

COPY . .

# 7. 실행 명령어
CMD ["python", "-m", "app.router", "event.json"]