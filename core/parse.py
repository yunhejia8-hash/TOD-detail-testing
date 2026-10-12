# -*- coding: utf-8 -*-
"""
从识别文本中解析：发票号/合同号/付款单号、金额（含税/不含税）、对方名称。
含发票号"硬校验"（§6.4）。PaddleEngine、文本层、以及对 Qwen3-VL 输出的复核
都复用本模块，保证同一套口径。
"""
from __future__ import annotations
import re
from typing import List, Optional
import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from engines.base import Amount, RecognizeResult

# 金额：必须带小数或千分位，避免把长串纯数字 ID 误当金额
RE_AMOUNT_STRICT = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{2})(?![\d])")
# 关键词后允许整数金额
RE_AMOUNT_LOOSE = re.compile(r"(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)")


def normalize_text(text: str) -> str:
    """Unicode 短横归一、去全角空格。"""
    if not text:
        return ""
    for d in config.DASH_CHARS:
        text = text.replace(d, "-")
    text = text.replace("　", " ").replace("：", ":")
    return text


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


# ---------------- 发票号硬校验 ----------------
def validate_invoice_number(num: str) -> bool:
    """20 位全电发票号，或旧版 8 位发票号码。"""
    return bool(re.fullmatch(r"\d{20}", num) or re.fullmatch(r"\d{8}", num))


def fix_digits(s: str) -> str:
    """OCR 数字混淆纠正（仅用于疑似号码串）。"""
    return s.translate(config.DIGIT_CONFUSION)


# ---------------- 号码提取 ----------------
_INV_LINE_KW = ("发票号码", "发票号", "票号", "No", "NO", "N0", "号码")
# 税号/统一社会信用代码行里的 20 位数字不是发票号，负向过滤
_INV_NEG_KW = ("识别号", "信用代码", "纳税人", "社会", "开户行", "账号", "账户")
# 买方税号标记：发票号若每次出现都紧挨这些词，判定为买方税号而剔除
_BUYER_MARKERS = ("识别号", "纳税人", "信用代码", "购买方", "买方")


def _is_buyer_tax_id(text: str, num: str) -> bool:
    """num 的每一处出现，其前 30 字符内都含买方/税号标记 → 是买方税号，应剔除。
    （真发票号通常紧挨"发票号码"，不会命中买方标记 → 保留）"""
    import re as _re
    occ = list(_re.finditer(_re.escape(num), text))
    if not occ:
        return False
    for m in occ:
        pre = text[max(0, m.start() - 30): m.start()]
        if not any(k in pre for k in _BUYER_MARKERS):
            return False
    return True


def _is_date8(n: str) -> bool:
    """8 位是否像 yyyymmdd 日期（排除日期误判为发票号）。"""
    import re as _re
    return bool(_re.fullmatch(r"(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", n))


def extract_invoice_numbers(text: str) -> List[str]:
    """20 位全电发票号全收；8 位旧版发票号仅在含"发票号/No"的行内且不像日期时收，
    大幅降低密码区/税号/日期串的误报。"""
    res, seen = [], set()

    def add(n):
        if n not in seen and validate_invoice_number(n):
            seen.add(n)
            res.append(n)

    fallback20 = []   # 非税号行但也没"发票号"标签的 20 位数，仅在无更优时回退采用
    for line in text.splitlines():
        neg = any(kw in line for kw in _INV_NEG_KW)
        if neg:
            continue
        has_pos = any(kw in line for kw in _INV_LINE_KW)
        for m in config.RE_INVOICE_20.finditer(line):
            (add if has_pos else fallback20.append)(m.group(1))
        if has_pos:
            for m in config.RE_INVOICE_8.finditer(line):
                n = m.group(1)
                if not _is_date8(n):
                    add(n)
    # 回退：若带标签的发票号一个都没取到，才收非税号行的 20 位数
    if not res:
        for n in fallback20:
            add(n)
    # 剔除买方税号（每次出现都紧挨买方/税号标记的号）
    res = [n for n in res if not _is_buyer_tax_id(text, n)]
    return res


def extract_payment_numbers(text: str) -> List[str]:
    out, seen = [], set()
    for m in config.RE_PAYMENT.finditer(text):
        n = m.group(1)
        # 排除 20 位纯数字（更像全电发票号）
        if re.fullmatch(r"\d+", n):
            continue
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def extract_contract_numbers(text: str) -> List[str]:
    out, seen = [], set()
    for m in config.RE_CONTRACT.finditer(text):
        n = m.group(1)
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


