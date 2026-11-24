"""通用文本/数值处理工具 —— 采集后处理必备。"""
from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation

# 中文计数单位；复合单位按出现顺序连乘，如 "万亿" = 10_000 × 100_000_000。
_COUNT_UNITS: dict[str, int] = {
    "十": 10,
    "百": 100,
    "千": 1_000,
    "万": 10_000,
    "亿": 100_000_000,
}
# 单位组允许为空：finditer 对每个数字段只尝试一次匹配，避免"长数字 + 单位"
# 场景下从每个起点回溯造成的 O(n²)。空单位的匹配在解析时跳过。
_COUNT_SEGMENT_RE = re.compile(r"(\d+(?:\.\d+)?|\.\d+)([十百千万亿]*)")

# 安全上限：计数字段来自不可信页面文本，超限直接判无效，防止回溯与大整数膨胀。
_MAX_COUNT_TEXT_LEN = 1000
_MAX_UNIT_RUN_LEN = 8


def _parse_unit_count(text: str) -> int | None:
    """解析含中文单位的计数（'2亿300万' / '1万亿' / '10万5千'）。

    只解析第一段连续计数：按"数字 + 连续单位"分段，单位从前往后连乘后
    各段累加，如 "2亿300万" = 2 × 1e8 + 300 × 1e4 = 203_000_000。
    第一个带单位的段之前允许任意文本；后续段与上一个有效段之间只允许
    "零"和空白，遇到其他字符即停止（"销量2亿，营收3万" → 200_000_000，
    "5万～10万" → 50_000）。小数用 Decimal 精确计算，避免浮点截断
    （"1.13万" = 11_300）。

    无效输入返回 None，由调用方回退为 0 / None：输入超过 1000 字符、
    未找到任何有效"数字+单位"段，或单位串超过 8 个（该段跳过，不抛异常）。
    """
    if len(text) > _MAX_COUNT_TEXT_LEN:
        return None
    if not any(unit in text for unit in _COUNT_UNITS):
        return None
    total = 0
    matched = False
    last_end = 0
    for match in _COUNT_SEGMENT_RE.finditer(text):
        units = match.group(2)
        # 空单位（纯数字）不参与；超长单位串按无效段跳过。
        if not units or len(units) > _MAX_UNIT_RUN_LEN:
            continue
        if matched:
            gap = text[last_end : match.start()]
            if any(ch != "零" and not ch.isspace() for ch in gap):
                break
        multiplier = 1
        for unit in units:
            multiplier *= _COUNT_UNITS[unit]
        try:
            total += int(Decimal(match.group(1)) * multiplier)
        except InvalidOperation:
            continue
        matched = True
        last_end = match.end()
    return total if matched else None


def parse_count(text: str) -> int:
    """将 '2399.2万' / '1.2亿' / '2亿300万' / '531' 转为整数（无效返回 0）。

    数字与单位之间允许空白（含 NBSP / 全角空格），解析前统一去除；
    只累加第一段连续计数，段间仅允许"零"与空白。输入超过 1000 字符
    或单位串超过 8 个时判为无效，返回 0。
    """
    text = str(text).strip().replace(",", "")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = re.sub(r"\s+", "", text)
    if len(text) > _MAX_COUNT_TEXT_LEN:
        return 0
    parsed = _parse_unit_count(text)
    if parsed is not None:
        return parsed
    try:
        return int(text)
    except ValueError:
        return 0


def normalize_count(text: str | None) -> int | None:
    """中文计数转整数；空/无效返回 None（与 parse_count 的区别）。

    与 parse_count 共用同一套空白清理与分段规则；输入超过 1000 字符、
    单位串超过 8 个等无效情况返回 None。
    """
    if text is None:
        return None
    text = str(text).strip()
    if not text or text == "-":
        return None
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    cleaned = re.sub(r"[,\s]", "", text)
    if len(cleaned) > _MAX_COUNT_TEXT_LEN:
        return None
    try:
        return int(cleaned)
    except ValueError:
        pass
    return _parse_unit_count(cleaned)


def clean_anti_crawl_watermark(text: str) -> str:
    """清洗反爬注入的水印文本（头条、CSDN 等平台已确认出现）。

    清理内容：
      1. 零宽字符（常被用作追踪标记或内容指纹）
      2. mmMwWLliI0fiflO 系列 token（合成粘贴事件检测水印）
      3. 连续重复的 "word" 文本（5 次及以上，反自动化水印）

    纯文本后处理，不会触发任何反爬升级。
    """
    if not text:
        return text
    text = re.sub(r"[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff]", "", text)
    text = re.sub(r"(?:mmMwWLliI0fiflO(?:&\d+)?)+", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?:word[\s\u200b\u200c\u200d\u200e\u200f\u2060\ufeff]*){5,}",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def clean_html(text: str | None) -> str:
    """剥离 HTML 标签 + 还原实体 + 清洗反爬水印。

    搜索结果常把关键词用 <em> 包裹，直接入库会露出标签。
    """
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", str(text))
    try:
        text = html.unescape(text)
    except Exception:
        pass
    return clean_anti_crawl_watermark(text)


def safe_filename(text: str, *, default_name: str = "", max_len: int = 0) -> str:
    """将任意文本转为安全的文件名（Windows/Linux 通用）。"""
    value = str(text or "").strip() or default_name
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = value.strip(" .")
    if max_len > 0:
        value = value[:max_len].strip(" .")
    return value or default_name


def make_dedupe_key(
    platform: str,
    external_id: str | None = None,
    url: str | None = None,
    keyword: str = "",
    title: str = "",
    author_external_id: str = "",
) -> str | None:
    """生成稳定的去重键。

    优先级：platform:id > platform:规范化URL > platform:keyword:author:title
    """
    platform = platform.strip()
    eid = (external_id or "").strip()
    if platform and eid:
        return f"{platform}:{eid}"

    from urllib.parse import urlsplit, urlunsplit

    n_url: str | None = None
    if url:
        parsed = urlsplit(url.strip())
        if parsed.netloc:
            n_url = urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
            )
    if platform and n_url:
        return f"{platform}:{n_url}"

    kw, ti, ae = keyword.strip(), title.strip(), author_external_id.strip()
    if platform and kw and ti and ae:
        return f"{platform}:{kw}:{ae}:{ti}"
    return None
