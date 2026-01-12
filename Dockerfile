# 1. 일반 파이썬 이미지 사용
FROM python:3.11-slim

# 2. 작업 디렉토리 설정
WORKDIR /app

# 3. 필수 패키지 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 소스 코드 및 프로필 복사
# 이 단계에서 user_profile.json이 /app/user_profile.json 경로에 위치하게 됩니다.
COPY . .

# 5. 환경 변수 및 Python 경로 설정
# 현재 위치를 PYTHONPATH에 추가하여 app.jobs 등을 정상적으로 import 하도록 합니다.
ENV PYTHONPATH=/app
ENV LANG C.UTF-8
ENV LC_ALL C.UTF-8
WORKDIR /app
# 6. 실행 명령어
# korea_university.py가 app/jobs/ 안에 있다면 아래 경로로 실행해야 합니다.
CMD ["python", "app/jobs/korea_university.py"]