# -*- coding: utf-8 -*-
"""
客户材料发现与分类（§5.2）：
- 样本目录名 = 纯数字序号
- 凭证类型按文件名关键词判定（含"合同/发票/付款单"）
- 兼容旧版子目录结构（样本N/发票|合同|凭证/）
- 顺手从文件名抽合同号（如 ..._1_DEMO-CONTRACT_合同.pdf），避免 OCR 多页合同
"""
from __future__ import annotations
import sys, os, re
from dataclasses import dataclass, field
from typing import List, Dict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


@dataclass
class Material:
    path: str
    mtype: str          # 合同 | 发票 | 付款单 | 凭证 | 其他
    filename: str


@dataclass
class SampleMaterials:
    sid: str
    folder: str
    materials: List[Material] = field(default_factory=list)
    filename_contracts: List[str] = field(default_factory=list)

    def by_type(self, t: str) -> List[Material]:
        return [m for m in self.materials if m.mtype == t]


def classify_by_name(name: str) -> str:
    for t, kws in config.TYPE_KEYWORDS.items():
        if any(k in name for k in kws):
            return t
    return "其他"


def _is_material(fn: str) -> bool:
    return os.path.splitext(fn)[1].lower() in config.MATERIAL_EXTS


def discover(input_dir: str) -> Dict[str, SampleMaterials]:
    out: Dict[str, SampleMaterials] = {}
    if not os.path.isdir(input_dir):
        raise ValueError(f"材料目录不存在：{input_dir}")
    for entry in sorted(os.listdir(input_dir)):
        sub = os.path.join(input_dir, entry)
        if not os.path.isdir(sub):
            continue
        # 目录名格式：纯数字（1/8/10）或"数字、说明"（5、合成样本）
        m = re.match(r"(\d{1,4})", entry)
        if not m:
            continue
        sid = str(int(m.group(1)))
        sm = SampleMaterials(sid=sid, folder=sub)
        # 兼容旧版：样本目录下是否有 发票/合同/凭证/付款单 子目录
        typed_subdirs = [d for d in os.listdir(sub)
                         if os.path.isdir(os.path.join(sub, d)) and classify_by_name(d) != "其他"]
        if typed_subdirs:
            for d in typed_subdirs:
                t = classify_by_name(d)
                dpath = os.path.join(sub, d)
                for fn in sorted(os.listdir(dpath)):
                    if _is_material(fn):
                        sm.materials.append(Material(os.path.join(dpath, fn), t, fn))
        # 平铺结构（本样例）：按文件名分类
        for fn in sorted(os.listdir(sub)):
            fpath = os.path.join(sub, fn)
            if os.path.isfile(fpath) and _is_material(fn):
                sm.materials.append(Material(fpath, classify_by_name(fn), fn))
            # 从文件名抽合同号
            for m in config.RE_CONTRACT.finditer(fn):
                if m.group(1) not in sm.filename_contracts:
                    sm.filename_contracts.append(m.group(1))
        out[sid] = sm
    return out
