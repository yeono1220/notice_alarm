# 1. Base Image (Playwright 포함)
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# 2. 한국 시간대 설정 (가장 중요!)
# 이 설정이 없으면 기본 UTC(영국 시간)로 동작합니다.
ENV TZ=Asia/Seoul
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# 3. 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 브라우저 설치
RUN playwright install chromium

# 5. 코드 복사
COPY . .

# 6. 실행
ENV PORT 8080
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 main:app