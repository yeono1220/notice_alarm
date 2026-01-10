# 1. 일반 파이썬 이미지 사용 (AWS Lambda 이미지 X)
FROM python:3.11-slim

# 2. 작업 디렉토리 설정
WORKDIR /app

# 3. 필수 패키지 설치
# requirements.txt에 있는 requests, beautifulsoup4, openai 등을 설치합니다.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4. 소스 코드 복사 (모든 Python 파일 및 프로필 데이터)
COPY . .

# 5. 실행 명령어 변경
# AWS Lambda Handler 대신 파이썬 스크립트를 직접 실행합니다.
# (korea_uni.py 하단에 if __name__ == "__main__": 블록이 이미 존재하므로 바로 실행 가능)
CMD ["python", "korea_uni.py"]