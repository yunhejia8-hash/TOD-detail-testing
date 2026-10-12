# -*- coding: utf-8 -*-
"""
TOD 核对填充插件 · 主入口（CLI）
读底稿样本 → 识别客户材料（本地 PaddleOCR）→ 金额/摘要三态核对
→ 一致才把 合同号/发票号/付款单号 摘录进底稿目标列；存疑/不一致进核对报告等人工。
本地处理材料；离线使用前须准备好依赖与模型（§9）。

用法见 README.md 或 run_example.ps1。
"""
from __future__ import annotations
import sys, os, argparse, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import config
from engines.service import OCREngineService
from core.workpaper import Workpaper
from core import materials as M
from core import extract as EX
from core import reconcile as RC
from core.parse import parse_date
import report as RP
from datetime import datetime, timedelta


_DEFAULT_OCR_SERVICE = OCREngineService()


def str2bool(v):
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on", "是")


def build_args():
    d = config.DEFAULTS
    p = argparse.ArgumentParser(description="TOD 核对填充插件（本地）")
    p.add_argument("--input-dir", required=True, help="客户材料根目录（含数字命名的样本子目录）")
    p.add_argument("--workpaper", required=True, help="底稿 .xlsx")
    p.add_argument("--sheet", default=d["sheet"], help="工作表名；留空自动识别")
    p.add_argument("--sample-id-col", default=d["sample_id_col"])
    p.add_argument("--sample-amount-col", default=d["sample_amount_col"])
    p.add_argument("--sample-summary-col", default=d["sample_summary_col"])
    p.add_argument("--target-col", default=d["target_col"])
    p.add_argument("--amount-tol", type=float, default=d["amount_tol"])
    p.add_argument("--rel-tol", type=float, default=d["rel_tol"])
    p.add_argument("--summary-threshold", type=float, default=d["summary_threshold"])
    p.add_argument("--summary-method", default=d["summary_method"], choices=["jieba", "2gram", "char"])
    p.add_argument("--soft-summary", default=str(d["soft_summary"]), help="true/false")
    p.add_argument("--engine-mode", default=d["engine_mode"], choices=["paddle_only", "vl_only", "hybrid"])
    p.add_argument("--ocr-backend", default=d["ocr_backend"], choices=["auto", "paddle", "rapid"],
                   help="OCR后端：auto/paddle=Paddle(准,推荐) | rapid=RapidOCR(快但多发票会读错,仅初筛)")
    p.add_argument("--overwrite", action="store_true", help="覆盖目标列已有内容")
    p.add_argument("--fast", action="store_true", help="用 PP-OCRv5 mobile 轻量模型提速(快2-3×)")
    p.add_argument("--offline", action="store_true", help="断网模式（模型须已预置本地）")
    p.add_argument("--output", default="", help="已填底稿输出路径（默认 底稿目录\\底稿_已填.xlsx）")
    p.add_argument("--report", default="", help="核对报告 xlsx 路径（默认 底稿目录\\核对报告.xlsx）")
    p.add_argument("--dpi", type=int, default=0, help="扫描渲染DPI，覆盖默认240；无GPU机器可调低(如180)提速")
    p.add_argument("--skip-contract", action="store_true", help="金额对不上也不读合同（更快，但少一道复核）")
    p.add_argument("--open-output", action="store_true", help="完成后自动打开输出所在文件夹")
    return p.parse_args()


def _safe_write(write_fn, path):
    """写文件；若被占用(PermissionError，通常是文件还开在 Excel 里)，
    自动改存带时间戳的副本并提示，避免整轮崩溃。"""
    import time
    try:
        write_fn(path)
        return path
    except PermissionError:
        base, ext = os.path.splitext(path)
        alt = f"{base}_{time.strftime('%H%M%S')}{ext}"
        print(f"  ⚠ {os.path.basename(path)} 被占用(可能在 Excel 中打开)，改存为 {os.path.basename(alt)}")
        write_fn(alt)
        return alt


def format_target(ev: EX.SampleEvidence) -> str:
    """Legacy: 合并所有号码到一段文字（用于'目标列'）。"""
    parts = []
    if ev.contract_numbers:
        parts.append("合同：" + "、".join(ev.contract_numbers))
    if ev.invoice_numbers:
        parts.append("发票：" + "、".join(ev.invoice_numbers))
    if ev.payment_numbers:
        parts.append("付款单：" + "、".join(ev.payment_numbers))
    return "\n".join(parts)


