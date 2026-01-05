# 1. 가볍고 호환성 좋은 파이썬 3.11 이미지 사용
FROM python:3.11-slim

# 2. 작업 폴더 설정
WORKDIR /app

# 3. 필수 라이브러리 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 소스 코드 복사 (모든 .py 파일)
COPY . .

# 5. 실행 명령어 (Gunicorn으로 main:app 실행)
# Cloud Run은 8080 포트에서 대기해야 함
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "120", "main:app"]