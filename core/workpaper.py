# -*- coding: utf-8 -*-
"""
底稿读写 — 通用列定位（概念组匹配 + 多行表头 + 子目标列拆分）。
"""
from __future__ import annotations
import sys, os, re
from dataclasses import dataclass, field
from typing import Dict, List, Optional
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, column_index_from_string
from openpyxl.styles import Alignment
from rapidfuzz import fuzz


# 字段 -> 期望表头（保留作为兜底，主匹配走概念组）
FIELD_EXPECT = {
    "id":       ["样本序号", "序号", "项目序号"],
    "amount":   ["金额", "收入", "成本", "资产原价", "原值", "新增资产金额", "在建工程转出金额", "应收金额"],
    "summary":  ["名称", "摘要", "资产名称", "项目名称", "客户名称", "固定资产名称"],
    "target":   ["证据", "支持", "描述", "获得的证据/支持的描述", "关键支持性文件"],
    "fill":     ["支持性文件中的资产金额"],
    "diff":     ["差异"],
}
REQUIRED_FIELDS = ["id", "amount", "summary", "target"]

# 子目标列 → 概念组（这些列不存在时正常跳过，不影响主流程）
SUB_TARGET_FIELDS = {
    "contract_col": ["合同", "合同号", "合同编号", "合同号/文件号", "订单号", "协议编号"],
    "invoice_col":  ["发票", "发票号", "发票编号", "发票代码"],
    "payment_col":  ["付款单", "付款单号", "付款编号", "付款凭证"],
    "evidence_col": ["关键支持性文件", "文件名称", "证据描述", "证据名称"],
}

def _merge_custom_fields():
    _path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "custom_keywords.json")
    if not os.path.exists(_path):
        return
    try:
        import json as _json
        with open(_path, "r", encoding="utf-8") as f:
            d = _json.load(f)
        for k, v in d.get("field_expect", {}).items():
            if k in FIELD_EXPECT:
                for x in v:
                    if x not in FIELD_EXPECT[k]:
                        FIELD_EXPECT[k].append(x)
            elif k in SUB_TARGET_FIELDS:
                for x in v:
                    if x not in SUB_TARGET_FIELDS[k]:
                        SUB_TARGET_FIELDS[k].append(x)
            else:
                FIELD_EXPECT[k] = list(v)
    except Exception:
        pass
_merge_custom_fields()


def normalize_header(s) -> str:
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\n", "").replace("\r", "").replace(" ", "").replace("　", "")
    s = re.sub(r"[（(].*?[)）]", "", s)
    s = s.replace("：", ":")
    return s.strip()


@dataclass
class ColumnHit:
    field: str
    col_idx: int
    col_letter: str
    actual_header: str
    score: float
    fuzzy: bool


@dataclass
class Sample:
    row: int
    sid: str
    amount: Optional[float]
    summary: str
    sample_date: Optional[str] = None  # YYYY-MM-DD


@dataclass
class WorkpaperLayout:
    sheet: str
    header_row: int
    header_rows: List[int] = field(default_factory=list)  # 多行表头
    columns: Dict[str, ColumnHit] = field(default_factory=dict)
    sub_columns: Dict[str, ColumnHit] = field(default_factory=dict)
    samples: List[Sample] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


_COL_RE = re.compile(r"^[A-Za-z]{1,2}$")
_CHINESE = re.compile(r"[一-鿿]")

# 概念组映射：field → list of concept terms
_CONCEPT_MAP = {
    "id": config.ID_CONCEPTS,
    "amount": config.AMOUNT_CONCEPTS,
    "summary": config.SUMMARY_CONCEPTS,
    "target": config.TARGET_CONCEPTS,
    "diff": config.DIFF_CONCEPTS,
    "date": config.DATE_CONCEPTS,
}
_SUB_CONCEPT_MAP = {
    "contract_col": config.SUB_CONTRACT_CONCEPTS,
    "invoice_col": config.SUB_INVOICE_CONCEPTS,
    "payment_col": config.SUB_PAYMENT_CONCEPTS,
    "evidence_col": config.SUB_EVIDENCE_CONCEPTS,
}
# 负向词：表头含这些→说明是编码/单号/日期/类型类列，削弱匹配
_NEG_AMOUNT = re.compile(r"单号|编码|编号|日期|类型|税率|代号|代码|序号|随机")
_NEG_SUMMARY = re.compile(r"编码|日期|税率|规格|代号|代码|随机|类型")
_NEG_ID = re.compile(r"类型|类别|文件|档案")


