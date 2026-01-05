import os
import logging
from flask import Flask, request, jsonify

# 각 크롤러 모듈 임포트
# (파일 이름이 정확해야 합니다. 업로드된 파일명 기준)
import sogang_uni
import ewha_uni
# import korea_uni  <-- 필요시 추가

app = Flask(__name__)

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CloudRunRouter")

@app.route("/", methods=["POST"])
def router():
    """
    들어온 JSON 요청의 'url'을 보고 어떤 크롤러를 돌릴지 결정합니다.
    """
    event = request.get_json() or {}
    url = event.get("url", "")
    
    logger.info(f"🚀 요청 수신: {url}")

    try:
        # 1. 서강대 (URL에 'sogang'이 있으면)
        if "sogang.ac.kr" in url:
            # sogang_uni.py의 lambda_handler를 호출 (event 그대로 전달)
            result = sogang_uni.lambda_handler(event, None)
            return jsonify(result)

        # 2. 이화여대 (URL에 'ewha.ac.kr'이 있으면)
        elif "ewha.ac.kr" in url:
            # ewha_uni.py는 crawl() 함수가 메인입니다.
            result = ewha_uni.crawl()
            return jsonify(result)

        # 3. 그 외 (또는 Linkareer 등)
        else:
            return jsonify({"error": "지원하지 않는 URL입니다.", "input_url": url}), 400

    except Exception as e:
        logger.error(f"❌ 처리 중 에러 발생: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    # Cloud Run은 PORT 환경변수로 포트를 지정해줍니다 (기본 8080)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))