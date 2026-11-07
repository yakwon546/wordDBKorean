#!/usr/bin/env python3
"""성인문해 교과서(NLC) OCR 텍스트에서 OCR 잡음을 지우고 쪽 단위 정리본을 만든다.

사용 예:
    python3 nlc_clean.py NLC_corpus -o NLC_clean
    python3 nlc_clean.py NLC_corpus -o NLC_clean --files "소망의나무 1"   # 한 권만 시험

처리 내용 (CLAUDE.md, handoff.md 설계안 및 2026-11-07 사용자 결정 반영):
  1. 쪽(`===== p.N =====`) 단위로 나눈다.
  2. 표지·판권, 교재 사용 안내, 차례 쪽은 쪽 전체를 제외한다.
  3. 확실한 잡음 줄을 지운다: URL, 인쇄 시각(OCR 변형 포함), 쪽 번호(n/N), 숫자만 있는 줄,
     한글이 없는 줄(영어 문장 포함), 책 제목 머리말, 교재 구성 표시.
  4. 애매한 줄(한 글자 줄, 낱글자 나열, 영문 비율이 높은 줄)은 집계에서 빼고
     uncertain_lines.csv에 남긴다.
  5. 남은 줄을 이어 붙인다. 인쇄 줄바꿈으로 끊긴 단어(`봅\\n시다`)는 kiwi 분석 점수를 비교해
     붙인 쪽이 JOIN_MARGIN 이상 높을 때만 공백 없이 붙이고, 나머지는 공백으로 잇는다.

원본 파일은 읽기만 한다. 지운 줄과 판단 근거는 모두 CSV로 남긴다.
"""

import argparse
import csv
import difflib
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from pathlib import Path

try:
    import kiwipiepy
    from kiwipiepy import Kiwi
except ImportError:
    sys.exit("kiwipiepy가 설치되어 있지 않습니다. 먼저 `python3 -m pip install kiwipiepy==0.24.0`을 실행하세요.")

EXPECTED_KIWI_VERSION = "0.24.0"

# 분석 대상 15권 (워크북 제외). 파일명(확장자 제외, NFC) → 묶음
TARGET_FILES = {
    "소망의나무 1": "초등1단계", "소망의나무 2": "초등1단계",
    "소망의나무 3": "초등1단계", "소망의나무 4": "초등1단계",
    "배움의 나무 5": "초등2단계", "배움의 나무 6": "초등2단계",
    "배움의 나무 7": "초등2단계", "배움의 나무 8": "초등2단계",
    "지혜의 나무 9": "초등3단계", "지혜의 나무 10": "초등3단계",
    "지혜의 나무 11": "초등3단계", "지혜의 나무 12": "초등3단계",
    "국어 1 교과서": "중학", "국어 2 교과서": "중학", "국어 3 교과서": "중학",
}

PAGE_RE = re.compile(r"^===== p\.(\d+) =====\s*$")
HANGUL_SYL = re.compile(r"[가-힣]")
LATIN = re.compile(r"[A-Za-z]")

# 줄 단위 잡음
URL_RE = re.compile(r"https?://|(?:le|1e|ie)\.or\.kr|or\.kr/ebook", re.IGNORECASE)
PRINT_TIME_RE = re.compile(r"^\d{1,2}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*오[전후]\s*\d{1,2}:\d{2}$")
PRINT_TIME_BROKEN_RE = re.compile(r"^[\d\s\.:,]*[0오O]?\s*[전후][\s\d\.:,]*$")  # 예: '76 10 77 0후 17.74'
PAGE_COUNTER_RE = re.compile(r"^\d+\s*/\s*\d+$")  # 예: '3/130'
DIGITS_ONLY_RE = re.compile(r"^[\d\s\.,\-~·:;/\\()]+$")

# 교재 구성 표시 (사용자 결정: 제외)
SECTION_MARKERS = {"생각 열기", "생각열기", "쉼터", "학습 활동", "학습활동", "단원을 정리하며",
                   "보기", "중략", "출처", "지은이"}
SOURCE_LINE_RE = re.compile(r"^[\W\d_]*(출처|지은이)\s*[:：]")  # '출처: …', '지은이: …' 줄 전체

# 쪽 단위 제외
COVER_KEYS = ("성인문해교과서", "연구진", "집필진", "초판 발행", "저작권자", "편찬인")
COVER_PRICE_RE = re.compile(r"^값\s*[\d,]+\s*원$")
GUIDE_KEYS = ("교과서 공부 이렇게 해요", "차시 학습 주제", "단원 학습 목표")
TOC_KEYS = {"차례", "목차"}
TOC_ITEM_RE = re.compile(r"^\d+\s*[\.\)]\s*\S|^\d+$")

JOIN_MARGIN = 2.0      # 붙인 쪽 점수가 이만큼 이상 높을 때만 공백 없이 붙인다
JOIN_MARGIN_COMPLETE = 10.0  # 앞 어절이 조사·어미로 끝나 완결된 어절로 보일 때 요구하는 점수 차
                             # (두 단 편집 쪽에서 다른 단의 줄이 붙는 것을 막는다. 예: '손가락과'+'거나')
