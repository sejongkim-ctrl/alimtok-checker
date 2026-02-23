"""
Gemini AI 2차 분석 모듈
- 규칙 기반 결과가 "애매함"(50~79점)인 경우에만 호출
- Few-shot 프롬프트로 실제 반려/승인 사례 기반 판단
"""
import os
import google.generativeai as genai


SYSTEM_PROMPT = """당신은 카카오 알림톡 검수 담당자입니다.
아래 기준에 따라 알림톡 본문의 승인/반려 여부를 판단하세요.

## 카카오 알림톡 검수 기준
1. 알림톡은 "수신자의 액션을 기반한 정보성 메시지"만 허용
2. 수신자가 요청하지 않은 내용으로 광고성/공지성 메시지는 반려
3. 동일 내용 2회 이상 발송 시 영리 목적 광고로 판단
4. 수신자 액션(신청, 요청 등)이 본문에 고정값으로 기재되어야 함

## 반려되는 조건
- 정확한 수신대상/수신사유를 확인하기 어려운 경우
- 불특정 다수에게 발송될 수 있는 홍보/광고성 문구
- 혜택 제공을 조건으로 특정 행위 유도
- 앱 설치 유도

## 실제 반려 사례
1. "환자분들에게~바랍니다" → 홍보성 느낌으로 반려
2. "수멤버스 연간 일정" 상세 기재 → 마케팅성 메시지로 반려 (욕심 과다)
3. "가입만 하면 무료!" → 광고성으로 반려
4. "미리 구비해주세요" → 구매 유도로 반려
5. "깜짝 프로모션 혜택" → 명백한 홍보로 반려

## 실제 승인 사례
1. "수멤버스에 가입해주셔서 감사합니다. 콜드퀵이 정식 출시되어 안내드립니다" → 승인
2. "원장님께서 요청하신 세션이 시작됩니다" → 즉시 승인
3. "사전 신청해주신 세션이 금일 진행됩니다" → 승인
4. "가입 시 필수 절차인 공동이용계약 관련 안내드립니다" → 승인
5. 가격 인상 정보만 담은 알림톡 → 순수 정보 전달로 승인"""

USER_PROMPT_TEMPLATE = """아래 알림톡 본문을 검수해주세요.

## 알림톡 본문
{body}

{cta_section}

## 요청사항
1. **판정**: 승인 / 반려 / 조건부 승인 중 택 1
2. **판정 근거**: 카카오 검수 기준에 비추어 2~3문장
3. **수정 제안**: 반려 또는 조건부 승인이면 구체적 수정안 제시 (수정된 전체 본문 포함)
4. **의견제출 전략**: 반려될 경우 의견제출에 쓸 수 있는 근거 문구 1줄

위 4개 항목만 간결하게 답변하세요."""


def analyze_with_ai(body: str, cta: str = "", api_key: str = None) -> dict:
    """Gemini AI로 알림톡 본문 분석"""
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {
            "success": False,
            "error": "GEMINI_API_KEY가 설정되지 않았습니다.",
        }

    genai.configure(api_key=key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    cta_section = f"## CTA 버튼명\n{cta}" if cta else ""
    user_prompt = USER_PROMPT_TEMPLATE.format(body=body, cta_section=cta_section)

    try:
        response = model.generate_content(
            [
                {"role": "user", "parts": [SYSTEM_PROMPT]},
                {"role": "model", "parts": ["네, 카카오 알림톡 검수 기준을 이해했습니다. 본문을 보내주세요."]},
                {"role": "user", "parts": [user_prompt]},
            ],
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=1024,
                temperature=0.3,
            ),
        )
        return {
            "success": True,
            "analysis": response.text,
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Gemini API 오류: {str(e)}",
        }
