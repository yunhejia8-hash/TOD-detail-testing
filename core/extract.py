# -*- coding: utf-8 -*-
"""
抽取编排（§4）：
- PyMuPDF 文本层优先（本户扫描件极少命中），无文本层则渲染页图走引擎（含混合回退）
- 合同多页大文件：号码已从文件名/付款单取，OCR 仅首页（取对方名/编号），避免整本 OCR
- 按样本聚合所有材料的 号码/金额/对方名称/来源引擎
渲染用 PyMuPDF（无需 poppler）。
"""
from __future__ import annotations
import sys, os
from dataclasses import dataclass, field
from typing import List, Set
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import fitz  # PyMuPDF
from engines.base import RecognizeResult, Amount
from core import parse
from core.materials import Material, SampleMaterials

# 每个 PDF 文件 OCR 的最大页数。合同=0：号码从文件名/付款单取，整本不 OCR（提速）
MAX_PAGES = {"发票": 1, "付款单": 1, "凭证": 1, "合同": 0, "其他": 1}


@dataclass
class FileResult:
    material: Material
    result: RecognizeResult
    used_text_layer: bool


@dataclass
class SampleEvidence:
    sid: str
    invoice_numbers: List[str] = field(default_factory=list)
    contract_numbers: List[str] = field(default_factory=list)
    payment_numbers: List[str] = field(default_factory=list)
    amounts: List[Amount] = field(default_factory=list)
    amount_by_source: dict = field(default_factory=lambda: {"发票": set(), "付款单": set(), "合同": set()})
    counterparties: List[str] = field(default_factory=list)
    confidence: float = 1.0
    engines_used: Set[str] = field(default_factory=set)
    conflict: bool = False
    contract_attempted: bool = False  # 是否进入过合同懒加载路径
    contract_read: bool = False     # 是否已 OCR 合同（懒加载：金额对不上才读）
    contract_failed: bool = False   # 是否有合同读取/OCR 失败
    file_results: List[FileResult] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _render_png(page, dpi=None) -> bytes:
    pix = page.get_pixmap(dpi=dpi or config.DEFAULTS["render_dpi"])
    return pix.tobytes("png")


def _merge(a: RecognizeResult, b: RecognizeResult) -> RecognizeResult:
    def u(k):
        return sorted(set(a.doc_numbers.get(k, [])) | set(b.doc_numbers.get(k, [])))
    amap = {}
    for am in (a.amounts + b.amounts):
        if am.value not in amap or amap[am.value].kind == "未知":
            amap[am.value] = am
    return RecognizeResult(
        raw_text=(a.raw_text + "\n" + b.raw_text),
        doc_numbers={"发票": u("发票"), "合同": u("合同"), "付款单": u("付款单")},
        amounts=list(amap.values()),
        counterparty=a.counterparty or b.counterparty,
        companies=sorted(set(a.companies) | set(b.companies)),
        confidence=min(x for x in [a.confidence, b.confidence] if x > 0) if (a.confidence and b.confidence) else max(a.confidence, b.confidence),
        engine="+".join(sorted({x for x in [a.engine, b.engine] if x})),
        passed_validation=a.passed_validation or b.passed_validation,
        conflict=a.conflict or b.conflict,
        dates=sorted(set(a.dates + b.dates)),
        note="；".join(x for x in (a.note, b.note) if x),
        succeeded=a.succeeded and b.succeeded,
    )


def _extract_file(mat: Material, engine) -> FileResult:
    ext = os.path.splitext(mat.path)[1].lower()
    max_pages = MAX_PAGES.get(mat.mtype, 2)

    # 合同等配置为 0 页的材料统一延迟处理，图片也不能绕过页数限制。
    if max_pages <= 0:
        return FileResult(mat, RecognizeResult(engine=""), used_text_layer=False)

    # 图片：直接 OCR
    if ext in (".jpg", ".jpeg", ".png"):
        with open(mat.path, "rb") as f:
            png = f.read()
        rr = engine.recognize(png)
        return FileResult(mat, rr, used_text_layer=False)

    # PDF
    merged = RecognizeResult(engine="")
    used_text = False
    try:
        doc = fitz.open(mat.path)
    except Exception as e:
        merged.note = f"打开PDF失败:{e}"
        return FileResult(mat, merged, False)
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            text = page.get_text("text") or ""
            if len(text.strip()) >= config.DEFAULTS["min_text_layer_chars"] and any(c.isdigit() for c in text):
                rr = parse.parse_all(text, engine="文本层", confidence=1.0)
                used_text = True
            else:
                rr = engine.recognize(_render_png(page))
            merged = _merge(merged, rr) if merged.engine or merged.raw_text else rr
    finally:
        doc.close()
    return FileResult(mat, merged, used_text)


