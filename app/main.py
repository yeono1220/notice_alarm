import os
import requests
import uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI
from pydantic import BaseModel
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
    callbackUrl: str
    authToken: str

class BatchRequest(BaseModel):
    userId: str
    targetUrl: str
    userProfile: UserProfile
    summary: str
    callback: CallbackConfig
@app.post("/crawl/request")
async def handle_crawl(request_data: BatchRequest):
    try:
        data_dict = request_data.model_dump()
        
        # [핵심] run 함수가 event.get("userProfile")을 사용하므로 키 이름을 맞춰줍니다.
        event = {
            "userId": data_dict["userId"],
            "targetUrl": data_dict["targetUrl"],
            "userProfile": data_dict["userProfile"], # 'profile'이 아니라 'userProfile'로 전달
            "callbackUrl": data_dict["callback"]["callbackUrl"]
        }
        
        print(f"DEBUG: Passing event to run: {event}")
        result = run(event)
        
        # [방어 코드] result가 None이거나 실패한 경우 처리
        if not result or result.get("status") != "SUCCESS":
            msg = result.get("message") if result else "결과 없음"
            print(f"⚠️ 크롤러 응답 미흡: {msg}")
            return {"status": "SKIPPED", "message": msg}

        # [데이터 전송] run 함수의 리턴 구조(단일 dict)에 맞춰 callback 실행
        if data_dict["callback"]["enabled"]:
            # run 함수는 이미 'data' 안에 dict를 담아 보내주므로 그대로 전달하거나 가공
            send_to_callback(
                data_dict["callback"]["callbackUrl"],
                data_dict["userId"],
                result
            )
            
        return {"status": "SUCCESS", "message": "프로세스 완료"}
        
    except Exception as e:
        print(f"💥 상세 에러: {str(e)}")
        return {"status": "ERROR", "message": str(e)}

def send_to_callback(callback_url: str, user_id: str, result: dict):
    """최종 규격에 맞춰 백엔드로 전송"""
    # run 함수가 이미 'data'에 필요한 필드를 채워서 줍니다.
    item = result.get("data") 
    if not item: return

    payload = {
        "status": "SUCCESS",
        "relevanceScore": result.get("relevanceScore", 0.0),
        "data": item # 이미 category, title, summary, originalUrl 등이 들어있음
    }

    try:
        requests.post(callback_url, json=payload, timeout=30)
        print("🚀 [Callback] 전송 완료")
    except Exception as e:
        print(f"❌ [Callback] 실패: {e}")
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, log_level="info")