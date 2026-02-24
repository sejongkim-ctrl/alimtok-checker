"""
알림톡 검수 자동 학습기 v2 — 스레드 추적 + 이미지 멀티모달 분석
- 매일 00:00 KST에 GitHub Actions에서 실행
- Slack m_13_b2d_공지 채널에서 검수 관련 스레드 전체 추적
- 스레드 내 이미지(알림톡 스크린샷)도 Gemini 멀티모달로 분석
- 반려→수정→재검수→승인 전체 흐름을 맥락으로 파악
"""
import os
import json
import re
import base64
import requests
from datetime import datetime, timedelta, timezone

# --- 설정 ---
SLACK_TOKEN = os.environ.get("SLACK_USER_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
CHANNEL_ID = "C06JA0YS64E"  # m_13_b2d_공지
LEARNED_FILE = os.path.join(os.path.dirname(__file__), "learned_rules.json")

SEARCH_KEYWORDS = ["검수", "반려", "승인", "알림톡", "친구톡", "검수요청", "검수 요청"]
MAX_IMAGES_PER_THREAD = 5
MAX_IMAGES_TOTAL = 15
MAX_THREADS = 10

GEMINI_PROMPT = """아래는 카카오 알림톡/친구톡 검수 관련 Slack 스레드들입니다.
각 스레드는 검수 요청 → 반려 → 수정 → 재검수 → 승인의 전체 흐름을 담고 있습니다.
텍스트 메시지와 함께 첨부된 이미지(알림톡 본문 스크린샷)도 분석해주세요.

## 분석 요청
1. 각 스레드의 최종 결과가 "승인"인지 "반려"인지 분류
2. 반려 사유 분석 — 어떤 워딩/표현이 문제였는지
3. 승인 성공 요인 — 어떤 수정/문구가 통과에 기여했는지
4. 이미지에 보이는 알림톡 본문 텍스트도 읽어서 분석에 포함
5. 새로 발견된 금지 워딩, 매직 문구, 정보성 키워드 추출

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
      "summary": "산청2호점 공동이용신고 알림톡",
      "result": "승인",
      "rejections_before_approval": 3,
      "rejection_reasons": ["공지성 메시지 분류", "수신자 액션 불명확"],
      "approval_factors": ["가입된 원장님 대상 문구 추가", "수신자 액션 명시"],
      "final_message_text": "최종 승인된 알림톡 본문 (이미지에서 읽은 경우 포함)",
      "key_phrases": ["가입된 원장님 대상"]
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

검수와 무관한 스레드는 cases에서 제외하세요.
기존에 이미 등록된 워딩/문구는 제외하고 새로 발견된 것만 추출하세요.
이미지에서 읽은 알림톡 본문 텍스트는 final_message_text에 포함해주세요.

## Slack 스레드들
{threads}"""


def slack_api(endpoint: str, params: dict) -> dict:
    """Slack API 호출 헬퍼"""
    url = f"https://slack.com/api/{endpoint}"
    headers = {"Authorization": f"Bearer {SLACK_TOKEN}"}
    resp = requests.get(url, headers=headers, params=params)
    data = resp.json()
    if not data.get("ok"):
        print(f"  Slack API 실패 ({endpoint}): {data.get('error', 'unknown')}")
    return data


def fetch_channel_history(hours_back: int = 25) -> list[dict]:
    """채널 최근 메시지 가져오기"""
    oldest = str(int((datetime.now(timezone.utc) - timedelta(hours=hours_back)).timestamp()))
    data = slack_api("conversations.history", {
        "channel": CHANNEL_ID,
        "oldest": oldest,
        "limit": 100,
    })
    return data.get("messages", [])


def fetch_thread_replies(thread_ts: str) -> list[dict]:
    """스레드 전체 답글 가져오기"""
    data = slack_api("conversations.replies", {
        "channel": CHANNEL_ID,
        "ts": thread_ts,
        "limit": 100,
    })
    return data.get("messages", [])


def download_slack_image(url_private: str) -> str | None:
    """Slack 이미지 다운로드 → base64 인코딩"""
    try:
        resp = requests.get(
            url_private,
            headers={"Authorization": f"Bearer {SLACK_TOKEN}"},
            timeout=15,
        )
        if resp.status_code == 200 and len(resp.content) > 0:
            return base64.b64encode(resp.content).decode("utf-8")
    except requests.RequestException as e:
        print(f"  이미지 다운로드 실패: {e}")
    return None


def is_relevant_thread(messages: list[dict]) -> bool:
    """스레드가 검수 관련인지 판단 — 스레드 내 모든 메시지에서 키워드 검색"""
    all_text = " ".join(msg.get("text", "") for msg in messages)
    return any(kw in all_text for kw in SEARCH_KEYWORDS)


def collect_threads() -> list[dict]:
    """채널에서 검수 관련 스레드 수집 (텍스트 + 이미지)"""
    raw_messages = fetch_channel_history()
    print(f"  채널 메시지 총 {len(raw_messages)}건 수신")

    # 스레드가 있는 메시지만 추출
    thread_roots = [
        msg for msg in raw_messages
        if msg.get("reply_count", 0) > 0 or msg.get("thread_ts") == msg.get("ts")
    ]
    # 스레드 없는 단독 메시지 중 키워드 매칭되는 것도 포함
    standalone = [
        msg for msg in raw_messages
        if msg.get("reply_count", 0) == 0
        and msg.get("thread_ts") is None
        and any(kw in msg.get("text", "") for kw in SEARCH_KEYWORDS)
    ]

    print(f"  스레드: {len(thread_roots)}건, 단독 메시지: {len(standalone)}건")

    threads = []
    total_images = 0

    # 스레드 처리
    for root in thread_roots[:MAX_THREADS]:
        thread_ts = root.get("ts")
        replies = fetch_thread_replies(thread_ts)

        if not is_relevant_thread(replies):
            continue

        thread_data = {"messages": [], "images": []}

        for reply in replies:
            msg_ts = float(reply.get("ts", 0))
            thread_data["messages"].append({
                "text": reply.get("text", ""),
                "user": reply.get("user", ""),
                "date": datetime.fromtimestamp(msg_ts, timezone.utc).strftime("%Y-%m-%d %H:%M"),
            })

            # 이미지 파일 수집
            for f in reply.get("files", []):
                if total_images >= MAX_IMAGES_TOTAL:
                    break
                if len(thread_data["images"]) >= MAX_IMAGES_PER_THREAD:
                    break
                mimetype = f.get("mimetype", "")
                if mimetype.startswith("image/"):
                    url = f.get("url_private", "")
                    if url:
                        print(f"  📷 이미지 다운로드: {f.get('name', 'unknown')} ({mimetype})")
                        img_b64 = download_slack_image(url)
                        if img_b64:
                            thread_data["images"].append({
                                "data": img_b64,
                                "mimetype": mimetype,
                                "name": f.get("name", "image"),
                            })
                            total_images += 1

        threads.append(thread_data)
        msg_count = len(thread_data["messages"])
        img_count = len(thread_data["images"])
        preview = thread_data["messages"][0]["text"][:60] if thread_data["messages"] else ""
        print(f"  ✓ 스레드 수집: {msg_count}건 메시지, {img_count}건 이미지 | {preview}...")

    # 단독 메시지 처리 (스레드 형태로 포장)
    for msg in standalone:
        msg_ts = float(msg.get("ts", 0))
        thread_data = {
            "messages": [{
                "text": msg.get("text", ""),
                "user": msg.get("user", ""),
                "date": datetime.fromtimestamp(msg_ts, timezone.utc).strftime("%Y-%m-%d %H:%M"),
            }],
            "images": [],
        }
        # 단독 메시지의 파일도 수집
        for f in msg.get("files", []):
            if total_images >= MAX_IMAGES_TOTAL:
                break
            mimetype = f.get("mimetype", "")
            if mimetype.startswith("image/"):
                url = f.get("url_private", "")
                if url:
                    img_b64 = download_slack_image(url)
                    if img_b64:
                        thread_data["images"].append({
                            "data": img_b64,
                            "mimetype": mimetype,
                            "name": f.get("name", "image"),
                        })
                        total_images += 1
        threads.append(thread_data)

    print(f"\n검수 관련 스레드: {len(threads)}건 (이미지 총 {total_images}건)")
    return threads


def analyze_with_gemini(threads: list[dict]) -> dict:
    """Gemini 멀티모달 분석 — 텍스트 + 이미지 동시 전송"""
    empty = {"cases": [], "new_forbidden_words": {}, "new_magic_phrases": [], "new_informational_keywords": []}
    if not threads:
        return empty

    # Gemini 멀티모달 parts 구성
    parts = []

    # 스레드별 텍스트 + 이미지 배치
    threads_text_parts = []
    for i, thread in enumerate(threads, 1):
        thread_text = f"\n### 스레드 {i}\n"
        for msg in thread["messages"]:
            thread_text += f"[{msg['date']}] {msg['user']}: {msg['text']}\n"
        if thread["images"]:
            thread_text += f"\n(아래 {len(thread['images'])}개 이미지는 이 스레드에 첨부된 알림톡 스크린샷입니다)\n"
        threads_text_parts.append(thread_text)

    # 프롬프트 조합
    all_threads_text = "\n".join(threads_text_parts)
    prompt_text = GEMINI_PROMPT.replace("{threads}", all_threads_text)
    parts.append({"text": prompt_text})

    # 이미지 parts 추가 (스레드별 라벨 포함)
    for i, thread in enumerate(threads, 1):
        for j, img in enumerate(thread["images"], 1):
            parts.append({"text": f"[스레드 {i} - 이미지 {j}: {img['name']}]"})
            parts.append({
                "inline_data": {
                    "mime_type": img["mimetype"],
                    "data": img["data"],
                }
            })

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"maxOutputTokens": 4096, "temperature": 0.2},
    }

    print(f"  Gemini 요청: {len(parts)}개 parts (텍스트+이미지)")
    resp = requests.post(url, json=payload, timeout=60)
    data = resp.json()

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(1))
        return json.loads(text)
    except (KeyError, json.JSONDecodeError, IndexError) as e:
        print(f"Gemini 응답 파싱 실패: {e}")
        # 디버깅용 응답 일부 출력
        if "candidates" in data:
            try:
                raw = data["candidates"][0]["content"]["parts"][0]["text"]
                print(f"응답 텍스트 (앞 500자): {raw[:500]}")
            except (KeyError, IndexError):
                pass
        else:
            print(f"원본 응답: {json.dumps(data, ensure_ascii=False)[:500]}")
        return empty


