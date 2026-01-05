import os
from dotenv import load_dotenv
from google import genai

# 1. .env 파일에서 비밀번호 꺼내오기
load_dotenv()
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# 2. 클라이언트 생성
if not GEMINI_API_KEY:
    print("에러: .env 파일을 찾을 수 없거나 키가 없습니다.")
else:
    client = genai.Client(api_key=GEMINI_API_KEY)

    # 3. Gemini 실행
    try:
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents="안녕! 나 이제 보안까지 철저하게 지키는 중이야."
        )
        print("\n--- Gemini의 답변 ---")
        print(response.text)
        print("---------------------")
    except Exception as e:
        print(f"실행 중 에러 발생: {e}")