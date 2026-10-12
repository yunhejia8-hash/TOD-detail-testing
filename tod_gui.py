# -*- coding: utf-8 -*-
"""
TOD 核对填充插件 · 图形界面（双击即用）
================================================
本地处理材料；离线使用前须准备好依赖与模型。底层调用 tod_reconcile.run()。

双击 “启动TOD核对.vbs”（或桌面快捷方式）即可打开本界面。
开发调试可直接：.venv\\Scripts\\python.exe tod_gui.py
"""
from __future__ import annotations
import os
import sys
import json
import queue
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

import tod_reconcile as TR
from engines.service import OCREngineService

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, "gui_settings.json")
LOGO_PATH = os.path.join(HERE, "logo.jpg")


# ---------------- 配置记忆（记住上次选的路径/参数）----------------
def load_cfg() -> dict:
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(d: dict):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _to_float(s, default):
    try:
        return float(str(s).strip())
    except Exception:
        return default


def _to_int(s, default):
    try:
        return int(float(str(s).strip()))
    except Exception:
        return default


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q: "queue.Queue" = queue.Queue()
        self.running = False
        self.result = None
        self.ocr_service = OCREngineService()
        cfg = load_cfg()

        root.title("TOD 核对填充插件 · 本地版")
        root.geometry("820x760")
        root.minsize(720, 620)

        # 窗口图标
        if os.path.exists(LOGO_PATH):
            try:
                img = Image.open(LOGO_PATH).resize((32, 32), Image.LANCZOS)
                self._icon_img = ImageTk.PhotoImage(img)
                root.iconphoto(True, self._icon_img)
            except Exception:
                pass

        pad = {"padx": 6, "pady": 4}
        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)
        r = 0

        # ---- 标题/安全条 ----
        ttk.Label(main, text="TOD 细节测试 · 核对填充", font=("Microsoft YaHei UI", 13, "bold")).grid(
            row=r, column=0, columnspan=3, sticky="w")
        r += 1
        ttk.Label(main, text="本地核对 · 模型预置后离线使用 · 结果需人工复核",
                  foreground="#2C7A2C").grid(row=r, column=0, columnspan=3, sticky="w", pady=(0, 8))
        r += 1

        # ---- 材料文件夹 ----
        ttk.Label(main, text="材料文件夹").grid(row=r, column=0, sticky="e", **pad)
        self.var_input = tk.StringVar(value=cfg.get("input_dir", ""))
        ttk.Entry(main, textvariable=self.var_input).grid(row=r, column=1, sticky="we", **pad)
        ttk.Button(main, text="浏览…", command=self._pick_input).grid(row=r, column=2, **pad)
        r += 1

        # ---- 底稿 ----
        ttk.Label(main, text="底稿 .xlsx").grid(row=r, column=0, sticky="e", **pad)
        self.var_wp = tk.StringVar(value=cfg.get("workpaper", ""))
        ttk.Entry(main, textvariable=self.var_wp).grid(row=r, column=1, sticky="we", **pad)
        ttk.Button(main, text="浏览…", command=self._pick_wp).grid(row=r, column=2, **pad)
        r += 1

        # ---- 工作表 ----
        ttk.Label(main, text="工作表(--sheet)").grid(row=r, column=0, sticky="e", **pad)
        self.var_sheet = tk.StringVar(value=cfg.get("sheet", ""))
        ttk.Entry(main, textvariable=self.var_sheet).grid(row=r, column=1, sticky="we", **pad)
        ttk.Label(main, text="留空=自动识别", foreground="#888").grid(row=r, column=2, sticky="w", **pad)
        r += 1

        # ---- 参数区（两列网格）----
        params = ttk.LabelFrame(main, text="常用参数", padding=8)
        params.grid(row=r, column=0, columnspan=3, sticky="we", pady=8)
        for c in (1, 3):
            params.columnconfigure(c, weight=1)
        r += 1

        d = TR.config.DEFAULTS
        # 渲染DPI
        ttk.Label(params, text="渲染DPI(--dpi)").grid(row=0, column=2, sticky="e", **pad)
        self.var_dpi = tk.StringVar(value=str(cfg.get("dpi", "") or ""))
        ttk.Entry(params, textvariable=self.var_dpi, width=10).grid(row=0, column=3, sticky="w", **pad)

        # 摘要阈值
        ttk.Label(params, text="摘要阈值").grid(row=1, column=0, sticky="e", **pad)
        self.var_thr = tk.StringVar(value=str(cfg.get("summary_threshold", d["summary_threshold"])))
        ttk.Entry(params, textvariable=self.var_thr, width=10).grid(row=1, column=1, sticky="w", **pad)
        # 摘要方法
        ttk.Label(params, text="摘要算法").grid(row=1, column=2, sticky="e", **pad)
        self.var_method = tk.StringVar(value=cfg.get("summary_method", d["summary_method"]))
        ttk.Combobox(params, textvariable=self.var_method, width=8, state="readonly",
                     values=["jieba", "2gram", "char"]).grid(row=1, column=3, sticky="w", **pad)

        # 绝对容差
        ttk.Label(params, text="金额绝对容差").grid(row=2, column=0, sticky="e", **pad)
        self.var_tol = tk.StringVar(value=str(cfg.get("amount_tol", d["amount_tol"])))
        ttk.Entry(params, textvariable=self.var_tol, width=10).grid(row=2, column=1, sticky="w", **pad)
        # 相对容差
        ttk.Label(params, text="金额相对容差").grid(row=2, column=2, sticky="e", **pad)
        self.var_reltol = tk.StringVar(value=str(cfg.get("rel_tol", d["rel_tol"])))
        ttk.Entry(params, textvariable=self.var_reltol, width=10).grid(row=2, column=3, sticky="w", **pad)

        # 跨期检测
        ttk.Label(params, text="跨期时长(天)").grid(row=3, column=0, sticky="e", **pad)
        self.var_cross = tk.StringVar(value=str(cfg.get("cross_period_days", d["cross_period_days"])))
        ttk.Entry(params, textvariable=self.var_cross, width=10).grid(row=3, column=1, sticky="w", **pad)
        ttk.Label(params, text="0=不检测；>0=底稿与发票日期差超过此值标为跨期",
                  foreground="#888").grid(row=3, column=2, columnspan=2, sticky="w", **pad)

        # OCR 后端（auto=有独显走Paddle-GPU / 无独显走RapidOCR）
        ttk.Label(params, text="OCR后端").grid(row=4, column=0, sticky="e", **pad)
        self.var_backend = tk.StringVar(value=cfg.get("ocr_backend", d["ocr_backend"]))
        ttk.Combobox(params, textvariable=self.var_backend, width=12, state="readonly",
                     values=["auto", "paddle", "rapid"]).grid(row=4, column=1, sticky="w", **pad)
        ttk.Label(params, text="auto/paddle=准确(推荐)；rapid=快但多发票会读错·仅初筛",
                  foreground="#888").grid(row=4, column=2, columnspan=2, sticky="w", **pad)

        # 手动列覆盖（填列字母如G/B/E/M，或填表头文字均可；留空=自动识别）
        ttk.Separator(params, orient="horizontal").grid(row=5, column=0, columnspan=4, sticky="we", pady=2)
        ttk.Label(params, text="手动列覆盖（填列字母如G 或表头文字均可；留空=自动识别）",
                  foreground="#888").grid(row=6, column=0, columnspan=4, sticky="w", **pad)
        ttk.Label(params, text="序号列").grid(row=7, column=0, sticky="e", **pad)
        self.var_id_col = tk.StringVar(value=cfg.get("sample_id_col", ""))
        ttk.Entry(params, textvariable=self.var_id_col, width=6).grid(row=7, column=1, sticky="w", **pad)
        ttk.Label(params, text="金额列").grid(row=7, column=2, sticky="e", **pad)
        self.var_amt_col = tk.StringVar(value=cfg.get("sample_amount_col", ""))
        ttk.Entry(params, textvariable=self.var_amt_col, width=6).grid(row=7, column=3, sticky="w", **pad)
        ttk.Label(params, text="摘要列").grid(row=8, column=0, sticky="e", **pad)
        self.var_sum_col = tk.StringVar(value=cfg.get("sample_summary_col", ""))
        ttk.Entry(params, textvariable=self.var_sum_col, width=6).grid(row=8, column=1, sticky="w", **pad)
        ttk.Label(params, text="目标列").grid(row=8, column=2, sticky="e", **pad)
        self.var_tgt_col = tk.StringVar(value=cfg.get("target_col", ""))
        ttk.Entry(params, textvariable=self.var_tgt_col, width=6).grid(row=8, column=3, sticky="w", **pad)
        ttk.Button(params, text="📤 学习底稿格式", command=self._learn_workpaper).grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(params, text="上传新类型底稿→自动提取关键词→确认保存→立即生效",
                  foreground="#888").grid(row=9, column=2, columnspan=2, sticky="w", pady=(4, 0))

        # ---- 复选项 ----
        checks = ttk.Frame(params)
        checks.grid(row=10, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self.var_overwrite = tk.BooleanVar(value=cfg.get("overwrite", False))
        self.var_fast = tk.BooleanVar(value=cfg.get("fast", False))
        self.var_skip = tk.BooleanVar(value=cfg.get("skip_contract", False))
        self.var_offline = tk.BooleanVar(value=cfg.get("offline", False))
        self.var_open = tk.BooleanVar(value=cfg.get("open_output", True))
        ttk.Checkbutton(checks, text="覆盖底稿已有内容(--overwrite)", variable=self.var_overwrite).grid(row=0, column=0, sticky="w", padx=4)
        ttk.Checkbutton(checks, text="快速模式·准度降(--fast)", variable=self.var_fast).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Checkbutton(checks, text="跳过合同OCR·更快(--skip-contract)", variable=self.var_skip).grid(row=1, column=0, sticky="w", padx=4)
        ttk.Checkbutton(checks, text="断网模式(--offline)", variable=self.var_offline).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Checkbutton(checks, text="完成后打开输出文件夹", variable=self.var_open).grid(row=2, column=0, sticky="w", padx=4)

        # ---- 引擎状态 ----
        self.lbl_status = ttk.Label(main, text="引擎：检测中…（首次会加载 OCR 模型，请稍候）", foreground="#555")
        self.lbl_status.grid(row=r, column=0, columnspan=3, sticky="w", **pad)
        r += 1

        # ---- 按钮行 ----
        btns = ttk.Frame(main)
        btns.grid(row=r, column=0, columnspan=3, sticky="we", pady=4)
        self.btn_run = ttk.Button(btns, text="▶ 开始核对", command=self.start)
        self.btn_run.pack(side="left", padx=4)
        self.btn_open = ttk.Button(btns, text="📂 打开输出文件夹", command=self._open_out, state="disabled")
        self.btn_open.pack(side="left", padx=4)
        self.lbl_summary = ttk.Label(btns, text="", font=("Microsoft YaHei UI", 10, "bold"))
        self.lbl_summary.pack(side="left", padx=12)
        r += 1

        # ---- 进度条 ----
        self.prog = ttk.Progressbar(main, mode="indeterminate")
        self.prog.grid(row=r, column=0, columnspan=3, sticky="we", **pad)
        r += 1

        # ---- 日志框 ----
        main.rowconfigure(r, weight=1)
        logframe = ttk.LabelFrame(main, text="运行日志", padding=4)
        logframe.grid(row=r, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        logframe.rowconfigure(0, weight=1)
        logframe.columnconfigure(0, weight=1)
        self.txt = tk.Text(logframe, height=14, wrap="word", font=("Consolas", 9))
        self.txt.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logframe, command=self.txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.txt.config(yscrollcommand=sb.set)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain)
        threading.Thread(target=self._query_status, daemon=True).start()

    # ---------------- 选择文件 ----------------
    def _pick_input(self):
        p = filedialog.askdirectory(title="选择客户材料根目录（内含数字命名的样本子文件夹）",
                                    initialdir=self.var_input.get() or os.path.expanduser("~"))
        if p:
            self.var_input.set(p)

    def _pick_wp(self):
        p = filedialog.askopenfilename(title="选择底稿 .xlsx", filetypes=[("Excel", "*.xlsx"), ("所有文件", "*.*")],
                                       initialdir=os.path.dirname(self.var_wp.get()) or os.path.expanduser("~"))
        if p:
            self.var_wp.set(p)

    # ---------------- 学习底稿格式 ----------------
    def _learn_workpaper(self):
        """上传新类型底稿，自动提取表头关键词并保存到白名单，下次自动识别。"""
        p = filedialog.askopenfilename(
            title="选择新类型底稿用于学习关键词",
            filetypes=[("Excel", "*.xlsx"), ("所有文件", "*.*")])
        if not p:
            return
        try:
            from core.workpaper import learn_keywords
            new_kw, new_fields = learn_keywords(p)
        except Exception as e:
            messagebox.showerror("学习失败", f"扫描底稿时出错：{e}")
            return

        if not new_kw and not new_fields:
            messagebox.showinfo("学习结果", "未发现新关键词——当前白名单已能识别该底稿，无需添加。")
            return

        lines = ["从该底稿中提取到："]
        if new_kw:
            lines.append(f"\n▸ 新表头关键词 {len(new_kw)} 个：")
            lines.append("、".join(new_kw[:25]))
            if len(new_kw) > 25:
                lines.append(f"…等 {len(new_kw)} 个")
        if new_fields:
            lines.append(f"\n▸ 新字段列名映射：")
            for fld, terms in new_fields.items():
                lines.append(f"  {fld}: {'、'.join(terms[:5])}")

        lines.append(f"\n\n保存后下次遇到同类底稿将自动识别。要保存吗？")

        if not messagebox.askyesno("学习底稿格式", "\n".join(lines)):
            return

        import config as cfg
        cfg.save_custom_keywords(new_kw, new_fields)
        new_total = sum(len(v) for v in new_fields.values())
        messagebox.showinfo("已保存",
            f"已添加 {len(new_kw)} 个关键词、{new_total} 个列名映射。\n立即生效，无需重启。")

    # ---------------- 引擎状态 ----------------
    def _query_status(self):
        try:
            eng, _created = self.ocr_service.acquire(
                ocr_backend=self.var_backend.get(), fast=self.var_fast.get())
            self.q.put(("status", eng.status()))
        except Exception as e:
            self.q.put(("status", "状态查询失败：%s" % e))

    # ---------------- 启动核对 ----------------
    def start(self):
        if self.running:
            return
        inp = self.var_input.get().strip()
        wp = self.var_wp.get().strip()
        if not os.path.isdir(inp):
            messagebox.showwarning("提示", "请选择有效的【材料文件夹】"); return
        if not os.path.isfile(wp):
            messagebox.showwarning("提示", "请选择有效的【底稿 .xlsx】"); return

        cfg = {
            "input_dir": inp, "workpaper": wp, "sheet": self.var_sheet.get().strip(),
            "summary_method": self.var_method.get(),
            "summary_threshold": _to_float(self.var_thr.get(), 0.6),
            "amount_tol": _to_float(self.var_tol.get(), 0.01),
            "rel_tol": _to_float(self.var_reltol.get(), 0.0),
            "dpi": _to_int(self.var_dpi.get(), 0),
            "overwrite": self.var_overwrite.get(), "fast": self.var_fast.get(),
            "skip_contract": self.var_skip.get(), "offline": self.var_offline.get(),
            "open_output": self.var_open.get(), "ocr_backend": self.var_backend.get(),
            "cross_period_days": _to_int(self.var_cross.get(), 0),
            "sample_id_col": self.var_id_col.get().strip(),
            "sample_amount_col": self.var_amt_col.get().strip(),
            "sample_summary_col": self.var_sum_col.get().strip(),
            "target_col": self.var_tgt_col.get().strip(),
        }
        save_cfg(cfg)

        opts = TR.make_opts(
            input_dir=inp, workpaper=wp, sheet=cfg["sheet"],
            summary_method=cfg["summary_method"],
            ocr_backend=cfg["ocr_backend"],
            sample_id_col=cfg["sample_id_col"], sample_amount_col=cfg["sample_amount_col"],
            sample_summary_col=cfg["sample_summary_col"], target_col=cfg["target_col"],
            summary_threshold=cfg["summary_threshold"], amount_tol=cfg["amount_tol"],
            rel_tol=cfg["rel_tol"], dpi=cfg["dpi"], overwrite=cfg["overwrite"],
            fast=cfg["fast"], skip_contract=cfg["skip_contract"], offline=cfg["offline"],
            cross_period_days=cfg["cross_period_days"],
            open_output=False)  # 输出文件夹由界面在完成后自己打开

        self.running = True
        self.result = None
        self.btn_run.config(state="disabled")
        self.btn_open.config(state="disabled")
        self.lbl_summary.config(text="运行中…")
        self.txt.delete("1.0", "end")
        self.prog.start(12)
        threading.Thread(target=self._work, args=(opts, cfg["open_output"]), daemon=True).start()

    def _work(self, opts, open_after):
        try:
            res = TR.run(
                opts,
                log=lambda m: self.q.put(("log", m)),
                engine_service=self.ocr_service,
            )
            res["_open_after"] = open_after
            self.q.put(("done", res))
        except Exception as e:
            self.q.put(("log", "\n[错误] " + str(e)))
            self.q.put(("log", traceback.format_exc()))
            self.q.put(("error", str(e)))

    # ---------------- 主线程消费队列 ----------------
    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.txt.insert("end", str(payload) + "\n")
                    self.txt.see("end")
                elif kind == "status":
                    self.lbl_status.config(text="引擎：" + str(payload))
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "error":
                    self._finish()
                    messagebox.showerror("运行出错", payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _on_done(self, res):
        self._finish()
        self.result = res
        # 自动记忆列名：填了列字母→学实际表头文字；填了文字名→直接记住
        import re as _re
        overrides = {}
        # 从底层 run 结果拿到实际定位的列映射
        _layout_hits = res.get("_column_hits", {})
        for key, var in [("id", self.var_id_col), ("amount", self.var_amt_col),
                         ("summary", self.var_sum_col), ("target", self.var_tgt_col)]:
            v = var.get().strip()
            if not v:
                continue
            if _re.fullmatch(r"[A-Za-z]{1,2}", v):
                # 填了列字母：取实际表头文字来记
                ch = _layout_hits.get(key)
                if ch and ch.get("actual_header", "").startswith("(列"):
                    continue  # 首列自动识别的，不记
                if ch:
                    overrides[key] = [ch.get("actual_header", "").strip()]
            else:
                overrides[key] = [v]
        if overrides and res.get("total", 0) > 0:
            import config as cfg
            cfg.save_custom_keywords([], overrides)
        self.btn_open.config(state="normal")
        self.lbl_summary.config(
            text=f"✅一致 {res['ok']}　⚠️存疑 {res['warn']}　❌不一致 {res['bad']}　/ 共 {res['total']}")
        if res.get("_open_after"):
            self._open_out()
        messagebox.showinfo(
            "核对完成",
            f"✅ 一致 {res['ok']}　⚠️ 存疑 {res['warn']}　❌ 不一致 {res['bad']}　共 {res['total']} 样本\n\n"
            f"已填底稿：{os.path.basename(res['out_xlsx'])}\n"
            f"核对报告：{os.path.basename(res['rep_xlsx'])}\n\n"
            "仅 ✅ 一致样本自动填列；⚠️/❌ 留空待人工，请先看报告再用已填底稿。")

    def _finish(self):
        self.running = False
        self.prog.stop()
        self.btn_run.config(state="normal")

    def _open_out(self):
        out = (self.result or {}).get("out_dir") or os.path.dirname(self.var_wp.get())
        try:
            os.startfile(out)
        except Exception as e:
            messagebox.showwarning("提示", "打不开文件夹：%s" % e)

    def _on_close(self):
        if self.running and not messagebox.askyesno("确认", "核对仍在进行，确定要关闭吗？"):
            return
        self.ocr_service.clear()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")   # Windows 原生外观
    except Exception:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