def _is_label_cell(v) -> bool:
    """判断单元格是否'像列名'（短文本、含中文或字母、非纯数字）。"""
    hn = normalize_header(v)
    if not hn or len(hn) < 2 or len(hn) > 22:
        return False
    # 纯数字/符号/日期 → 不是列名
    if re.fullmatch(r"[\d.,%\-/ xX×]+", hn):
        return False
    # 像 GUID
    if re.fullmatch(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", hn, re.I):
        return False
    # 含中文 或 短英文缩写
    if _CHINESE.search(hn):
        return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_ /]{1,20}", hn):
        return True
    return False


def _concept_score(concepts: List[str], cell_norm: str, negative=None) -> float:
    """单元格匹配概念组的最佳得分。negative 为负向词正则，命中则降权。"""
    best = 0.0
    for c in concepts:
        cn = normalize_header(c)
        if not cn or not cell_norm:
            continue
        if cn == cell_norm:
            best = max(best, 1.0)
        elif cn in cell_norm or cell_norm in cn:
            best = max(best, 0.85)
        else:
            best = max(best, fuzz.ratio(cn, cell_norm) / 100.0)
    if negative and negative.search(cell_norm):
        best *= 0.3  # 编码/日期/单号类列大幅降权
    return best


class Workpaper:

    @staticmethod
    def _score(expected: str, cell_norm: str) -> float:
        e = normalize_header(expected)
        if not cell_norm:
            return 0.0
        if e == cell_norm:
            return 1.0
        if e in cell_norm or cell_norm in e:
            return 0.9
        return fuzz.ratio(e, cell_norm) / 100.0
    def __init__(self, path: str, sheet: str = "", expect_overrides: dict = None):
        self.path = path
        self.sheet_arg = sheet
        self._col_letter = {}
        self.expect = {k: list(v) for k, v in FIELD_EXPECT.items()}
        if expect_overrides:
            for k, v in expect_overrides.items():
                if not v:
                    continue
                if _COL_RE.match(v):
                    self._col_letter[k] = v.upper()
                else:
                    self.expect[k] = [v] + self.expect.get(k, [])
        self._wb = load_workbook(path, data_only=True)

    # ---------- 表头行识别（通用：数像列名的格）----------
    @staticmethod
    def _row_label_count(ws, r) -> int:
        return sum(1 for c in ws[r] if _is_label_cell(c.value))

    def _pick_sheet(self):
        if self.sheet_arg:
            if self.sheet_arg in self._wb.sheetnames:
                return self._wb[self.sheet_arg]
            cleaned = self.sheet_arg.strip().replace("　", " ").replace("\r", "").replace("\n", "")
            cleaned = re.sub(r"\s+", " ", cleaned)
            exact = {s: s for s in self._wb.sheetnames}
            if cleaned in exact:
                return self._wb[exact[cleaned]]
            cleaned_names = {}
            for s in self._wb.sheetnames:
                cn = s.strip().replace("　", " ").replace("\r", "").replace("\n", "")
                cn = re.sub(r"\s+", " ", cn)
                cleaned_names[cn] = s
            if cleaned in cleaned_names:
                return self._wb[cleaned_names[cleaned]]
            for s in self._wb.sheetnames:
                if s.strip().replace("　", " ").startswith(cleaned):
                    return self._wb[s]
            for s in self._wb.sheetnames:
                if cleaned in s or s in cleaned:
                    return self._wb[s]
            raise ValueError(f"找不到工作表「{self.sheet_arg}」，可选：{self._wb.sheetnames}")

        # 自动选择：优先测试类表
        candidates = list(self._wb.worksheets)
        test_sheets = [ws for ws in candidates if _is_testing_sheet(ws.title)]
        search = test_sheets if test_sheets else candidates
        scored = []
        for ws in search:
            top = min(ws.max_row, 60)
            best_r, best_h = -1, -1
            for r in range(1, top + 1):
                h = self._row_label_count(ws, r)
                if h > best_h:
                    best_h, best_r = h, r
            if best_r < 1:
                continue
            cols = self._locate_columns(ws, best_r)
            found = sum(1 for f in REQUIRED_FIELDS if f in cols)
            scored.append((found, best_h, ws, best_r))
        if not scored:
            raise ValueError("未能自动识别表头行；请用 --sheet 指定工作表或检查底稿")
        scored.sort(reverse=True, key=lambda x: (x[0], x[1]))
        self._precomputed_header_row = scored[0][3]
        return scored[0][2]

    def _find_header_rows(self, ws, primary_row) -> List[int]:
        """找主表头行 + 下方子表头行（某些列名在下一行出现）。"""
        rows = [primary_row]
        for offset in (1, 2):
            r = primary_row + offset
            if r > ws.max_row:
                break
            new_labels = 0
            for c in ws[r]:
                col = c.column
                hn = normalize_header(c.value)
                if not hn:
                    continue
                # 该列在主表头行是否为空或以 "(列" 开头
                primary_hn = normalize_header(ws.cell(primary_row, col).value)
                if primary_hn:
                    continue  # 主表头已经有内容了
                if _is_label_cell(c.value):
                    new_labels += 1
            if new_labels >= 2:
                rows.append(r)
        return rows

    def _find_header_row(self, ws) -> int:
        if hasattr(self, "_precomputed_header_row") and self._precomputed_header_row > 0:
            r = self._precomputed_header_row
            self._precomputed_header_row = 0
            return r
        top = min(ws.max_row, 60)
        candidates = []
        for r in range(1, top + 1):
            lc = self._row_label_count(ws, r)
            if lc >= config.HEADER_ROW_MIN_LABELS:
                cols = self._locate_columns(ws, r)
                found = sum(1 for f in REQUIRED_FIELDS if f in cols)
                candidates.append((found, lc, r))
        if not candidates:
            raise ValueError("未能自动识别表头行；请用 --sheet 指定工作表或检查底稿")
        candidates.sort(reverse=True, key=lambda x: (x[0], x[1], x[2]))
        return candidates[0][2]

    # ---------- 列定位 ----------
    def _locate_columns(self, ws, header_row, header_rows=None) -> Dict[str, ColumnHit]:
        """从表头行定位列：概念组匹配 + legacy FIELD_EXPECT 兜底 + 列字母强制 + id回退。
        主列只用第一行表头匹配；子表头的列留给 _locate_sub_columns。"""
        if header_rows is None:
            header_rows = [header_row]

        hits, used_cols = {}, set()

        # 第一步：列字母直接定位
        for fld, col_letter in self._col_letter.items():
            idx = column_index_from_string(col_letter)
            raw = normalize_header(ws.cell(header_row, idx).value) or f"(列{col_letter}·手动)"
            hits[fld] = ColumnHit(fld, idx, get_column_letter(idx), raw, 1.0, fuzzy=False)
            used_cols.add(idx)

        # 第二步：只从主表头行收集 cells（子表头留给 _locate_sub_columns）
        all_cells = {}
        for c in ws[header_row]:
            hn = normalize_header(c.value)
            if hn:
                all_cells[c.column] = hn

        # 第三步：概念组 + FIELD_EXPECT 联合打分
        candidates = []
        field_concepts = {}
        for fld in REQUIRED_FIELDS + ["diff", "fill", "date"]:
            terms = list(self.expect.get(fld, []))
            if fld in _CONCEPT_MAP:
                terms = list(_CONCEPT_MAP[fld]) + terms
            field_concepts[fld] = terms

        for fld, concepts in field_concepts.items():
            if fld in hits:
                continue
            for idx, hn in all_cells.items():
                if idx in used_cols:
                    continue
                # 先用概念组打分，再用 FIELD_EXPECT 兜底
                neg = {"amount": _NEG_AMOUNT, "summary": _NEG_SUMMARY, "id": _NEG_ID}.get(fld)
                sc = _concept_score(concepts, hn, negative=neg)
                if sc < 0.55:
                    expect_names = self.expect.get(fld, [])
                    if expect_names:
                        sc = max(self._score(n, hn) for n in expect_names)
                if sc >= 0.55:
                    candidates.append((sc, fld, idx, hn))

        candidates.sort(reverse=True, key=lambda x: x[0])
        used_flds = set(hits.keys())
        for sc, fld, idx, hn in candidates:
            if fld in used_flds or idx in used_cols:
                continue
            hits[fld] = ColumnHit(fld, idx, get_column_letter(idx), hn, sc, fuzzy=sc < 0.88)
            used_flds.add(fld)
            used_cols.add(idx)

        # id 列回退：首列表头为空但数据行有连续整数
        if "id" not in hits:
            for test_col in (1, 2):
                if test_col in used_cols:
                    continue
                id_count = 0
                for r in range(header_row + 1, min(header_row + 4, ws.max_row + 1)):
                    v = ws.cell(r, test_col).value
                    if v is not None and isinstance(v, (int, float)) and float(v) == int(v) and int(v) > 0:
                        id_count += 1
                if id_count >= 2:
                    hits["id"] = ColumnHit("id", test_col, get_column_letter(test_col),
                                           "(首列·自动识别)", 0.95, fuzzy=False)
                    used_cols.add(test_col)
                    break

        return hits

    def _locate_sub_columns(self, ws, header_rows) -> Dict[str, ColumnHit]:
        """定位子目标列：只从子表头行（header_rows[1:]）匹配，不与主列抢。"""
        if len(header_rows) < 2:
            return {}
        all_cells = {}
        for hr in header_rows[1:]:  # 只扫描子表头行
            for c in ws[hr]:
                hn = normalize_header(c.value)
                if hn and c.column not in all_cells:
                    all_cells[c.column] = hn

        hits = {}
        for fld, concepts in _SUB_CONCEPT_MAP.items():
            for idx, hn in all_cells.items():
                sc = _concept_score(list(concepts), hn)  # 子列不设负向词，概念组本身已够精确
                if sc >= 0.65:
                    hits[fld] = ColumnHit(fld, idx, get_column_letter(idx), hn, sc, fuzzy=sc < 0.88)
                    break
        return hits

    # ---------- 样本读取 ----------
    @staticmethod
    def _is_id(v) -> bool:
        if v is None:
            return False
        if isinstance(v, (int, float)):
            return float(v) == int(v) and int(v) > 0
        return bool(re.fullmatch(r"\d{1,6}", str(v).strip()))

    def _read_samples(self, ws, header_row, cols) -> List[Sample]:
        id_idx = cols["id"].col_idx
        amt_idx = cols["amount"].col_idx
        sum_idx = cols["summary"].col_idx
        date_idx = cols["date"].col_idx if "date" in cols else None
        out = []
        for r in range(header_row + 1, ws.max_row + 1):
            v = ws.cell(r, id_idx).value
            if not self._is_id(v):
                continue
            sid = str(int(v)) if isinstance(v, (int, float)) else str(v).strip()
            amt = ws.cell(r, amt_idx).value
            try:
                amt = float(amt) if amt is not None else None
            except (ValueError, TypeError):
                amt = None
            summ = ws.cell(r, sum_idx).value
            # 读取日期
            date_str = ""
            if date_idx:
                from core.parse import parse_date
                date_str = parse_date(ws.cell(r, date_idx).value) or ""
            out.append(Sample(r, sid, amt, "" if summ is None else str(summ), date_str))
        return out

    # ---------- 对外：定位 ----------
    def analyze(self) -> WorkpaperLayout:
        ws = self._pick_sheet()
        header_row = self._find_header_row(ws)
        header_rows = self._find_header_rows(ws, header_row)
        cols = self._locate_columns(ws, header_row, header_rows)

        # 子目标列（合同号/发票号/付款单号分别列）
        sub_cols = self._locate_sub_columns(ws, header_rows)

        warns = []
        missing = [f for f in REQUIRED_FIELDS if f not in cols]
        if missing:
            actual = [normalize_header(c.value) for c in ws[header_row] if c.value]
            raise ValueError(
                f"必需列未定位到：{missing}。请在 GUI 手动填写对应的列字母或表头文字。\n"
                f"（表头行 {header_row} 实际表头：{actual}）")
        for f, ch in cols.items():
            if ch.fuzzy:
                warns.append(f"列「{f}」为模糊命中（{ch.col_letter}『{ch.actual_header}』, 相似度{ch.score:.2f}），请核对")
        samples = self._read_samples(ws, header_row, cols)
        return WorkpaperLayout(ws.title, header_row, header_rows, cols, sub_cols, samples, warns)

    # ---------- 写出已填底稿（含子目标列）----------
    def write_filled(self, layout: WorkpaperLayout, fills: Dict[str, dict],
                     out_path: str, overwrite: bool = False):
        """fills[sid] = {target, contract_col, invoice_col, payment_col, evidence_col, fill_amount, diff}"""
        wb = load_workbook(self.path)
        ws = wb[layout.sheet]
        wrap = Alignment(wrap_text=True, vertical="top")

        # 主目标列
        tcol = layout.columns.get("target", None)
        t_idx = tcol.col_idx if tcol else None
        f_idx = layout.columns["fill"].col_idx if "fill" in layout.columns else None
        d_idx = layout.columns["diff"].col_idx if "diff" in layout.columns else None

        # 子目标列索引
        sub_idx = {}
        for fld in SUB_TARGET_FIELDS:
            ch = layout.sub_columns.get(fld)
            if ch:
                sub_idx[fld] = ch.col_idx

        for s in layout.samples:
            f = fills.get(s.sid)
            if not f:
                continue
            if t_idx and f.get("target"):
                cell = ws.cell(s.row, t_idx)
                if overwrite or cell.value in (None, ""):
                    cell.value = f["target"]
                    cell.alignment = wrap
            # 子目标列
            for fld, idx in sub_idx.items():
                val = f.get(fld, "")
                if val:
                    cell = ws.cell(s.row, idx)
                    if overwrite or cell.value in (None, ""):
                        cell.value = val
                        cell.alignment = wrap
            if f_idx and f.get("fill_amount") is not None:
                c = ws.cell(s.row, f_idx)
                if overwrite or c.value in (None, ""):
                    c.value = f["fill_amount"]
            if d_idx and f.get("diff") is not None:
                c = ws.cell(s.row, d_idx)
                if overwrite or c.value in (None, ""):
                    c.value = f["diff"]
        wb.save(out_path)