def _apply_file_result(ev: SampleEvidence, fr: FileResult):
    """把一个文件的完整识别结果合并到样本证据。"""
    mat, r = fr.material, fr.result
    # 发票号只从"发票/凭证"类文件取——付款单/合同上的长号码可能是账号/税号。
    if mat.mtype in ("发票", "凭证", "其他"):
        for n in r.doc_numbers.get("发票", []):
            if n not in ev.invoice_numbers:
                ev.invoice_numbers.append(n)
    for n in r.doc_numbers.get("合同", []):
        if n not in ev.contract_numbers:
            ev.contract_numbers.append(n)
    for n in r.doc_numbers.get("付款单", []):
        if n not in ev.payment_numbers:
            ev.payment_numbers.append(n)
    for amount in r.amounts:
        amount.source = mat.mtype
        ev.amount_by_source.setdefault(mat.mtype, set()).add(round(amount.value, 2))
        if amount.value not in [item.value for item in ev.amounts]:
            ev.amounts.append(amount)
    if r.counterparty and r.counterparty not in ev.counterparties:
        ev.counterparties.append(r.counterparty)
    for company in r.companies:
        if company not in ev.counterparties:
            ev.counterparties.append(company)
    if r.engine:
        ev.engines_used.add(r.engine)
    if r.conflict:
        ev.conflict = True
    if r.note:
        ev.notes.append(f"{mat.filename}:{r.note}")
    if not fr.used_text_layer and r.confidence > 0:
        ev.confidence = min(ev.confidence, r.confidence)


def _replace_deferred_file_result(ev: SampleEvidence, fr: FileResult):
    """用懒加载结果替换普通阶段的空占位，避免证据文件重复。"""
    for index, existing in enumerate(ev.file_results):
        if existing.material.path == fr.material.path:
            ev.file_results[index] = fr
            break
    else:
        ev.file_results.append(fr)
    _apply_file_result(ev, fr)


def extract_sample(sm: SampleMaterials, engine) -> SampleEvidence:
    ev = SampleEvidence(sid=sm.sid)
    ev.contract_numbers = list(sm.filename_contracts)   # 文件名里的合同号优先
    for mat in sm.materials:
        fr = _extract_file(mat, engine)
        ev.file_results.append(fr)
        _apply_file_result(ev, fr)
    return ev


def extract_contract_amounts(
        sm: SampleMaterials, engine, max_pages=15,
        evidence: SampleEvidence = None) -> List[Amount]:
    """懒加载：仅当发票/付款单金额对不上底稿时，才 OCR 合同(扫描件)前若干页找金额。
    合同常是大额总价/进度款表，金额可能在前几页条款里；OCR 整本 44 页太慢，限页。"""
    out = []
    contracts = [mat for mat in sm.materials if mat.mtype == "合同"]
    if evidence is not None and contracts:
        evidence.contract_attempted = True
    for mat in contracts:
        ext = os.path.splitext(mat.path)[1].lower()
        if ext in (".jpg", ".jpeg", ".png"):
            try:
                with open(mat.path, "rb") as f:
                    rr = engine.recognize(f.read())
            except Exception as exc:
                if evidence is not None:
                    evidence.contract_failed = True
                    evidence.notes.append(f"{mat.filename}:合同读取失败:{exc}")
                continue
            if not rr.succeeded:
                if evidence is not None:
                    evidence.contract_failed = True
                    _replace_deferred_file_result(
                        evidence, FileResult(mat, rr, used_text_layer=False))
                continue
            if evidence is not None:
                evidence.contract_read = True
                _replace_deferred_file_result(
                    evidence, FileResult(mat, rr, used_text_layer=False))
            for a in rr.amounts:
                a.source = "合同"
                out.append(a)
            continue
        try:
            doc = fitz.open(mat.path)
        except Exception as exc:
            if evidence is not None:
                evidence.contract_failed = True
                evidence.notes.append(f"{mat.filename}:合同读取失败:{exc}")
            continue
        read_any = False
        try:
            for i, page in enumerate(doc):
                if i >= max_pages:
                    break
                try:
                    text = page.get_text("text") or ""
                    if len(text.strip()) >= config.DEFAULTS["min_text_layer_chars"] and any(c.isdigit() for c in text):
                        rr = parse.parse_all(text, engine="文本层", confidence=1.0)
                    else:
                        rr = engine.recognize(_render_png(page))
                except Exception as exc:
                    if evidence is not None:
                        evidence.contract_failed = True
                        evidence.notes.append(
                            f"{mat.filename}:合同第{i + 1}页读取失败:{exc}")
                    continue
                if not rr.succeeded:
                    if evidence is not None:
                        evidence.contract_failed = True
                        detail = rr.note or "OCR 未成功"
                        evidence.notes.append(
                            f"{mat.filename}:合同第{i + 1}页读取失败:{detail}")
                    continue
                read_any = True
                for a in rr.amounts:
                    a.source = "合同"
                    out.append(a)
                    if evidence is not None:
                        evidence.amount_by_source.setdefault("合同", set()).add(round(a.value, 2))
                        if a.value not in [item.value for item in evidence.amounts]:
                            evidence.amounts.append(a)
        finally:
            doc.close()
        if evidence is not None:
            evidence.contract_read = evidence.contract_read or read_any
            if not read_any and not evidence.contract_failed:
                evidence.contract_failed = True
                evidence.notes.append(f"{mat.filename}:合同无可读页面")
    return out