TITLE_SIMILARITY = 0.8  # 책 제목 머리말 판정 기준 (공백 제거 후 difflib 비율)
LATIN_RATIO = 0.5       # 영문 글자 비율이 이보다 높으면 OCR 의심


def nfc(s):
    return unicodedata.normalize("NFC", s)


def strip_marks(s):
    """앞뒤의 번호·기호를 뗀 핵심 문자열."""
    return re.sub(r"^[\W\d_]+|[\W\d_]+$", "", s).strip()


def classify_line(line, title_variants):
    """줄 하나를 판정한다. ('keep'|'remove'|'uncertain', 이유)"""
    if URL_RE.search(line):
        return "remove", "URL"
    if PRINT_TIME_RE.match(line):
        return "remove", "인쇄 시각"
    if PRINT_TIME_BROKEN_RE.match(line) and re.search(r"\d", line):
        return "remove", "인쇄 시각(OCR 변형)"
    if PAGE_COUNTER_RE.match(line):
        return "remove", "쪽 번호(n/N)"
    if DIGITS_ONLY_RE.match(line):
        return "remove", "숫자만"
    if not HANGUL_SYL.search(line):
        return "remove", "한글 없는 줄"
    ns = re.sub(r"\s+", "", line)
    if max(difflib.SequenceMatcher(None, ns, t).ratio() for t in title_variants) >= TITLE_SIMILARITY:
        return "remove", "책 제목 머리말"
    core = strip_marks(line)
    if core in SECTION_MARKERS or SOURCE_LINE_RE.match(line):
        return "remove", "교재 구성 표시"

    syllables = HANGUL_SYL.findall(line)
    if len(syllables) == 1:
        return "uncertain", "한 글자 줄"
    if len(set(syllables)) == 1:
        return "uncertain", "같은 글자 반복"  # 예: '근근근근' (ㄹ 쓰기 칸의 OCR 오인식)
    eojeols = [HANGUL_SYL.findall(w) for w in line.split()]
    eojeols = [e for e in eojeols if e]
    if len(eojeols) >= 2 and all(len(e) == 1 for e in eojeols):
        return "uncertain", "낱글자 나열"
    visible = re.sub(r"\s+", "", line)
    if visible and len(LATIN.findall(visible)) / len(visible) > LATIN_RATIO:
        return "uncertain", "영문 비율 높음(OCR 의심)"
    return "keep", ""


def classify_page(lines, prev_status):
    """쪽 전체 제외 여부. (status, reason) — status는 'include' 또는 'exclude'."""
    for ln in lines:
        if any(k in ln for k in COVER_KEYS) or COVER_PRICE_RE.match(ln):
            return "exclude", "표지·판권"
    for ln in lines:
        if any(k in ln for k in GUIDE_KEYS):
            return "exclude", "교재 사용 안내"
    if any(strip_marks(ln) in TOC_KEYS for ln in lines):
        return "exclude", "차례"
    if prev_status == "exclude:차례":
        body = [ln for ln in lines if not URL_RE.search(ln) and not PAGE_COUNTER_RE.match(ln)
                and not PRINT_TIME_RE.match(ln)]
        if body and sum(bool(TOC_ITEM_RE.match(ln)) for ln in body) / len(body) >= 0.5:
            return "exclude", "차례"
    return "include", ""