def format_target_dict(ev: EX.SampleEvidence) -> dict:
    """拆分输出：每个证据类型一个子键，用于分别填入合同/发票/付款单列。"""
    d = {"target": format_target(ev)}
    if ev.contract_numbers:
        d["contract_col"] = "、".join(ev.contract_numbers)
    if ev.invoice_numbers:
        d["invoice_col"] = "、".join(ev.invoice_numbers)
    if ev.payment_numbers:
        d["payment_col"] = "、".join(ev.payment_numbers)
    return d


def build_evidence_trace(ev: EX.SampleEvidence) -> str:
    """构建证据溯源：文件名，标注页码。"""
    lines = []
    for i, fr in enumerate(ev.file_results, 1):
        mat = fr.material
        r = fr.result
        # 统计页数
        page_count = getattr(r, 'page_count', 1) or 1
        page_info = f"第{page_count}页" if page_count > 1 else ""
        lines.append(f"{i}. {mat.filename} {page_info}".strip())
    return "\n".join(lines)


def make_opts(**over):
    """从 config.DEFAULTS 出发构造一份运行参数（供 GUI 调用 run() 用）。
    只需传 input_dir / workpaper 等覆盖项，其余取默认值。"""
    import argparse as _ap
    d = config.DEFAULTS
    ns = _ap.Namespace(
        input_dir="", workpaper="", sheet=d["sheet"],
        sample_id_col=d["sample_id_col"], sample_amount_col=d["sample_amount_col"],
        sample_summary_col=d["sample_summary_col"], target_col=d["target_col"],
        amount_tol=d["amount_tol"], rel_tol=d["rel_tol"],
        summary_threshold=d["summary_threshold"], summary_method=d["summary_method"],
        soft_summary=d["soft_summary"],
        ocr_backend=d["ocr_backend"],
        overwrite=d["overwrite"], fast=False, offline=False, output="", report="",
        dpi=0, skip_contract=False, open_output=False,
        cross_period_days=d["cross_period_days"])
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def run(a, log=print, engine_service=None):
    """核对全流程（CLI 与 GUI 共用）。log 为日志输出回调（GUI 注入到文本框）。
    返回结果字典：{ok, warn, bad, total, out_xlsx, rep_xlsx, rep_md, status, engine_available, out_dir}。"""
    run_started = time.perf_counter()
    log(config.SAFETY_BANNER)
    if getattr(a, "dpi", 0):                       # GUI/CLI 覆盖渲染DPI（无GPU机器可调低提速）
        config.DEFAULTS["render_dpi"] = int(a.dpi)
        log(f" [dpi] 渲染DPI={a.dpi}")
    if a.offline:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        log(" [offline] 已设离线标志；模型权重须已预置本地。")

    # 引擎：服务只保存兼容配置的模型实例，不保存任何任务数据。
    service = engine_service or _DEFAULT_OCR_SERVICE
    acquire_started = time.perf_counter()
    engine, created = service.acquire(
        fast=a.fast, ocr_backend=getattr(a, "ocr_backend", "auto"))
    log(f" [ocr-lifecycle] {'新建' if created else '复用'}应用级引擎，"
        f"获取耗时 {time.perf_counter() - acquire_started:.4f}s")
    estatus = engine.status()
    log("\n引擎：" + estatus)
    if not engine.available:
        log("⚠ 当前无可用 OCR 引擎；扫描件将无法识别，相关样本标『待人工』。仍会产出报告与列定位。")

    # 底稿定位
    wp = Workpaper(a.workpaper, sheet=a.sheet, expect_overrides={
        "id": a.sample_id_col, "amount": a.sample_amount_col,
        "summary": a.sample_summary_col, "target": a.target_col})
    layout = wp.analyze()
    log(f"\n底稿：{layout.sheet}  表头行 {layout.header_row}")
    for f, ch in layout.columns.items():
        log(f"  {f:8}->{ch.col_letter} 『{ch.actual_header}』 {ch.score:.2f}"
            + ("（模糊命中，请核对）" if ch.fuzzy else ""))
    for w in layout.warnings:
        log("  ⚠ " + str(w))

    # 材料
    mats = M.discover(a.input_dir)
    log(f"\n发现样本材料目录：{sorted(mats.keys(), key=lambda x:int(x))}")

    rows, fills = [], {}
    soft = str2bool(a.soft_summary)
    skip_contract = getattr(a, "skip_contract", False)

    # Phase 1：先抽取所有样本证据
    evs = {}
    for s in layout.samples:
        sm = mats.get(s.sid)
        if sm and sm.materials:
            log(f"\n— 样本 {s.sid}：识别 {len(sm.materials)} 个材料 …")
            evs[s.sid] = EX.extract_sample(sm, engine)

    # 跨样本去重：同一"发票号"在 ≥2 个样本出现 → 多半是买方税号/账号被误识，全部剔除
    from collections import Counter
    cnt = Counter()
    for ev in evs.values():
        for n in set(ev.invoice_numbers):
            cnt[n] += 1
    shared = {n for n, c in cnt.items() if c >= 2}
    if shared:
        log(f"\n[跨样本去重] 剔除疑似买方税号/账号(在多个样本重复的发票号)：{sorted(shared)}")
        for ev in evs.values():
            ev.invoice_numbers = [n for n in ev.invoice_numbers if n not in shared]

    # Phase 2：逐样本核对判定
    for s in layout.samples:
        ev = evs.get(s.sid)
        if ev is None:
            rows.append(_row(s, EX.SampleEvidence(sid=s.sid),
                             RC.AmountResult(), RC.SummaryResult(),
                             RC.Verdict("❌不一致", "未找到该样本的材料目录—待人工")))
            continue
        inv_nets = RC.invoice_net_amounts(ev.file_results)
        amt = RC.reconcile_amount(s.amount, ev.amounts, a.amount_tol, a.rel_tol,
                                  by_source=ev.amount_by_source, invoice_nets=inv_nets)
        # 懒加载合同：发票/付款单金额对不上，且有合同 → OCR 合同复核(扫描件，较慢)
        if not amt.matched and not skip_contract:
            sm = mats.get(s.sid)
            if sm and any(m.mtype == "合同" for m in sm.materials):
                log(f"   样本{s.sid} 发票/付款单金额未对上，读合同复核…")
                EX.extract_contract_amounts(sm, engine, evidence=ev)
                amt = RC.reconcile_amount(s.amount, ev.amounts, a.amount_tol, a.rel_tol,
                                          by_source=ev.amount_by_source, invoice_nets=inv_nets)
        summ = RC.reconcile_summary(s.summary, ev.counterparties,
                                    a.summary_threshold, a.summary_method)
        per_file_names = [fr.result.counterparty for fr in ev.file_results]
        cpc = RC.counterparty_consistent(per_file_names)
        low_conf = ev.confidence < config.DEFAULTS["ocr_low_conf"]
        verdict = RC.judge(
            amt, summ, soft_summary=soft, cp_consistent=cpc,
            threshold=a.summary_threshold, conflict=ev.conflict,
            has_key_number=bool(ev.invoice_numbers or ev.payment_numbers),
            low_conf=low_conf)
        log(f"   金额:{'命中' if amt.matched else '未命中'}({amt.kind}) "
            f"摘要:{summ.score:.2f} → {verdict.state}")

        # 跨期检测：取与底稿日期最近的OCR日期比较
        cross_days = getattr(a, "cross_period_days", 0) or 0
        cross_period = ""
        if cross_days > 0 and s.sample_date:
            from datetime import datetime
            try:
                wp_dt = datetime.strptime(s.sample_date, "%Y-%m-%d")
            except ValueError:
                wp_dt = None
            if wp_dt:
                all_ocr_dates = set()
                for fr in ev.file_results:
                    for d in fr.result.dates:
                        try:
                            all_ocr_dates.add(datetime.strptime(d, "%Y-%m-%d"))
                        except ValueError:
                            pass
                # 取最接近底稿日期的OCR日期
                closest_dt, closest_diff = None, 999999
                for ocr_dt in all_ocr_dates:
                    diff = abs((ocr_dt - wp_dt).days)
                    if diff < closest_diff:
                        closest_dt, closest_diff = ocr_dt, diff
                if closest_dt is not None:
                    if closest_diff > cross_days:
                        cross_period = f"是(材料{closest_dt.strftime('%Y-%m-%d')} vs 底稿{s.sample_date}, 差{closest_diff}天>阈值{cross_days}天)"
                    else:
                        cross_period = f"否(差{closest_diff}天)"

        rows.append(_row(s, ev, amt, summ, verdict, cross_period))
        if verdict.state.startswith("✅"):
            fills[s.sid] = format_target_dict(ev)
            fills[s.sid].update({"fill_amount": amt.matched_value, "diff": amt.delta})

    # 输出已填底稿（文件被占用[在Excel中打开]时自动改存带时间戳的副本，不崩溃）
    wpdir = os.path.dirname(os.path.abspath(a.workpaper))
    out_xlsx = _safe_write(lambda p: wp.write_filled(layout, fills, p, overwrite=a.overwrite),
                           a.output or os.path.join(wpdir, "底稿_已填.xlsx"))
    rep_xlsx = _safe_write(lambda p: RP.write_xlsx(p, layout, rows, estatus),
                           a.report or os.path.join(wpdir, "核对报告.xlsx"))
    rep_md = _safe_write(lambda p: RP.write_md(p, layout, rows, estatus, config.SAFETY_BANNER),
                         os.path.splitext(a.report or os.path.join(wpdir, "核对报告.xlsx"))[0] + ".md")

    n_ok = sum(1 for r in rows if r["state"].startswith("✅"))
    n_warn = sum(1 for r in rows if r["state"].startswith("⚠"))
    n_bad = sum(1 for r in rows if r["state"].startswith("❌"))
    log(f"\n完成：✅{n_ok} ⚠️{n_warn} ❌{n_bad} / 共{len(rows)} 样本")
    log(f"  已填底稿：{out_xlsx}")
    log(f"  核对报告：{rep_xlsx}")
    log(f"  报告速览：{rep_md}")
    log("  仅 ✅ 一致样本自动填列；⚠️/❌ 留空待人工，请先看报告再使用已填底稿。")
    log(f"  [perf] 本任务总耗时 {time.perf_counter() - run_started:.3f}s")
    if getattr(a, "open_output", False):
        try:
            os.startfile(wpdir)            # 仅 Windows
        except Exception:
            pass
    return {"ok": n_ok, "warn": n_warn, "bad": n_bad, "total": len(rows),
            "out_xlsx": out_xlsx, "rep_xlsx": rep_xlsx, "rep_md": rep_md,
            "status": estatus, "engine_available": engine.available, "out_dir": wpdir,
            "_column_hits": {f: {"actual_header": c.actual_header,
                                 "col_letter": c.col_letter}
                            for f, c in layout.columns.items()}}


