"""
Gemini AI 분석 모듈
- 2차 정밀 분석: 규칙 기반 결과가 "애매함"(50~79점)인 경우 호출
- 이미지 OCR: 알림톡 스크린샷에서 텍스트 추출 (Gemini Vision)
- 리라이팅: 검수 통과 범위 내에서 더 매력적인 메시지 제안
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


# ── 이미지 OCR ──────────────────────────────────────

OCR_PROMPT = """이 이미지는 카카오 알림톡 메시지의 스크린샷입니다.
이미지에서 알림톡 본문 텍스트를 정확하게 추출해주세요.

규칙:
1. 알림톡 본문 텍스트만 추출 (UI 요소, 상태바, 시간 등은 제외)
2. **중요**: 화면 폭 때문에 줄바꿈된 단어는 반드시 이어붙여서 출력. 예: "처\n방해주셔서" → "처방해주셔서"
3. 문단 구분(의미상 빈 줄)만 줄바꿈으로 유지. 같은 문장 내 줄바꿈은 제거
4. 이모지/특수문자도 그대로 유지
5. CTA 버튼명이 보이면 마지막 줄에 [CTA: 버튼명] 형태로 별도 표기

추출된 텍스트만 출력하세요. 설명이나 부연은 불필요합니다."""


def extract_text_from_image(image_bytes: bytes, mime_type: str, api_key: str = None) -> dict:
    """Gemini Vision으로 알림톡 이미지에서 텍스트 추출"""
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"success": False, "error": "GEMINI_API_KEY가 설정되지 않았습니다."}

    genai.configure(api_key=key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    try:
        response = model.generate_content(
            [OCR_PROMPT, {"mime_type": mime_type, "data": image_bytes}],
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=1024,
                temperature=0.1,
            ),
        )
        return {"success": True, "text": response.text.strip()}
    except Exception as e:
        return {"success": False, "error": f"이미지 텍스트 추출 오류: {str(e)}"}


# ── 매력적 리라이팅 ──────────────────────────────────

def rewrite_attractive(body: str, cta: str = "", issues: list = None, api_key: str = None) -> dict:
    """검수 통과 범위 내에서 더 매력적인 메시지로 리라이팅"""
    from rules import FORBIDDEN_WORDS, MAGIC_PHRASES

    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"success": False, "error": "GEMINI_API_KEY가 설정되지 않았습니다."}

    genai.configure(api_key=key)
    model = genai.GenerativeModel("gemini-2.0-flash")

    forbidden_list = ", ".join(FORBIDDEN_WORDS.keys())
    magic_list = ", ".join(mp["phrase"] for mp in MAGIC_PHRASES)
    issues_text = "\n".join(f"- {i}" for i in issues) if issues else "특별한 문제 없음"

    prompt = f"""당신은 카카오 알림톡 메시지를 검수 통과시키면서 최대한 매력적으로 쓰는 전문 카피라이터입니다.

## 절대 규칙 (위반 시 반려)
- 금지 워딩 절대 사용 금지: {forbidden_list}
- 수신자 액션 고정값 필수 포함: {magic_list} 중 최소 1개
- 광고/홍보성 느낌 제거 (불특정 다수 대상 X)
- 구매 유도/긴급성 조장 표현 금지

## 매력적 리라이팅 전략
1. 오프닝에서 "왜 이 메시지를 받았는지"와 "핵심 가치"를 한 줄로 압축
2. 추상적 표현 → 구체적 수치/일정/사실
3. 짧은 문장, 핵심만, 자연스러운 정보 전달 톤
4. "확인해보세요", "안내드립니다" 등 부드러운 행동 유도
5. 읽는 사람이 "이건 나한테 온 거구나" 느낌이 들게

## 원본 메시지
{body}

{f"## CTA 버튼명: {cta}" if cta else ""}

## 현재 검수 문제점
{issues_text}

## 출력 형식 (이 형식 그대로 따라주세요)

### 리라이팅 본문
(수정된 전체 알림톡 본문 — 복사해서 바로 쓸 수 있게)

### 변경 포인트
1. (무엇을 → 무엇으로, 왜)
2. (무엇을 → 무엇으로, 왜)
3. (무엇을 → 무엇으로, 왜)

### 예상 검수 결과
(승인 예상 + 1줄 근거)"""

    try:
        response = model.generate_content(
            [prompt],
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=1500,
                temperature=0.7,
            ),
        )
        return {"success": True, "rewrite": response.text}
    except Exception as e:
        return {"success": False, "error": f"리라이팅 오류: {str(e)}"}
