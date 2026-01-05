import os
import json
import firebase_admin
from firebase_admin import firestore
from flask import Flask, request
from datetime import datetime, timedelta
import pytz

# --- [이전에 만든 크롤러 함수 import] ---
# crawler.py 파일이 같은 폴더에 있다고 가정하거나, 
# 이 파일 안에 크롤러 함수(run_crawler)를 포함시켜야 합니다.
from crawler import run_crawler 

app = Flask(__name__)

# DB 초기화 (Firestore 사용 가정)
# ※ 주의: 로컬 테스트 시에는 서비스 계정 키 파일 경로 설정이 필요할 수 있습니다.
if not firebase_admin._apps:
    firebase_admin.initialize_app()
db = firestore.client()

def check_interval(user_data):
    """
    오늘 알림을 보내야 하는 날인지 확인 (interval_days 체크)
    """
    last_sent = user_data.get('last_sent_at') # ERD에 추가한 컬럼
    interval = user_data.get('interval_days', 1) # 기본값 1일

    # 한 번도 보낸 적 없으면 -> 무조건 발송
    if not last_sent:
        return True

    # 마지막 발송일로부터 며칠 지났는지 계산
    # Firestore Timestamp 객체를 파이썬 datetime으로 변환
    last_date = last_sent.date() if hasattr(last_sent, 'date') else last_sent.date()
    today = datetime.now(pytz.timezone('Asia/Seoul')).date()
    
    diff = (today - last_date).days
    return diff >= interval

@app.route("/", methods=["POST"])
def scheduler_trigger():
    """
    Cloud Scheduler가 매시 정각마다 찌르는 함수
    """
    # 1. 한국 시간 기준 현재 시각 (예: "18:00") 구하기
    korea_tz = pytz.timezone('Asia/Seoul')
    now = datetime.now(korea_tz)
    current_time_str = now.strftime("%H:00") 
    
    print(f"⏰ [Scheduler] 작동 시작: {current_time_str}")

    # 2. DB에서 '알림 시간'이 지금인 유저 찾기
    # ERD의 users 테이블 -> Firestore 'users' 컬렉션 매핑
    users_ref = db.collection("users")
    # alarm_time이 문자열 "18:00"으로 저장되어 있다고 가정
    query = users_ref.where("alarm_time", "==", current_time_str).stream()

    cnt = 0
    for user_doc in query:
        user_data = user_doc.to_dict()
        user_id = user_doc.id # 문서 ID
        
        print(f"🔍 유저 스캔 중: {user_data.get('username', 'Unknown')}")

        # 3. 알림 주기(interval) 체크
        if not check_interval(user_data):
            print("  -> 오늘은 알림 주기가 아닙니다. 패스.")
            continue

        # 4. 키워드 파싱 (ERD: interest_field = "UX,개발")
        raw_interests = user_data.get('interest_field', '')
        keywords = [k.strip() for k in raw_interests.split(',')] if raw_interests else []
        
        if not keywords:
            print("  -> 관심 키워드가 없습니다. 패스.")
            continue

        # 5. 크롤러 실행 (학교 사이트 등)
        print(f"  -> 크롤링 시작 (키워드: {keywords})")
        # run_crawler는 [{title, summary, url}, ...] 형태를 반환한다고 가정
        results = run_crawler(keywords) 

        # 6. 결과 처리 및 DB 저장 (notification 테이블)
        if results:
            print(f"  -> {len(results)}건 발견! DB 저장 및 알림")
            
            # (1) 알림 내역 저장 (중복 방지 로직 포함 가능)
            for res in results:
                db.collection("notification").add({
                    "user_id": user_id,
                    "title": res['title'],
                    "summary": res['analysis'], # Gemini 요약
                    "created_at": firestore.SERVER_TIMESTAMP,
                    "is_sent": True
                })
            
            # (2) 유저의 last_sent_at 업데이트 (오늘 날짜로)
            users_ref.document(user_id).update({
                "last_sent_at": firestore.SERVER_TIMESTAMP
            })
            
            cnt += 1
            # (3) 여기서 카카오톡 API 등을 호출하여 실제 발송
            # send_kakao_msg(user_data['phone'], results)

    return f"Job Done. Processed {cnt} users.", 200

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))