# -*- coding: utf-8 -*-
"""
核对逻辑（§7，业务核心）：
- 金额核对：单张 / 同类多张求和 / 含税与不含税都试；容差判一致。
- 摘要核对：对方名称 partial_ratio + 词级 token（jieba/2gram），取 max。
- 三态判定 + 软信号：金额精确命中且对方名材料内可反查一致 → 摘要降软信号。
原则：把握不准一律交人工，绝不为"对上"去凑。
"""
from __future__ import annotations
import sys, os
from dataclasses import dataclass, field
from typing import List, Optional
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from rapidfuzz import fuzz
from engines.base import Amount

try:
    import jieba
    jieba.setLogLevel(20)
except Exception:
    jieba = None


# ---------------- 金额核对 ----------------
@dataclass
class AmountResult:
    matched: bool = False
    matched_value: Optional[float] = None
    kind: str = ""          # 命中口径描述
    delta: Optional[float] = None
    candidates: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)   # 命中金额出现在哪些材料(发票/付款单/合同)
    consistency: str = ""   # 发票/付款单/合同 金额一致性说明


def _sources_of(value, by_source, tol):
    """命中金额出现在哪些来源材料里。"""
    if not by_source:
        return []
    out = []
    for s, vals in by_source.items():
        if any(abs(abs(value) - abs(v)) <= max(tol, 0.01) for v in vals):
            out.append(s)
    return out


def _consistency_note(srcs, kind_desc):
    """命中金额在 发票/付款单/合同 间的一致性说明。
    注意：底稿常记不含税，付款单常是含税/分次，单值不同属正常口径差异，不报警。"""
    if "求和" in kind_desc:
        return "多张发票求和=底稿"
    has = lambda x: x in srcs
    if has("发票") and has("付款单"):
        return "发票=付款单一致"
    if has("发票"):
        return "发票(不含税)口径命中·付款单或为含税/分次"
    if has("付款单"):
        return "付款单口径命中·发票或为含税/分次"
    if has("合同"):
        return "合同印证"
    return ""


def _pick_net(vals, tol=1.0):
    """从一张发票的所有金额里挑"不含税"：
    ① 找 含税=不含税+税额 的三元组(含税最大)，取不含税；
    ② 凑不上(常因税额OCR误读)则用 含税÷(1+税率) 反推命中某金额；
    ③ 都不行退而取最大额。"""
    vals = sorted(set(round(v, 2) for v in vals if v > 0), reverse=True)
    if not vals:
        return None
    for a in vals:                       # a 从大到小，最大的是价税合计(含税)
        for b in vals:
            for c in vals:
                if b != a and c != a and b >= c and abs(a - (b + c)) <= tol:
                    return b             # 不含税
    a = vals[0]                          # 最大额视作含税，按常见税率反推不含税
    for r in (0.13, 0.09, 0.06, 0.03, 0.01):
        net = round(a / (1 + r), 2)
        for v in vals:
            if abs(v - net) <= tol:
                return v
    return a


def invoice_net_amounts(file_results):
    """每张发票一个"不含税"金额，用于多张发票求和核对底稿。"""
    nets = []
    for fr in file_results:
        if getattr(getattr(fr, "material", None), "mtype", "") != "发票":
            continue
        net = _pick_net([a.value for a in fr.result.amounts])
        if net:
            nets.append(net)
    return nets


def _hit(s, v, tol, rel_tol):
    if abs(s - v) <= tol:
        return True
    if rel_tol > 0 and abs(s) > 0 and abs(s - v) <= rel_tol * abs(s):
        return True
    return False


def reconcile_amount(S: float, amounts: List[Amount], tol=0.01, rel_tol=0.0,
                     by_source: dict = None, invoice_nets: List[float] = None) -> AmountResult:
    if S is None:
        return AmountResult(False, None, "明细账金额缺失", None)
    # 按绝对值比对：转出测试底稿金额为负(账面价值转出)，而材料金额为正，
    # 审计只看金额大小、不看增/减方向。
    Sa = abs(S)
    cands = []   # (value, 口径描述)
    # 每张发票不含税之和（最贴近"多票拼一项资产"的账面价值）
    if invoice_nets and len(invoice_nets) >= 2:
        cands.append((round(sum(invoice_nets), 2), "多张发票不含税求和"))
    for a in amounts:
        cands.append((a.value, f"单张·{a.kind}"))
    # 同类多张求和
    by_kind = {}
    for a in amounts:
        by_kind.setdefault(a.kind, []).append(a.value)
    for kind, vals in by_kind.items():
        if len(vals) >= 2:
            cands.append((round(sum(vals), 2), f"多张求和·{kind}"))
    # 全部不同值求和（跨类，覆盖 1发票+多付款 等）
    distinct = sorted({a.value for a in amounts})
    if len(distinct) >= 2:
        cands.append((round(sum(distinct), 2), "多张求和·混合"))

    # 去重
    uniq, seen = [], set()
    for v, d in cands:
        key = round(v, 2)
        if key not in seen:
            seen.add(key)
            uniq.append((v, d))
    # 命中：按绝对值比对，优先精确(delta=0)，再容差内，取 |delta| 最小
    matches = [(v, d) for v, d in uniq if _hit(Sa, abs(v), tol, rel_tol)]
    if matches:
        matches.sort(key=lambda x: abs(Sa - abs(x[0])))
        v, d = matches[0]
        srcs = _sources_of(v, by_source, tol)
        cons = _consistency_note(srcs, d)
        return AmountResult(True, v, d, round(Sa - abs(v), 2),
                            candidates=[f"{vv:.2f}({dd})" for vv, dd in uniq],
                            sources=srcs, consistency=cons)
    # 未命中：给最接近的（按绝对值）
    near = min(uniq, key=lambda x: abs(Sa - abs(x[0]))) if uniq else None
    return AmountResult(False, None,
                        ("最接近 %.2f(%s)" % (near[0], near[1]) if near else "无金额候选"),
                        (round(Sa - abs(near[0]), 2) if near else None),
                        candidates=[f"{vv:.2f}({dd})" for vv, dd in uniq])


