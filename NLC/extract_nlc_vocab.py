#!/usr/bin/env python3
"""성인문해 교과서(NLC) 정리본에서 품사별 어휘를 추출하고 빈도를 집계한다.

nlc_clean.py가 만든 NLC_clean/ 폴더를 입력으로 쓴다.
분석 로직(kiwipiepy, 품사 목록, 태그 정규화, 원형 규칙, per_million, CSV 열 구성)은
NIKL 스크립트(20261104_NLCxcorpus/extract_written_corpus_vocab.py)와 같다 (비교 호환 조건).
NIKL 스크립트가 없는 폴더에서도 실행되도록 해당 함수와 설정을 아래에 그대로 옮겨 두었다.

집계 단위: 문서 = 교과서 1권, 문단 = 1쪽 (first_para_id 예: '지혜의 나무 9.p40')

사용 예:
    python3 extract_nlc_vocab.py NLC_clean -o NLC_output
"""

import argparse
import csv
import json
import re
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

try:
    import kiwipiepy
    from kiwipiepy import Kiwi
except ImportError:
    sys.exit("kiwipiepy가 설치되어 있지 않습니다. 먼저 `python3 -m pip install kiwipiepy==0.24.0`을 실행하세요.")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nlc_clean import EXPECTED_KIWI_VERSION, TARGET_FILES

# ---- NIKL 스크립트(extract_written_corpus_vocab.py)와 동일한 설정·함수 (수정 금지: 비교 호환 조건) ----
DEFAULT_POS = "NNG,NNP,VV,VA,MAG,MAJ"
PREDICATE_TAGS = {"VV", "VA", "VX", "VCP", "VCN"}  # 원형을 어간 + '다'로 만드는 품사
TEXT_PREVIEW_LEN = 200
PUNCT = "\"'“”‘’()[]{}<>《》〈〉「」『』.,!?…·:;~-—"

POS_LABELS = {
    "NNG": "일반명사", "NNP": "고유명사", "NNB": "의존명사", "NR": "수사", "NP": "대명사",
    "VV": "동사", "VA": "형용사", "VX": "보조용언", "VCP": "긍정지정사", "VCN": "부정지정사",
    "MM": "관형사", "MAG": "일반부사", "MAJ": "접속부사", "IC": "감탄사",
    "XR": "어근", "SL": "외국어", "SH": "한자", "SN": "숫자",
}


def base_tag(tag):
    """'VV-R', 'VA-I'처럼 규칙/불규칙 표시가 붙은 태그를 기본 태그로 합친다."""
    return tag.split("-")[0]


def make_lemma(form, tag):
    return form + "다" if tag in PREDICATE_TAGS else form


def eojeol_at(text, start, end):
    """형태소가 속한 어절(공백 단위)을 돌려준다. 앞뒤 문장부호는 뗀다."""
    left = text.rfind(" ", 0, start) + 1
    right = text.find(" ", end)
    if right == -1:
        right = len(text)
    return text[left:right].strip(PUNCT) or text[start:end]
# ---- NIKL 동일 부분 끝 ----

PAGE_RE = re.compile(r"^===== p\.(\d+) =====\s*$")
GROUP_ORDER = ["초등1단계", "초등2단계", "초등3단계", "중학"]
GROUP_LABELS = {"초등1단계": "성인문해 초등과정 1단계 (소망의 나무 1–4)",
                "초등2단계": "성인문해 초등과정 2단계 (배움의 나무 5–8)",
                "초등3단계": "성인문해 초등과정 3단계 (지혜의 나무 9–12)",
                "중학": "성인문해 중학과정 (국어 1–3 교과서)"}


def nfc(s):
    return unicodedata.normalize("NFC", s)


def iter_pages(path):
    """정리본에서 (쪽 번호, 텍스트)를 돌려준다."""
    page_no, buf = None, []
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = PAGE_RE.match(raw.strip())
        if m:
            if page_no is not None and " ".join(buf).strip():
                yield page_no, " ".join(buf).strip()
            page_no, buf = int(m.group(1)), []
        elif raw.strip():
            buf.append(raw.strip())
    if page_no is not None and " ".join(buf).strip():
        yield page_no, " ".join(buf).strip()


