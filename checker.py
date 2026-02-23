"""
규칙 기반 알림톡 검수 예측 엔진
- API 호출 없이 패턴 매칭으로 승인/반려 예측
- 100점 만점 채점 시스템
"""
import re
from dataclasses import dataclass, field
from rules import (
    FORBIDDEN_WORDS, MAGIC_PHRASES, RISKY_PATTERNS,
    FORBIDDEN_CTA, SCORING, THRESHOLDS,
)


@dataclass
class CheckItem:
    """개별 검사 결과"""
    category: str        # "forbidden" | "magic" | "personalization" | "cta" | "length" | "risky"
    severity: str        # "critical" | "warning" | "info" | "pass"
    message: str
    deduction: int = 0   # 감점
    suggestion: str = ""


@dataclass
class CheckResult:
    """전체 검수 예측 결과"""
    score: int = 100
    verdict: str = ""          # "승인 예상" | "애매함" | "반려 예상"
    items: list = field(default_factory=list)
    forbidden_found: list = field(default_factory=list)
    magic_found: list = field(default_factory=list)
    needs_ai: bool = False

    @property
    def color(self) -> str:
        if self.score >= THRESHOLDS["pass"]:
            return "green"
        elif self.score >= THRESHOLDS["ambiguous"]:
            return "orange"
        return "red"


def check_forbidden_words(text: str) -> list[CheckItem]:
    """금지 워딩 검사"""
    items = []
    for word, info in FORBIDDEN_WORDS.items():
        if word in text:
            suggestion = ""
            if info["replace"]:
                suggestion = f'"{word}" → "{info["replace"]}"로 변경'
            else:
                suggestion = f'"{word}" 삭제 또는 친구톡 전환 검토'
            items.append(CheckItem(
                category="forbidden",
                severity="critical",
                message=f'금지 워딩 "{word}" 발견 — {info["reason"]}',
                deduction=SCORING["forbidden_word"],
                suggestion=suggestion,
            ))
    return items


def check_magic_phrases(text: str) -> tuple[list[CheckItem], list[str]]:
    """매직 문구(수신자 액션 고정값) 검사"""
    found = []
    for mp in MAGIC_PHRASES:
        if mp["phrase"] in text:
            found.append(mp["phrase"])

    items = []
    if not found:
        items.append(CheckItem(
            category="magic",
            severity="critical",
            message="수신자 액션 고정값이 없음 — 반려 확률 높음",
            deduction=SCORING["no_magic_phrase"],
            suggestion='첫 문단에 "요청하신", "가입해주셔서", "신청해주신" 중 1개 추가',
        ))
    else:
        items.append(CheckItem(
            category="magic",
            severity="pass",
            message=f'수신자 액션 확인: {", ".join(found)}',
        ))
    return items, found


def check_personalization(text: str) -> list[CheckItem]:
    """개인화 변수 검사"""
    has_var = bool(re.search(r'#\{[^}]+\}', text))
    if not has_var:
        return [CheckItem(
            category="personalization",
            severity="warning",
            message="개인화 변수(#{성함} 등)가 없음",
            deduction=SCORING["no_personalization"],
            suggestion="#{성함} 변수를 추가하면 수신자 특정 증명에 도움",
        )]
    return [CheckItem(
        category="personalization",
        severity="pass",
        message="개인화 변수 포함됨",
    )]


def check_cta(cta_text: str) -> list[CheckItem]:
    """CTA 버튼명 검사"""
    if not cta_text:
        return []

    items = []
    for fc in FORBIDDEN_CTA:
        if fc["pattern"] in cta_text:
            items.append(CheckItem(
                category="cta",
                severity="critical",
                message=f'CTA 버튼 "{fc["pattern"]}" — 반려 위험',
                deduction=SCORING["bad_cta"],
                suggestion=f'"{fc["pattern"]}" → "{fc["replace"]}"로 변경',
            ))
    if not items and cta_text:
        items.append(CheckItem(
            category="cta",
            severity="pass",
            message=f'CTA 버튼 "{cta_text}" — 문제 없음',
        ))
    return items


def check_length(text: str) -> list[CheckItem]:
    """본문 길이 검사"""
    length = len(text)
    if length > 500:
        return [CheckItem(
            category="length",
            severity="warning",
            message=f"본문 {length}자 — 500자 초과 (담백하게 쓸수록 승인률 상승)",
            deduction=SCORING["too_long"],
            suggestion="핵심 내용만 남기고, 상세 내용은 CTA 버튼 링크로 유도",
        )]
    return [CheckItem(
        category="length",
        severity="pass",
        message=f"본문 {length}자",
    )]


def check_risky_patterns(text: str) -> list[CheckItem]:
    """위험 패턴 검사"""
    items = []
    for rp in RISKY_PATTERNS:
        if rp["pattern"] in text:
            items.append(CheckItem(
                category="risky",
                severity="warning",
                message=f'위험 표현 "{rp["pattern"]}" — {rp["reason"]}',
                deduction=SCORING["risky_pattern"],
                suggestion=f'"{rp["pattern"]}" 삭제 권장',
            ))
    return items


def run_check(body: str, cta: str = "") -> CheckResult:
    """전체 검수 예측 실행"""
    result = CheckResult()

    # 1. 금지 워딩
    forbidden_items = check_forbidden_words(body)
    result.items.extend(forbidden_items)
    result.forbidden_found = [item.message for item in forbidden_items]

    # 2. 매직 문구
    magic_items, magic_found = check_magic_phrases(body)
    result.items.extend(magic_items)
    result.magic_found = magic_found

    # 3. 개인화 변수
    result.items.extend(check_personalization(body))

    # 4. CTA 버튼
    result.items.extend(check_cta(cta))

    # 5. 본문 길이
    result.items.extend(check_length(body))

    # 6. 위험 패턴
    result.items.extend(check_risky_patterns(body))

    # 점수 계산
    for item in result.items:
        result.score += item.deduction
    result.score = max(0, min(100, result.score))

    # 판정
    if result.score >= THRESHOLDS["pass"]:
        result.verdict = "승인 예상"
    elif result.score >= THRESHOLDS["ambiguous"]:
        result.verdict = "애매함 — AI 분석 권장"
        result.needs_ai = True
    else:
        result.verdict = "반려 예상"

    return result
