# -*- coding: utf-8 -*-
"""
全局配置：通用概念组列匹配 + 默认参数 + 材料分类 + 单据号正则。
不写死列号/白名单用通用概念组匹配（见 workpaper.py）。
"""
import re, os, json

# ---------------- 默认运行参数 ----------------
DEFAULTS = {
    "sheet": "",
    "sample_id_col": "",
    "sample_amount_col": "",
    "sample_summary_col": "",
    "target_col": "",
    "fill_amount_col": "",
    "diff_col": "",
    "amount_tol": 0.01,
    "rel_tol": 0.0,
    "summary_threshold": 0.6,
    "summary_method": "jieba",
    "soft_summary": True,
    "engine_mode": "hybrid",
    "ocr_backend": "auto",
    "vl_model": "qwen3-vl:8b",
    "ollama_host": "http://localhost:11434",
    "fallback_threshold": 0.7,
    "cross_check": False,
    "overwrite": False,
    "offline": True,
    "min_text_layer_chars": 30,
    "ocr_low_conf": 0.85,
    "counterparty_consistent_ratio": 0.80,
    "render_dpi": 240,
    "det_limit_side": 1600,
    "cross_period_days": 0,         # 0=不检测跨期；>0=底稿日期与发票日期差超过此天数标记跨期
}

# ---------------- 通用概念组列匹配（不写死白名单）----------------
# 每个字段有一组"核心概念"；表头文本含任一概念→候选列
ID_CONCEPTS = ["序号", "编号", "No", "ID", "Item", "样本", "项目序号"]
AMOUNT_CONCEPTS = ["金额", "收入", "成本", "费用", "价格", "合计", "原值", "原价",
                   "净值", "余额", "工资", "薪酬", "奖金", "税金", "税款", "税额",
                   "付款", "应收", "应付", "总价", "折旧", "摊销", "保费"]
SUMMARY_CONCEPTS = ["名称", "摘要", "描述", "项目", "合同", "客户", "资产", "凭证",
                    "单据", "说明", "内容", "供应商", "对方", "工程", "简称",
                    "结算单", "对账单", "科目", "部门", "物料"]
TARGET_CONCEPTS = ["证据", "支持", "描述", "文件", "凭据", "备注", "说明", "核查", "检查",
                   "审计", "测试", "程序", "结论", "结果"]
DIFF_CONCEPTS = ["差异", "差额", "偏差"]
DATE_CONCEPTS = ["日期", "时间", "资本化日期", "单据日期", "转出日期", "入账日期", "发生日期"]

# 子目标列：合同号/发票号/付款单号分别列
SUB_CONTRACT_CONCEPTS = ["合同", "合同号", "合同编号", "协议", "协议编号", "订单号"]
SUB_INVOICE_CONCEPTS = ["发票", "发票号", "发票编号", "发票代码", "开票"]
SUB_PAYMENT_CONCEPTS = ["付款单", "付款单号", "付款编号", "付款凭证", "银行回单", "收据"]
SUB_EVIDENCE_CONCEPTS = ["支持性文件", "关键支持", "文件名称", "文件编号", "证据名称",
                         "证据编号", "凭证号", "凭证编号"]

# 表头行识别：不依赖白名单，数"像列名的短文本格"数量
HEADER_ROW_MIN_LABELS = 3   # 一行至少有 3 个像列名的格 → 候选表头行

# ---------------- 客户材料文件分类（按文件名关键词）----------------
TYPE_KEYWORDS = {
    "合同": ["合同", "协议"],
    "发票": ["发票"],
    "付款单": ["付款单", "付款", "请款"],
    "凭证": ["凭证", "记账"],
}
MATERIAL_EXTS = {".pdf", ".jpg", ".jpeg", ".png"}

# ---------------- 单据号正则（硬校验）----------------
RE_INVOICE_20 = re.compile(r"(?<!\d)(\d{20})(?!\d)")
RE_INVOICE_8 = re.compile(r"(?<!\d)(\d{8})(?!\d)")
RE_INVOICE_CODE = re.compile(r"(?<!\d)(\d{10}|\d{12})(?!\d)")
RE_PAYMENT = re.compile(r"\b([A-Z]{2,6}\d{11,18})\b")
RE_CONTRACT = re.compile(r"(?<![A-Za-z])([A-Z]{2,6}\d{6,8}-\d{1,3})(?!\d)")

DIGIT_CONFUSION = str.maketrans({
    "O": "0", "o": "0", "Q": "0", "D": "0",
    "I": "1", "l": "1", "|": "1",
    "Z": "2", "S": "5", "B": "8", "b": "6", "g": "9",
})
DASH_CHARS = "－—–‐-﹣"

# ---------------- 金额关键词（含税/不含税口径）----------------
AMOUNT_KEYWORDS_TAX_INCL = ["价税合计", "价税总计", "含税合计", "含税金额", "实付金额", "付款金额", "小写"]
AMOUNT_KEYWORDS_TAX_EXCL = ["不含税金额", "金额合计", "金额", "合计金额"]
AMOUNT_KEYWORDS_TAXAMT = ["税额", "税款"]
RE_AMOUNT = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{1,2}|\d{3,})(?![\d])")

# ---------------- 对方名称关键词 ----------------
COUNTERPARTY_LABELS = ["销售方", "名称", "对方名称", "对方单位", "收款单位", "收款方", "甲方", "乙方", "供应商"]
RE_COMPANY = re.compile(r"[一-龥（）()A-Za-z0-9]{2,30}?(?:有限公司|股份有限公司|公司|中心|事务所|工作室|厂|店|合伙企业)")

# 为向后兼容保留（被 workpaper 的 _concept_match 替代）
HEADER_ROW_KEYWORDS = [
    "样本序号", "样本类型", "资产名称", "新增资产金额",
    "在建工程转出金额", "支持性文件", "差异", "资产编号",
]

SAFETY_BANNER = (
    "============================================================\n"
    " TOD 核对填充插件 · 本地核对；首次准备模型可能联网下载，材料请离线处理\n"
    " 识别引擎：PaddleOCR（本地）\n"
    "============================================================"
)

# ---------------- 自定义底稿关键词持久化 ----------------
_CUSTOM_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "custom_keywords.json")

def _load_custom_kw():
    if not os.path.exists(_CUSTOM_PATH):
        return [], {}
    try:
        with open(_CUSTOM_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d.get("header_keywords", []), d.get("field_expect", {})
    except Exception:
        return [], {}

def save_custom_keywords(new_hdr: list, new_fields: dict):
    _ch, _cf = _load_custom_kw()
    merged_hdr = list(dict.fromkeys(_ch + new_hdr))
    merged_fields = {}
    for fld in sorted(set(list(_cf.keys()) + list(new_fields.keys()))):
        merged_fields[fld] = list(dict.fromkeys(_cf.get(fld, []) + new_fields.get(fld, [])))
    with open(_CUSTOM_PATH, "w", encoding="utf-8") as f:
        json.dump({"header_keywords": merged_hdr, "field_expect": merged_fields},
                  f, ensure_ascii=False, indent=2)
    import core.workpaper as _wp
    for k, v in merged_fields.items():
        if k in _wp.FIELD_EXPECT:
            for x in v:
                if x not in _wp.FIELD_EXPECT[k]:
                    _wp.FIELD_EXPECT[k].append(x)
        else:
            _wp.FIELD_EXPECT[k] = list(v)

_custom_hdr, _ = _load_custom_kw()
if _custom_hdr:
    for kw in _custom_hdr:
        if kw not in HEADER_ROW_KEYWORDS:
            HEADER_ROW_KEYWORDS.append(kw)