def write_word_freq(path, rows, total_tokens, para_freq, doc_freq, surfaces, first_seen, min_freq,
                    prefix_cols=(), prefix_vals=()):
    """NIKL과 같은 열 구성으로 word_freq를 쓴다. prefix_cols가 있으면 앞에 붙인다."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([*prefix_cols, "lemma", "pos", "pos_label", "freq", "para_freq", "doc_freq",
                    "per_million", "surface_top5", "first_para_id", "first_para_text"])
        for (lemma, tag), n in rows:
            if n < min_freq:
                continue
            key = (lemma, tag)
            top5 = " | ".join(f"{s}({c})" for s, c in surfaces[key].most_common(5))
            pid, text = first_seen[key]
            w.writerow([*prefix_vals, lemma, tag, POS_LABELS.get(tag, ""), n, para_freq[key],
                        doc_freq[key], round(n / total_tokens * 1_000_000, 2) if total_tokens else 0,
                        top5, pid, text])


def sort_rows(freq):
    return sorted(freq.items(), key=lambda kv: (-kv[1], kv[0][1], kv[0][0]))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="nlc_clean.py 결과 폴더 (NLC_clean)")
    parser.add_argument("-o", "--output", default="NLC_output", help="결과 폴더 (기본: NLC_output)")
    parser.add_argument("--pos", default=DEFAULT_POS, help=f"추출 품사, 쉼표 구분 (기본: {DEFAULT_POS})")
    parser.add_argument("--min-freq", type=int, default=1, help="word_freq CSV에 남길 최소 빈도 (기본: 1)")
    args = parser.parse_args()

    if kiwipiepy.__version__ != EXPECTED_KIWI_VERSION:
        print(f"[경고] kiwipiepy {kiwipiepy.__version__} 사용 중. NIKL 결과와 비교하려면 "
              f"{EXPECTED_KIWI_VERSION}을 쓰세요.", file=sys.stderr)

    target_pos = [t.strip() for t in args.pos.split(",") if t.strip()]
    target_set = set(target_pos)
    in_dir = Path(args.input)
    available = {nfc(p.stem): p for p in in_dir.glob("*.txt")}
    files = [(name, available[name]) for name in TARGET_FILES if name in available]
    if not files:
        sys.exit(f"정리본을 찾지 못했습니다: {in_dir} (먼저 nlc_clean.py를 실행하세요)")
    missing = [name for name in TARGET_FILES if name not in available]
    if missing:
        print(f"[알림] 정리본이 없는 책은 건너뜁니다: {missing}", file=sys.stderr)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    kiwi = Kiwi()

    freq, para_freq, doc_freq = Counter(), Counter(), Counter()
    surfaces = defaultdict(Counter)
    first_seen = {}
    pos_tokens = Counter()
    # 묶음별
    g_freq, g_para, g_doc = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    g_surfaces = defaultdict(lambda: defaultdict(Counter))
    g_first = defaultdict(dict)
    g_tokens = Counter()
    doc_info = []
    n_paras = 0

    t0 = time.time()
    for i, (name, path) in enumerate(files, 1):
        group = TARGET_FILES[name]
        pages = list(iter_pages(path))
        n_paras += len(pages)
        doc_keys = set()
        doc_tokens = 0
        texts = [text for _, text in pages]
        for (page_no, text), tokens in zip(pages, kiwi.tokenize(texts)):
            pid = f"{name}.p{page_no}"
            para_keys = set()
            for tok in tokens:
                tag = base_tag(tok.tag)
                if tag not in target_set:
                    continue
                key = (make_lemma(tok.form, tag), tag)
                surface = eojeol_at(text, tok.start, tok.start + tok.len)
                freq[key] += 1
                pos_tokens[tag] += 1
                surfaces[key][surface] += 1
                g_freq[group][key] += 1
                g_surfaces[group][key][surface] += 1
                g_tokens[group] += 1
                doc_tokens += 1
                if key not in first_seen:
                    first_seen[key] = (pid, text[:TEXT_PREVIEW_LEN])
                if key not in g_first[group]:
                    g_first[group][key] = (pid, text[:TEXT_PREVIEW_LEN])
                para_keys.add(key)
            para_freq.update(para_keys)
            g_para[group].update(para_keys)
            doc_keys |= para_keys
        doc_freq.update(doc_keys)
        g_doc[group].update(doc_keys)
        doc_info.append({"file": path.name, "doc_id": name, "group": group,
                         "group_label": GROUP_LABELS[group], "n_paragraphs": len(pages),
                         "n_tokens": doc_tokens})
        print(f"[{i}/{len(files)}] {name}  쪽 {len(pages)}, 토큰 {doc_tokens:,}  ({time.time() - t0:.1f}s)",
              flush=True)
    elapsed = time.time() - t0
    total_tokens = sum(pos_tokens.values())

    # 1) word_freq.csv — NIKL과 같은 열 구성
    write_word_freq(out_dir / "word_freq.csv", sort_rows(freq), total_tokens, para_freq, doc_freq,
                    surfaces, first_seen, args.min_freq)

    # 2) pos_summary.csv — NIKL과 같은 열 구성
    types_by_pos = Counter(tag for _, tag in freq)
    with open(out_dir / "pos_summary.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pos", "pos_label", "types", "tokens"])
        for tag in target_pos:
            w.writerow([tag, POS_LABELS.get(tag, ""), types_by_pos[tag], pos_tokens[tag]])

    # 3) by_pos/ — 품사별 빈도순 파일 (열 구성은 word_freq.csv와 같음)
    pos_dir = out_dir / "by_pos"
    pos_dir.mkdir(exist_ok=True)
    for tag in target_pos:
        rows = [kv for kv in sort_rows(freq) if kv[0][1] == tag]
        write_word_freq(pos_dir / f"word_freq_{tag}_{POS_LABELS.get(tag, tag)}.csv", rows, total_tokens,
                        para_freq, doc_freq, surfaces, first_seen, args.min_freq)

    # 4) word_freq_by_group.csv — 묶음별 (앞에 group 열 추가, per_million은 묶음 안 토큰 기준)
    #    묶음마다 임시 파일을 쓴 뒤 헤더를 한 번만 남기고 합친다.
    tmp_paths = []
    for group in GROUP_ORDER:
        if not g_tokens[group]:
            continue
        p = out_dir / f".tmp_{group}.csv"
        write_word_freq(p, sort_rows(g_freq[group]), g_tokens[group], g_para[group], g_doc[group],
                        g_surfaces[group], g_first[group], args.min_freq,
                        prefix_cols=("group",), prefix_vals=(group,))
        tmp_paths.append(p)
    with open(out_dir / "word_freq_by_group.csv", "w", encoding="utf-8-sig", newline="") as out:
        for j, p in enumerate(tmp_paths):
            with open(p, encoding="utf-8-sig") as src:
                lines = src.readlines()
            out.writelines(lines if j == 0 else lines[1:])
            p.unlink()

    # 5) corpus_summary.json
    summary = {
        "input": str(in_dir.resolve()),
        "n_files": len(files),
        "n_documents": len(files),
        "n_paragraphs": n_paras,
        "unit": {"document": "교과서 1권", "paragraph": "1쪽 (first_para_id = '<책 이름>.p<쪽>')"},
        "total_tokens": total_tokens,
        "total_types": len(freq),
        "groups": {g: {"label": GROUP_LABELS[g], "tokens": g_tokens[g], "types": len(g_freq[g])}
                   for g in GROUP_ORDER if g_tokens[g]},
        "settings": {"pos": target_pos, "min_freq": args.min_freq},
        "analyzer": {"name": "kiwipiepy", "version": kiwipiepy.__version__,
                     "tag_normalization": "'VV-R', 'VA-I' 등 규칙/불규칙 표시는 기본 태그(VV, VA)로 합침"},
        "lemma_rule": "동사·형용사(VV, VA 등)는 kiwipiepy 어간 + '다', 그 외 품사는 형태 그대로",
        "normalization": "per_million = freq / total_tokens(선택 품사 전체 토큰) × 1,000,000; "
                         "word_freq_by_group.csv는 묶음 안 토큰 기준",
        "surface_top5": "형태소가 속한 어절(공백 단위, 앞뒤 문장부호 제거)의 상위 5개와 빈도",
        "min_freq_applies_to": "word_freq CSV 출력만 (total_tokens, pos_summary는 필터 전 기준)",
        "elapsed_seconds": round(elapsed, 1),
        "documents": doc_info,
    }
    with open(out_dir / "corpus_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"\n완료: 교과서 {len(files)}권, 쪽 {n_paras}개, 토큰 {total_tokens:,}개, 유형 {len(freq):,}개, "
          f"{elapsed:.1f}초 (품사 {','.join(target_pos)}, 최소 빈도 {args.min_freq})")
    print(f"결과 폴더: {out_dir.resolve()}")


if __name__ == "__main__":
    main()