def join_lines(kiwi, kept, join_log, file_name, page_no):
    """남은 줄을 잇는다. 끊긴 단어는 kiwi 점수 비교로 붙인다."""
    if not kept:
        return ""
    text = kept[0][1]
    for line_no, line in kept[1:]:
        left = text.split()[-1] if text.split() else ""
        right = line.split()[0]
        if (left and HANGUL_SYL.fullmatch(left[-1]) and HANGUL_SYL.match(right[0])):
            joined = kiwi.analyze(left + right, top_n=1)[0][1]
            spaced = kiwi.analyze(left + " " + right, top_n=1)[0][1]
            diff = joined - spaced
            last_tag = kiwi.tokenize(left)[-1].tag
            margin = JOIN_MARGIN_COMPLETE if last_tag[0] in "JE" else JOIN_MARGIN
            if diff >= margin:
                join_log.append([file_name, page_no, line_no, left, right, left + right, round(diff, 2)])
                text = text + line
                continue
        text = text + " " + line
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="NLC_corpus 폴더")
    parser.add_argument("-o", "--output", default="NLC_clean", help="정리본 폴더 (기본: NLC_clean)")
    parser.add_argument("--files", default="", help="처리할 책 이름, 쉼표 구분 (기본: 분석 대상 15권 전체)")
    args = parser.parse_args()

    if kiwipiepy.__version__ != EXPECTED_KIWI_VERSION:
        print(f"[경고] kiwipiepy {kiwipiepy.__version__} 사용 중. NIKL 결과와 비교하려면 "
              f"{EXPECTED_KIWI_VERSION}을 쓰세요.", file=sys.stderr)

    in_dir = Path(args.input)
    wanted = [nfc(s.strip()) for s in args.files.split(",") if s.strip()] or list(TARGET_FILES)
    unknown = [w for w in wanted if w not in TARGET_FILES]
    if unknown:
        sys.exit(f"분석 대상 15권이 아닌 이름입니다: {unknown}")

    available = {nfc(p.stem): p for p in in_dir.glob("*.txt")}
    missing = [w for w in wanted if w not in available]
    if missing:
        sys.exit(f"입력 폴더에서 파일을 찾지 못했습니다: {missing}")

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    kiwi = Kiwi()

    removed_rows, uncertain_rows, join_log, page_rows = [], [], [], []
    file_stats = []
    t0 = time.time()

    for i, name in enumerate(wanted, 1):
        src = available[name]
        # 머리말 비교용 제목: '국어 1 교과서'는 '국어1교과서'와 '국어1' 둘 다 비교
        title_variants = {re.sub(r"\s+", "", name), re.sub(r"\s+", "", name.replace(" 교과서", ""))}
        raw_lines = src.read_text(encoding="utf-8").splitlines()

        # 쪽 나누기: [(쪽 번호, [(파일 줄 번호, 줄)])]
        pages, cur = [], None
        for no, raw in enumerate(raw_lines, 1):
            m = PAGE_RE.match(raw.strip())
            if m:
                cur = (int(m.group(1)), [])
                pages.append(cur)
            elif cur is not None and raw.strip():
                cur[1].append((no, nfc(raw.strip())))

        out_parts = []
        prev_status = ""
        counts = Counter()
        for page_no, lines in pages:
            texts = [t for _, t in lines]
            status, reason = classify_page(texts, prev_status)
            prev_status = f"{status}:{reason}"
            n_removed = n_uncertain = 0
            kept = []
            if status == "exclude":
                for no, t in lines:
                    removed_rows.append([name, page_no, no, t, f"쪽 제외: {reason}"])
                n_removed = len(lines)
            else:
                for no, t in lines:
                    verdict, why = classify_line(t, title_variants)
                    if verdict == "keep":
                        kept.append((no, t))
                    elif verdict == "remove":
                        removed_rows.append([name, page_no, no, t, why])
                        n_removed += 1
                    else:
                        uncertain_rows.append([name, page_no, no, t, why])
                        n_uncertain += 1
                if not kept:
                    status, reason = "empty", "남은 줄 없음"
            counts[status] += 1
            page_text = join_lines(kiwi, kept, join_log, name, page_no)
            if page_text:
                out_parts.append(f"===== p.{page_no} =====\n{page_text}\n")
            page_rows.append([name, TARGET_FILES[name], page_no, status, reason, len(lines),
                              len(kept), n_removed, n_uncertain])

        (out_dir / f"{name}.txt").write_text("".join(out_parts), encoding="utf-8")
        file_stats.append({"file": name, "group": TARGET_FILES[name], "pages": len(pages),
                           "pages_included": counts["include"], "pages_excluded": counts["exclude"],
                           "pages_empty": counts["empty"]})
        print(f"[{i}/{len(wanted)}] {name}: 쪽 {len(pages)} (포함 {counts['include']}, "
              f"제외 {counts['exclude']}, 빈 쪽 {counts['empty']})  ({time.time() - t0:.1f}s)", flush=True)

    def write_csv(fname, header, rows):
        with open(out_dir / fname, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    write_csv("removed_lines.csv", ["file", "page", "line_no", "text", "reason"], removed_rows)
    write_csv("uncertain_lines.csv", ["file", "page", "line_no", "text", "reason"], uncertain_rows)
    write_csv("join_log.csv", ["file", "page", "line_no", "left", "right", "joined", "score_diff"], join_log)
    write_csv("page_log.csv", ["file", "group", "page", "status", "reason", "n_lines", "n_kept",
                               "n_removed", "n_uncertain"], page_rows)

    summary = {
        "input": str(in_dir.resolve()),
        "files": file_stats,
        "removed_by_reason": dict(Counter(r[4] for r in removed_rows).most_common()),
        "uncertain_by_reason": dict(Counter(r[4] for r in uncertain_rows).most_common()),
        "n_joins": len(join_log),
        "settings": {"join_margin": JOIN_MARGIN, "join_margin_complete": JOIN_MARGIN_COMPLETE, "title_similarity": TITLE_SIMILARITY,
                     "latin_ratio": LATIN_RATIO, "section_markers": sorted(SECTION_MARKERS)},
        "analyzer": {"name": "kiwipiepy", "version": kiwipiepy.__version__},
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    with open(out_dir / "clean_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n완료: {len(wanted)}권, 지운 줄 {len(removed_rows):,}, 검수 대상 줄 {len(uncertain_rows):,}, "
          f"붙인 줄바꿈 {len(join_log):,}")
    print(f"결과 폴더: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
