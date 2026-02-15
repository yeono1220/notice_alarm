import os
import requests
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

# 기존 임포트 경로 유지
from app.jobs.korea_university import run 

app = FastAPI()

# 1. 백엔드 명세서(이미지)와 100% 일치시킨 데이터 모델
class UserProfile(BaseModel):
    username: str
    phoneNumber: str
    school: str
    major: str
    interestFields: List[str]
    intervalDays: int  # JSON의 Long은 Python의 int로 대응됩니다.
    alarmTime: str

class CallbackConfig(BaseModel):
    enabled: bool = True
    callbackUrl: str = Field(
        default="https://api.allyeojujob.com/ai/callback",
        description="백엔드 알림 수신 기본 주소"
    )
    authToken: str
    

class BatchRequest(BaseModel):
    userId: str
    targetUrls: List[str]  # [수정] 단일 targetUrl -> 리스트 targetUrls
    userProfile: UserProfile
    callback: CallbackConfig

@app.post("/crawl/request")
@app.post("/crawl/request")
async def handle_crawl(request_data: BatchRequest):
    try:
        data_dict = request_data.model_dump()
        all_results = []

        for url in data_dict["targetUrls"]:
            event = {
                "userId": data_dict["userId"],
                "targetUrl": url,
                "userProfile": data_dict["userProfile"],
                "callbackUrl": data_dict["callback"]["callbackUrl"]
            }
            
            # 2차 크롤링 실행
            response_json = run(event) 
            
            # [수정된 로직]
            if isinstance(response_json, dict):
                # 'data' 키에 들어있는 실제 공지 리스트를 가져옴
                site_notices = response_json.get("data", [])
                if isinstance(site_notices, list):
                    all_results.extend(site_notices)
                else:
                    # 혹시나 data 자체가 단일 객체일 경우를 대비
                    all_results.append(site_notices)
            elif isinstance(response_json, list):
                all_results.extend(response_json)

        # 결과 전송
        if data_dict["callback"]["enabled"] and all_results:
            send_to_callback_list(
                data_dict["callback"]["callbackUrl"],
                all_results
            )
            
        return {"status": "SUCCESS", "count": len(all_results), "data": all_results}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}
def send_to_callback_list(callback_url: str, results: List[dict]):
    # 1. 은서님 보안 토큰
    auth_token = "25f58d6aa83f41de4c281e304227f63a864766e0bac8ea0c03d1fb80b1ff59d6"
    
    # 2. [이미지 7번 핵심] 전체 결과의 대표 relevanceScore 계산
    # 공지마다 점수가 있을 텐데, 그 중 가장 높은 점수를 바깥 대표 점수로 뽑습니다.
    global_relevance_score = 0.0
    if results:
        # 각 공지 객체 내부의 relevanceScore 필드를 찾아 최댓값 추출
        scores = [float(r.get("relevanceScore", 0.0)) for r in results if isinstance(r, dict)]
        global_relevance_score = max(scores) if scores else 0.87 # 없으면 기본값

    headers = {
        "Content-Type": "application/json",
        "X-AI-CALLBACK-TOKEN": auth_token 
    }

    # [이미지 7번 그대로] 최상단에 status, relevanceScore, data 리스트 배치
    payload = {
        "status": "SUCCESS",
        "relevanceScore": round(global_relevance_score, 2),
        "data": results  # 여기에 공지 객체 리스트가 들어감
    }

    try:
        response = requests.post(
            callback_url, 
            json=payload, 
            headers=headers, 
            timeout=30
        )
        print(f"📡 은서님 서버 응답 코드: {response.status_code}")
        print(f"🚀 [Callback] 이미지 7번 규격 전송 완료!!")
    except Exception as e:
        print(f"❌ [Callback] 전송 에러: {e}")
        
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, log_level="info")