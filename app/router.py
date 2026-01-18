from __future__ import annotations

import base64
import json
import logging
import os
import sys
from typing import Any, Callable

from app.jobs import korea_university
from app.jobs import firecrawl_fallback
from dotenv import load_dotenv  # 1. python-dotenv 라이브러리 임포트

# 2. .env 파일의 내용을 환경 변수로 로드
# 이 문장이 실행된 직후부터 os.getenv("GEMINI_API_KEY")가 정상 작동합니다.
load_dotenv()
BASE_PREFIX_KU = "https://info.korea.ac.kr/info/board/"
BASE_PREFIX_EWHA = "https://www.ewha.ac.kr"
BASE_PREFIX_SOGANG = "https://www.sogang.ac.kr"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
LOG = logging.getLogger("router")

RouteHandler = Callable[[dict[str, Any], Any | None], dict[str, Any]]


def match_korea(url: str | None) -> bool:
    return bool(url and url.startswith(BASE_PREFIX_KU))


def match_linkareer(url: str | None) -> bool:
    return bool(url and url.startswith("https://linkareer.com/"))


def match_ewha(url: str | None) -> bool:
    return bool(url and BASE_PREFIX_EWHA in url)


def match_sogang(url: str | None) -> bool:
    return bool(url and BASE_PREFIX_SOGANG in url)


ROUTES: list[tuple[str, Callable[[str | None], bool], RouteHandler]] = [
    ("korea_university", match_korea, korea_university.run),
   # ("linkareer", match_linkareer, linkareer.run),
    # 완성이 덜 된 학교들은 아래처럼 주석 처리하세요.
    # ("ewha_university", match_ewha, ewha_university.run),
    # ("sogang_university", match_sogang, sogang_university.run),
]
print(f"현재 활성화된 경로: {[r[0] for r in ROUTES]}")
def resolve_handler(url):
    # 고려대만 남겨두고 나머지는 주석 처리
    if url is None:
        logging.error("❌ 에러: URL이 제공되지 않았습니다.")
        return None, None
        
    if "korea.ac.kr" in url:
        from app.jobs import korea_university
        return "korea_university", korea_university.run
    
    return None, None    
    # [임시 제외] 완성이 덜 된 학교들은 주석 처리해서 호출을 막습니다. TODO: 완성 후 주석 해제
    
    


def pick_route_url(payload: dict[str, Any]) -> str | None:
    for key in ("base_url", "url"):
        value = payload.get(key)
        if value:
            return value
    return None


def _extract_payload(event: dict[str, Any] | None) -> dict[str, Any]:
    if not event:
        return {}
    # When invoked through Lambda Function URLs / API Gateway the body is nested.
    if "body" in event:
        raw_body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            try:
                raw_body = base64.b64decode(raw_body)
            except (base64.binascii.Error, TypeError) as exc:
                raise ValueError(f"invalid base64 body: {exc}") from exc
        if isinstance(raw_body, bytes):
            raw_body = raw_body.decode("utf-8", errors="ignore")
        try:
            payload = json.loads(raw_body or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid json body: {exc}") from exc
        # Merge query params to allow overrides if needed.
        if event.get("queryStringParameters"):
            payload.setdefault("query", event["queryStringParameters"])
        LOG.info("HTTP payload keys: %s", list(payload.keys()))
        return payload
    return event


# app/router.py

def lambda_handler(event: dict[str, Any] | None, context: Any | None = None) -> dict[str, Any]:
    target_url = event.get("targetUrl")
    logging.info(f"🔎 수신된 targetUrl: {target_url}")
    
    if not target_url:
        return {"status": "FAIL", "message": "targetUrl is missing in event.json"}
    
    name, handler = resolve_handler(target_url)

    try:
        payload = _extract_payload(event)
    except ValueError as exc:
        return {"statusCode": 400, "body": {"status": "FAIL", "error": str(exc)}}

    # [수정] 요청하신 targetUrl 기반 라우팅
    
    if not handler:
        return {"statusCode": 404, "body": {"status": "FAIL", "message": "지원하지 않는 URL"}}

    try:
        # handler(korea_university.run 등)에 전체 payload 전달
        result = handler(payload, context)
        return result # 이제 run 함수가 요청하신 아웃풋 구조를 반환함
    except Exception as exc:
        return {"statusCode": 500, "body": {"status": "FAIL", "error": str(exc)}}
if __name__ == "__main__":
    import sys
    import json

    # 1. 파일 경로 가져오기 (기본값: event.json)
    event_file = sys.argv[1] if len(sys.argv) > 1 else "event.json"
    
    try:
        # 2. 파일 읽기
        with open(event_file, "r", encoding="utf-8") as f:
            payload = json.load(f)
            
        # 3. 핸들러 호출 및 결과 출력
        # 여기서 payload가 lambda_handler의 'event' 인자로 들어갑니다.
        response = lambda_handler(payload)
        print(json.dumps(response, ensure_ascii=False, indent=2))
        
    except FileNotFoundError:
        print(json.dumps({"status": "FAIL", "message": f"{event_file} 파일을 찾을 수 없습니다."}))
    except Exception as e:
        print(json.dumps({"status": "FAIL", "message": str(e)}))