# ---------------- 金额提取（含税/不含税口径）----------------
def extract_amounts(text: str) -> List[Amount]:
    """返回去重后的金额列表，按关键词判定含税/不含税，其余记未知。"""
    kind_of = {}   # value -> kind，labeled 优先

    def mark(val: float, kind: str):
        if val <= 0:
            return
        if val not in kind_of or kind_of[val] == "未知":
            kind_of[val] = kind

    lines = text.splitlines()
    for line in lines:
        # 含税口径
        for kw in config.AMOUNT_KEYWORDS_TAX_INCL:
            idx = line.find(kw)
            if idx >= 0:
                m = RE_AMOUNT_LOOSE.search(line, idx)
                if m and m.group(1):
                    try:
                        mark(_to_float(m.group(1)), "含税")
                    except ValueError:
                        pass
        # 不含税口径
        for kw in config.AMOUNT_KEYWORDS_TAX_EXCL:
            idx = line.find(kw)
            if idx >= 0:
                m = RE_AMOUNT_LOOSE.search(line, idx)
                if m and m.group(1):
                    try:
                        mark(_to_float(m.group(1)), "不含税")
                    except ValueError:
                        pass
    # 兜底：全文严格金额（带小数/千分位）记为未知
    for m in RE_AMOUNT_STRICT.finditer(text):
        try:
            mark(_to_float(m.group(1)), "未知")
        except ValueError:
            pass
    return [Amount(v, k) for v, k in kind_of.items()]


# ---------------- 对方名称 ----------------
def extract_companies(text: str) -> List[str]:
    out, seen = [], set()
    for m in config.RE_COMPANY.finditer(text):
        name = m.group(0).strip()
        if 3 <= len(name) <= 30 and name not in seen:
            seen.add(name)
            out.append(name)
    return out


_CP_EXCLUDE = ("银行", "税务", "国家税务")


def extract_counterparty(text: str, companies: List[str] = None) -> str:
    """优先取"销售方"上下文后的公司名；排除银行、税务等。"""
    companies = companies if companies is not None else extract_companies(text)
    cand = [c for c in companies if not any(x in c for x in _CP_EXCLUDE)]
    # 优先"销售方/收款"上下文
    for label in ("销售方", "销售", "收款单位", "收款方", "对方名称", "对方单位", "供应商"):
        idx = text.find(label)
        if idx >= 0:
            tail = text[idx: idx + 80]
            for c in cand:
                if c in tail:
                    return c
    cand.sort(key=len, reverse=True)
    return cand[0] if cand else ""


# ---------------- 日期提取 ----------------
_RE_DATE = re.compile(
    r"(?P<y>20\d{2})[年/\-\.](?P<m>0?[1-9]|1[0-2])[月/\-\.](?P<d>[12]\d|3[01]|0?[1-9])(?:日)?(?![0-9])"
)
# 开票日期 2023年05月31日 → YYYY-MM-DD
_DATE_MAKERS = ("开票", "开票日期", "日期", "时间", "年月日", "制单", "填开", "打印")


def extract_dates(text: str) -> List[str]:
    """提取所有日期，统一为 YYYY-MM-DD 格式，去重排序。"""
    seen, out = set(), []
    for m in _RE_DATE.finditer(text):
        y, mo, d = m.group("y"), m.group("m"), m.group("d")
        try:
            dt = f"{int(y)}-{int(mo):02d}-{int(d):02d}"
            if dt not in seen:
                seen.add(dt)
                out.append(dt)
        except ValueError:
            pass
    out.sort()
    # 优先"开票日期"附近的日期
    prioritized = []
    for mk in _DATE_MAKERS:
        idx = text.find(mk)
        if idx >= 0:
            for m in _RE_DATE.finditer(text, idx, min(len(text), idx + 40)):
                try:
                    dt = f"{int(m.group('y'))}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"
                    if dt in seen:
                        prioritized.append(dt)
                        seen.discard(dt)
                except ValueError:
                    pass
    return prioritized + [d for d in out if d in seen]


def parse_date(s) -> Optional[str]:
    """把 Excel 单元格的日期值解析为 YYYY-MM-DD 字符串。"""
    import datetime as _dt
    if s is None:
        return None
    if isinstance(s, _dt.datetime):
        return s.strftime("%Y-%m-%d")
    if isinstance(s, _dt.date):
        return s.strftime("%Y-%m-%d")
    if isinstance(s, (int, float)):
        try:
            return _dt.datetime(1899, 12, 30) + _dt.timedelta(days=int(s))
        except Exception:
            return None
    s = str(s).strip()
    if not s:
        return None
    m = _RE_DATE.search(s)
    if m:
        try:
            return f"{int(m.group('y'))}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"
        except ValueError:
            return None
    return None
def parse_all(text: str, engine: str = "", confidence: float = 0.0) -> RecognizeResult:
    text = normalize_text(text)
    inv = extract_invoice_numbers(text)
    pay = extract_payment_numbers(text)
    con = extract_contract_numbers(text)
    amounts = extract_amounts(text)
    companies = extract_companies(text)
    cp = extract_counterparty(text, companies)
    dates = extract_dates(text)
    return RecognizeResult(
        raw_text=text,
        doc_numbers={"发票": inv, "合同": con, "付款单": pay},
        amounts=amounts,
        counterparty=cp,
        companies=companies,
        confidence=confidence,
        engine=engine,
        passed_validation=bool(inv),
        dates=dates,
    )