def main():
    run(build_args(), log=print)


def _clean_amounts(vals, top=6):
    """报告"发票金额/付款单金额"列显示用：剔除噪声，只留有意义的金额。
    ① 去掉 <1 元的(税率 0.06/0.09/0.13 等被当成金额的)；
    ② 去掉相对零碎的(< 最大额的 0.5%，如单价/数量/页码)；
    一张增票的 含税/不含税/税额 都会保留。仅影响显示，不动核对逻辑。"""
    vals = sorted({round(v, 2) for v in vals if v >= 1.0}, reverse=True)
    if not vals:
        return ""
    floor = 0.005 * vals[0]
    keep = [v for v in vals if v >= floor]
    return "、".join(f"{v:.2f}" for v in keep[:top])


def _row(s, ev, amt: "RC.AmountResult", summ: "RC.SummaryResult", verdict: "RC.Verdict",
         cross_period: str = ""):
    reason = verdict.reason
    if ev.notes:
        reason += "；" + "；".join(ev.notes)
    if cross_period.startswith("是"):
        reason += "；⚠跨期:" + cross_period
    if ev.contract_read and ev.contract_failed:
        contract_status = "合同部分读取失败"
    elif ev.contract_read:
        contract_status = "合同已读"
    elif ev.contract_attempted and ev.contract_failed:
        contract_status = "合同读取失败"
    else:
        contract_status = ""
    if ev.contract_read:
        unmatched_contract_status = (
            "合同部分读取失败且仍未对上" if ev.contract_failed else "合同已读仍未对上")
    else:
        unmatched_contract_status = contract_status
    return {
        "sid": s.sid,
        "amount": s.amount,
        "summary": s.summary,
        "hit_amount": (f"{amt.matched_value:.2f}（{amt.kind}）" if amt.matched else amt.kind),
        "delta": amt.delta if amt.delta is not None else "",
        "amt_consistency": ((amt.consistency + ("·" + contract_status if contract_status else ""))
                            if amt.matched else
                            unmatched_contract_status),
        "inv_amt": _clean_amounts(ev.amount_by_source.get("发票", [])),
        "pay_amt": _clean_amounts(ev.amount_by_source.get("付款单", [])),
        "sim": f"{summ.score:.2f}",
        "contract": "、".join(ev.contract_numbers),
        "invoice": "、".join(ev.invoice_numbers),
        "payment": "、".join(ev.payment_numbers),
        "cross_period": cross_period,
        "evidence_trace": build_evidence_trace(ev),
        "ocr_conf": f"{ev.confidence:.2f}",
        "source_engine": "、".join(sorted(ev.engines_used)) if ev.engines_used else "—",
        "state": verdict.state,
        "reason": reason,
        "amount_flag": verdict.amount_flag_summary_fail,
    }


if __name__ == "__main__":
    main()