def _is_testing_sheet(name: str) -> bool:
    markers = ["测试", "新增", "处置", "转入", "转出", "TOD", "样本", "抽凭", "核对", "直营", "收入"]
    return any(m in name for m in markers)


def learn_keywords(path):
    """从底稿中提取新表头关键词和字段列名映射。仅扫描测试类工作表。"""
    wb = load_workbook(path, data_only=True)
    all_sheet_rows = []

    _RE_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

    test_sheets = [ws for ws in wb.worksheets if _is_testing_sheet(ws.title)]
    if not test_sheets:
        test_sheets = list(wb.worksheets)

    for ws in test_sheets:
        for r in range(1, min(ws.max_row + 1, 60)):
            raw_cells = [(c.column, str(c.value).strip()) for c in ws[r] if c.value is not None and str(c.value).strip()]
            if len(raw_cells) < 4:
                continue
            texts = [str(c[1]).strip() for c in raw_cells]
            if re.fullmatch(r"\d{1,4}", texts[0]):
                continue
            long_texts = [t for t in texts if len(t) > 40]
            if len(long_texts) > len(texts) * 0.2:
                continue
            labels = sum(1 for rc in raw_cells if _is_label_cell(rc[1]))
            if labels < 3:
                continue
            row_hns = {}
            for col, text in raw_cells:
                hn = normalize_header(text)
                if 2 <= len(hn) <= 18:
                    row_hns[col] = hn
            if len(row_hns) >= 4:
                all_sheet_rows.append((ws.title, r, raw_cells, row_hns))

    if not all_sheet_rows:
        return [], {}

    from collections import defaultdict
    sheet_best = defaultdict(list)
    for sn, rn, rc, rh in all_sheet_rows:
        sheet_best[sn].append((rn, rc, rh))

    keywords = set()
    field_candidates = {}

    def _is_good_token(t: str) -> bool:
        t = t.strip()
        if len(t) < 2 or len(t) > 18:
            return False
        if _RE_GUID.match(t) or re.fullmatch(r"[\d.,%\-/ ]+", t):
            return False
        if _CHINESE.search(t):
            return not re.search(r"[\d]{8,}", t)
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_ ]{1,17}", t):
            return not re.fullmatch(r"[A-Z]{2,6}\d{3,}", t)
        return False

    all_concept_terms = set()
    for terms in list(_CONCEPT_MAP.values()) + list(_SUB_CONCEPT_MAP.values()):
        all_concept_terms.update(terms)

    for sn, rows in sheet_best.items():
        for rn, raw_cells, row_hns in sorted(rows)[:2]:
            for hn in row_hns.values():
                for token in re.split(r"[/\s　、，/·]+", hn):
                    if _is_good_token(token.strip()):
                        keywords.add(token.strip())

            for col, hn in row_hns.items():
                for fld, concepts in {**_CONCEPT_MAP, **_SUB_CONCEPT_MAP}.items():
                    if _concept_score(list(concepts) if isinstance(concepts, (list, set)) else list(concepts), hn) >= 0.70:
                        target = FIELD_EXPECT if fld in FIELD_EXPECT else SUB_TARGET_FIELDS
                        if hn not in target.get(fld, []):
                            field_candidates.setdefault(fld, set()).add(hn)
                        break

    new_kw = sorted(k for k in keywords if normalize_header(k) not in all_concept_terms)
    new_ft = {}
    for fld, terms in field_candidates.items():
        target = FIELD_EXPECT if fld in FIELD_EXPECT else SUB_TARGET_FIELDS
        existing = set(normalize_header(t) for t in target.get(fld, []))
        fresh = sorted(t for t in terms if normalize_header(t) not in existing and 2 <= len(t) <= 18)
        if fresh:
            new_ft[fld] = fresh

    return new_kw, new_ft
