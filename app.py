"""
알림톡 검수 예측기 — Streamlit 앱
B2D 팀 내부용: 카카오 알림톡 검수 결과를 사전 예측
"""
import os
import re
import base64
import streamlit as st
import streamlit.components.v1 as st_components
from checker import run_check, CheckResult, load_learned_rules
from ai_analyzer import analyze_with_ai, extract_text_from_image, rewrite_attractive
from rules import FORBIDDEN_WORDS, MAGIC_PHRASES, THRESHOLDS

st.set_page_config(
    page_title="알림톡 검수 예측기",
    page_icon="🔍",
    layout="centered",
)

# --- API 키 ---
api_key = os.environ.get("GEMINI_API_KEY", "")
if hasattr(st, "secrets") and "GEMINI_API_KEY" in st.secrets:
    api_key = st.secrets["GEMINI_API_KEY"]

# --- 이미지 붙여넣기 컴포넌트 ---
_paste_component_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "components", "paste_image")
paste_image_component = st_components.declare_component("paste_image", path=_paste_component_path)

# --- 헤더 ---
st.title("🔍 알림톡 검수 예측기")
learned = load_learned_rules()
last_learn = learned.get("last_updated") or "아직 없음"
total_cases = learned.get("total_cases", 0)
extra_rules = len(learned.get("learned_forbidden_words", {})) + len(learned.get("learned_magic_phrases", []))
st.caption(f"B2D 팀 내부용 · 자동 학습 {total_cases}건 반영 · 마지막 학습: {last_learn}")
st.divider()

# --- Session State 초기화 ---
if "body_input" not in st.session_state:
    st.session_state["body_input"] = ""
if "cta_input" not in st.session_state:
    st.session_state["cta_input"] = ""

# --- 이미지 붙여넣기 (OCR) ---
with st.expander("📷 이미지에서 텍스트 추출", expanded=False):
    pasted_data = paste_image_component(key="paste_area", default=None)

    if pasted_data and isinstance(pasted_data, str) and pasted_data.startswith("data:image"):
        # data URL → bytes 변환
        header, b64_str = pasted_data.split(",", 1)
        mime_type = header.split(":")[1].split(";")[0]
        image_bytes = base64.b64decode(b64_str)

        if st.button("🔍 텍스트 추출하기", use_container_width=True):
            if not api_key:
                st.error("GEMINI_API_KEY가 설정되지 않았습니다.")
            else:
                with st.spinner("Gemini Vision으로 텍스트 추출 중..."):
                    ocr_result = extract_text_from_image(image_bytes, mime_type, api_key)
                if ocr_result["success"]:
                    extracted = ocr_result["text"]
                    cta_match = re.search(r'\[CTA:\s*(.+?)\]', extracted)
                    if cta_match:
                        st.session_state["cta_input"] = cta_match.group(1).strip()
                        extracted = re.sub(r'\n?\[CTA:\s*.+?\]', '', extracted).strip()
                    st.session_state["body_input"] = extracted
                    st.rerun()
                else:
                    st.error(ocr_result["error"])

# --- 입력 영역 ---
body = st.text_area(
    "알림톡 본문",
    key="body_input",
    height=200,
    placeholder="검수에 넣을 알림톡 본문을 붙여넣으세요...\n\n📷 이미지로도 입력 가능 — 위 '이미지에서 텍스트 추출' 클릭",
)

cta = st.text_input(
    "CTA 버튼명 (선택)",
    key="cta_input",
    placeholder="예: 자세히 보기",
)

# --- 검수 예측 실행 ---
if st.button("검수 예측하기", type="primary", use_container_width=True):
    if not body.strip():
        st.warning("알림톡 본문을 입력해주세요.")
    else:
        result = run_check(body.strip(), cta.strip())
        st.session_state["result"] = result
        st.session_state["body"] = body.strip()
        st.session_state["cta"] = cta.strip()

