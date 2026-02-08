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

def send_to_callback(callback_url: str, result: dict, auth_token: str):
    """
    백엔드가 이미 {requestId}를 포함해 완성해서 준 callback_url을 
    수정 없이 그대로 사용하여 결과를 전송합니다.
    """
    # 1. 페이로드 구성 (백엔드 수신 규격)
    payload = {
        "status": "SUCCESS",
        "relevanceScore": result.get("relevanceScore", 0.0),
        "data": result.get("data")
    }

    # 2. 헤더 구성 (authToken이 있다면 함께 전송)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}" # 보안을 위해 토큰 추가
    }

    try:
        # 가공하지 않은 callback_url 그대로 POST!
        response = requests.post(
            callback_url, 
            json=payload, 
            headers=headers, 
            timeout=30
        )
        print(f"🚀 [Callback] 전송 완료: {callback_url} (Status: {response.status_code})")
    except Exception as e:
        print(f"❌ [Callback] 실패: {e}")
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, log_level="info")