def update_learned_rules(analysis: dict) -> bool:
    """learned_rules.json 업데이트"""
    with open(LEARNED_FILE, "r", encoding="utf-8") as f:
        learned = json.load(f)

    changed = False
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 새 케이스 추가 (중복 방지: 같은 날짜+요약이면 스킵)
    existing_keys = {(c.get("date"), c.get("summary")) for c in learned["cases"]}
    for case in analysis.get("cases", []):
        key = (case.get("date", today), case.get("summary", ""))
        if key not in existing_keys:
            learned["cases"].append(case)
            changed = True
            result = case.get("result", "?")
            print(f"  + 새 케이스: [{result}] {case.get('summary', '')}")

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
    print(f"알림톡 검수 자동 학습 v2 ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})")
    print("=" * 50)

    if not SLACK_TOKEN:
        print("ERROR: SLACK_USER_TOKEN 환경변수가 설정되지 않았습니다.")
        return
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
        return

    # 1. Slack 스레드 수집 (텍스트 + 이미지)
    print("\n[1/3] Slack 스레드 수집 중...")
    threads = collect_threads()

    if not threads:
        print("최근 24시간 내 검수 관련 스레드가 없습니다.")
        return

    # 2. Gemini 멀티모달 분석
    print("\n[2/3] Gemini AI 멀티모달 분석 중...")
    analysis = analyze_with_gemini(threads)
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