# ---------------- 摘要核对 ----------------
@dataclass
class SummaryResult:
    score: float = 0.0
    method: str = ""
    best_name: str = ""
    has_counterparty_name: bool = False


def _bigrams(s: str):
    s = "".join(ch for ch in s if ch.strip())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())


def _token_score(summary: str, text: str, method: str) -> float:
    if not summary or not text:
        return 0.0
    if method == "jieba" and jieba is not None:
        a = " ".join(jieba.cut(summary))
        b = " ".join(jieba.cut(text))
        return fuzz.token_set_ratio(a, b) / 100.0
    if method == "2gram":
        ba, bb = _bigrams(summary), _bigrams(text)
        if not ba or not bb:
            return 0.0
        return len(ba & bb) / len(ba | bb)
    return fuzz.ratio(summary, text) / 100.0


def reconcile_summary(summary: str, counterparties: List[str],
                      threshold=0.6, method="jieba") -> SummaryResult:
    summary = summary or ""
    best_name, score1 = "", 0.0
    for name in counterparties:
        if not name:
            continue
        s = fuzz.partial_ratio(name, summary) / 100.0
        if s > score1:
            score1, best_name = s, name
    joined = " ".join(c for c in counterparties if c)
    score2 = _token_score(summary, joined, method)
    score = max(score1, score2)
    return SummaryResult(round(score, 3), method, best_name,
                         has_counterparty_name=(score1 >= 0.8))


# ---------------- 对方名称材料内可反查一致性 ----------------
def counterparty_consistent(per_file_names: List[str]) -> bool:
    """≥2 个材料给出对方名且互相吻合（§7.3 软信号触发条件之一）。"""
    names = [n for n in per_file_names if n]
    if len(names) < 2:
        return False
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if fuzz.partial_ratio(names[i], names[j]) >= config.DEFAULTS["counterparty_consistent_ratio"] * 100:
                return True
    return False


# ---------------- 三态综合判定 ----------------
@dataclass
class Verdict:
    state: str = ""          # ✅一致 / ⚠️存疑 / ❌不一致
    reason: str = ""
    amount_flag_summary_fail: bool = False    # 金额命中·摘要不达标
    soft_passed: bool = False


def judge(amount_res: AmountResult, summary_res: SummaryResult,
          *, soft_summary: bool, cp_consistent: bool, threshold: float,
          conflict: bool, has_key_number: bool, low_conf: bool) -> Verdict:
    # 双引擎发票号冲突 → 存疑
    if conflict:
        return Verdict("⚠️存疑", "双引擎发票号冲突，需人工确认")

    # 关键号码识别失败 → 不一致/待人工
    if not has_key_number:
        return Verdict("❌不一致", "关键号码识别失败（发票/付款单均未取得）—待人工")

    # 金额未命中
    if not amount_res.matched:
        return Verdict("⚠️存疑",
                       f"金额差异（{amount_res.kind}，差额{amount_res.delta}）—待人工")

    summary_ok = summary_res.score >= threshold
    if summary_ok:
        reason = f"金额命中（{amount_res.kind}）｜摘要相似度{summary_res.score:.2f}≥{threshold}"
        if low_conf:
            reason += "｜注意OCR置信度偏低"
        return Verdict("✅一致", reason)

    # 金额命中但摘要不达标
    flag = True
    exact = amount_res.delta == 0
    if soft_summary and exact and cp_consistent:
        return Verdict(
            "✅一致",
            f"金额已确认（{amount_res.kind}·精确命中）·摘要待人工确认"
            f"（相似度{summary_res.score:.2f}<{threshold}，对方名材料内可反查一致）",
            amount_flag_summary_fail=True, soft_passed=True)
    return Verdict(
        "⚠️存疑",
        f"金额已命中（{amount_res.kind}）｜摘要未达标（相似度{summary_res.score:.2f}<{threshold}）—请人工确认摘要",
        amount_flag_summary_fail=True)
