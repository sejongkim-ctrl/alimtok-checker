"""
알림톡 검수 자동 학습기
- 매일 00:00 KST에 GitHub Actions에서 실행
- Slack m_13_b2d_공지 채널에서 검수 관련 메시지 검색 (최근 24시간)
- Gemini AI로 패턴 분석 → learned_rules.json 업데이트
"""
import os
import json
import re
import requests
from datetime import datetime, timedelta, timezone

# --- 설정 ---
SLACK_TOKEN = os.environ.get("SLACK_USER_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
CHANNEL_ID = "C06JA0YS64E"  # m_13_b2d_공지
LEARNED_FILE = os.path.join(os.path.dirname(__file__), "learned_rules.json")

SEARCH_KEYWORDS = ["검수", "반려", "승인", "알림톡", "친구톡"]

GEMINI_PROMPT = """아래는 카카오 알림톡 검수 관련 Slack 메시지들입니다.
각 메시지를 분석하여 JSON 형태로 결과를 정리해주세요.

## 분석 요청
1. 각 메시지가 검수 "승인"인지 "반려"인지 분류
2. 반려 사례에서 새로 발견된 금지 워딩 추출
3. 승인 사례에서 새로 발견된 매직 문구(수신자 액션 고정값) 추출
4. 정보성 메시지 키워드 추출 (배송, 가격 등 순수 정보 전달 유형)

## 기존에 이미 등록된 금지 워딩 (중복 제외)
혜택, 프로모션, 할인, 무료, 구입, 기다리신, 미리 구비, 깜짝

## 기존에 이미 등록된 매직 문구 (중복 제외)
요청하신, 가입하신, 가입해주셔서, 신청해주신, 참여해주셔서, 처방해주셔서, 계약된 원장님 대상, 가입되신

## 출력 형식 (반드시 이 JSON 형식만 출력)
```json
{
  "cases": [
    {
      "date": "2026-02-24",
      "summary": "설 배송 마감 D-7 알림톡",
      "result": "승인",
      "reason": "정보성 메시지로 판단",
      "key_phrases": ["배송 마감"]
    }
  ],
  "new_forbidden_words": {
    "새로운금지워딩": "반려 사유 설명"
  },
  "new_magic_phrases": [
    {"phrase": "새로운매직문구", "desc": "효과 설명"}
  ],
  "new_informational_keywords": ["새정보성키워드"]
}
```

메시지에서 검수 관련 내용이 없으면 cases를 빈 배열로 반환하세요.
기존에 이미 등록된 워딩/문구는 제외하고 새로 발견된 것만 추출하세요.

## Slack 메시지들
{messages}"""


def fetch_channel_history(hours_back: int = 25) -> list[dict]:
    """conversations.history로 채널 메시지 가져오기 (search:read 스코프 불필요)"""
    oldest = str(int((datetime.now(timezone.utc) - timedelta(hours=hours_back)).timestamp()))
    url = "https://slack.com/api/conversations.history"
    headers = {"Authorization": f"Bearer {SLACK_TOKEN}"}
    params = {
        "channel": CHANNEL_ID,
        "oldest": oldest,
        "limit": 100,
    }
    resp = requests.get(url, headers=headers, params=params)
    data = resp.json()

    if not data.get("ok"):
        print(f"Slack 채널 히스토리 실패: {data.get('error', 'unknown')}")
        return []

    return data.get("messages", [])


def collect_messages() -> list[dict]:
    """채널 히스토리에서 검수 관련 메시지만 필터링"""
    raw_messages = fetch_channel_history()
    print(f"  채널 메시지 총 {len(raw_messages)}건 수신")

    filtered = []
    for msg in raw_messages:
        text = msg.get("text", "")
        if any(kw in text for kw in SEARCH_KEYWORDS):
            msg_ts = float(msg.get("ts", 0))
            filtered.append({
                "ts": msg.get("ts"),
                "text": text,
                "user": msg.get("user", ""),
                "date": datetime.fromtimestamp(msg_ts, timezone.utc).strftime("%Y-%m-%d %H:%M"),
            })
            print(f"  [{filtered[-1]['date']}] {text[:80]}...")

    print(f"\n검수 관련 메시지: {len(filtered)}건")
    return filtered


def analyze_with_gemini(messages: list[dict]) -> dict:
    """Gemini AI로 메시지 분석"""
    if not messages:
        return {"cases": [], "new_forbidden_words": {}, "new_magic_phrases": [], "new_informational_keywords": []}

    messages_text = "\n\n".join([
        f"[{m['date']}] {m['user']}: {m['text']}"
        for m in messages
    ])

    prompt = GEMINI_PROMPT.format(messages=messages_text)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.2},
    }

    resp = requests.post(url, json=payload)
    data = resp.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        # JSON 블록 추출
        json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        # JSON 블록 없으면 전체 텍스트에서 파싱 시도
        return json.loads(text)
    except (KeyError, json.JSONDecodeError, IndexError) as e:
        print(f"Gemini 응답 파싱 실패: {e}")
        print(f"원본 응답: {data}")
        return {"cases": [], "new_forbidden_words": {}, "new_magic_phrases": [], "new_informational_keywords": []}