# --- 결과 표시 ---
if "result" in st.session_state:
    result: CheckResult = st.session_state["result"]
    st.divider()

    # 점수 + 판정
    col1, col2 = st.columns([1, 2])
    with col1:
        score_color = {"green": "🟢", "orange": "🟡", "red": "🔴"}[result.color]
        st.metric(
            label="검수 점수",
            value=f"{result.score}/100",
        )
    with col2:
        if result.color == "green":
            st.success(f"{score_color} {result.verdict}")
        elif result.color == "orange":
            st.warning(f"{score_color} {result.verdict}")
        else:
            st.error(f"{score_color} {result.verdict}")

    # 진행 바
    st.progress(result.score / 100)

    # 검사 항목별 결과
    st.subheader("검사 결과")

    severity_icon = {
        "critical": "❌",
        "warning": "⚠️",
        "info": "ℹ️",
        "pass": "✅",
    }

    for item in result.items:
        icon = severity_icon.get(item.severity, "•")
        st.markdown(f"{icon} **{item.message}**")
        if item.suggestion:
            st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;💡 {item.suggestion}")

    # 수정 제안 요약
    suggestions = [item for item in result.items if item.suggestion]
    if suggestions:
        st.divider()
        st.subheader("수정 제안 요약")
        for i, item in enumerate(suggestions, 1):
            st.markdown(f"{i}. {item.suggestion}")

    # --- 매력적 리라이팅 ---
    st.divider()
    st.subheader("✨ 매력적으로 리라이팅")
    st.caption("검수 통과 범위 내에서 더 후킹한 메시지로 제안합니다.")

    if st.button("✨ 매력적으로 리라이팅", use_container_width=True):
        if not api_key:
            st.error("GEMINI_API_KEY가 설정되지 않았습니다.")
        else:
            issues = [item.message for item in result.items if item.severity in ("critical", "warning")]
            with st.spinner("Gemini AI가 리라이팅 중..."):
                rewrite_result = rewrite_attractive(
                    st.session_state["body"],
                    st.session_state["cta"],
                    issues,
                    api_key,
                )
            if rewrite_result["success"]:
                st.markdown("---")
                st.markdown(rewrite_result["rewrite"])
            else:
                st.error(rewrite_result["error"])

    # AI 정밀 분석 버튼
    if result.needs_ai or result.score < THRESHOLDS["pass"]:
        st.divider()
        st.subheader("AI 정밀 분석")
        st.caption("Gemini AI가 카카오 검수 기준으로 본문을 정밀 분석합니다.")

        if st.button("🤖 AI 정밀 분석 실행", use_container_width=True):
            if not api_key:
                st.error("GEMINI_API_KEY가 설정되지 않았습니다.")
            else:
                with st.spinner("Gemini AI 분석 중..."):
                    ai_result = analyze_with_ai(
                        st.session_state["body"],
                        st.session_state["cta"],
                        api_key,
                    )
                if ai_result["success"]:
                    st.markdown("---")
                    st.markdown(ai_result["analysis"])
                else:
                    st.error(ai_result["error"])

# --- 하단 참고 가이드라인 ---
st.divider()
with st.expander("📚 검수 가이드라인 참고"):
    st.markdown("### 금지 워딩")
    for word, info in FORBIDDEN_WORDS.items():
        replace_text = f' → "{info["replace"]}"' if info["replace"] else " → 삭제"
        st.markdown(f"- **{word}**: {info['reason']}{replace_text}")

    st.markdown("### 매직 문구 (승인률 높이는 표현)")
    for mp in MAGIC_PHRASES:
        st.markdown(f'- **"{mp["phrase"]}"**: {mp["desc"]}')

    st.markdown("### 반려 시 대응")
    st.markdown("""
1. **의견제출** (본문 수정 없이): "~한 상황에서 발송한다, 그래서 본문에 ~라고 기재했다"
2. **본문 수정 후 재검수**: 수신자 액션 고정값 추가 + 금지 워딩 제거
3. **친구톡(브랜드 메시지) fallback**: 알림톡 검수 불가 시
""")

    st.markdown("### 채널별 주의")
    st.markdown("동일 내용이라도 수멤버스/아큐렉스 채널별로 검수 결과가 다를 수 있음")

    # 학습된 규칙 표시
    if extra_rules > 0:
        st.markdown("---")
        st.markdown("### 🤖 자동 학습된 규칙")
        if learned.get("learned_forbidden_words"):
            st.markdown("**추가 금지 워딩:**")
            for word, reason in learned["learned_forbidden_words"].items():
                st.markdown(f"- **{word}**: {reason}")
        if learned.get("learned_magic_phrases"):
            st.markdown("**추가 매직 문구:**")
            for mp in learned["learned_magic_phrases"]:
                st.markdown(f'- **"{mp["phrase"]}"**: {mp.get("desc", "")}')