def update_learned_rules(analysis: dict) -> bool:
    """learned_rules.json 업데이트"""
    with open(LEARNED_FILE, "r", encoding="utf-8") as f:
        learned = json.load(f)

    changed = False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 새 케이스 추가 (중복 방지: 같은 날짜+요약이면 스킵)
    existing_keys = {(c["date"], c["summary"]) for c in learned["cases"]}
    for case in analysis.get("cases", []):
        key = (case.get("date", today), case.get("summary", ""))
        if key not in existing_keys:
            learned["cases"].append(case)
            changed = True

    # 새 금지 워딩 추가
    for word, reason in analysis.get("new_forbidden_words", {}).items():
        if word and word not in learned["learned_forbidden_words"]:
            learned["learned_forbidden_words"][word] = reason
            changed = True
            print(f"  + 새 금지 워딩: \"{word}\" ({reason})")

    # 새 매직 문구 추가
    existing_phrases = {mp["phrase"] for mp in learned["learned_magic_phrases"]}
    for mp in analysis.get("new_magic_phrases", []):
        if mp.get("phrase") and mp["phrase"] not in existing_phrases:
            learned["learned_magic_phrases"].append(mp)
            changed = True
            print(f"  + 새 매직 문구: \"{mp['phrase']}\"")

    # 새 정보성 키워드 추가
    for kw in analysis.get("new_informational_keywords", []):
        if kw and kw not in learned["learned_informational_keywords"]:
            learned["learned_informational_keywords"].append(kw)
            changed = True
            print(f"  + 새 정보성 키워드: \"{kw}\"")

    if changed:
        learned["last_updated"] = today
        learned["total_cases"] = len(learned["cases"])
        with open(LEARNED_FILE, "w", encoding="utf-8") as f:
            json.dump(learned, f, ensure_ascii=False, indent=2)
        print(f"\nlearned_rules.json 업데이트 완료 (총 {learned['total_cases']}건)")
    else:
        print("\n새로운 학습 내용 없음")

    return changed


def main():
    print("=" * 50)
    print(f"알림톡 검수 자동 학습 시작 ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})")
    print("=" * 50)

    if not SLACK_TOKEN:
        print("ERROR: SLACK_USER_TOKEN 환경변수가 설정되지 않았습니다.")
        return
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
        return

    # 1. Slack 메시지 수집
    print("\n[1/3] Slack 메시지 수집 중...")
    messages = collect_messages()

    if not messages:
        print("최근 24시간 내 검수 관련 메시지가 없습니다.")
        return

    # 2. Gemini 분석
    print("\n[2/3] Gemini AI 분석 중...")
    analysis = analyze_with_gemini(messages)
    print(f"  분석 결과: {len(analysis.get('cases', []))}건 케이스")

    # 3. 규칙 업데이트
    print("\n[3/3] 규칙 업데이트 중...")
    changed = update_learned_rules(analysis)

    if changed:
        print("\n✅ 학습 완료 — commit & push 필요")
    else:
        print("\n⏭️ 변경사항 없음 — 스킵")


if __name__ == "__main__":
    main()
