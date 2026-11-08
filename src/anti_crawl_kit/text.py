"""通用文本/数值处理工具 —— 采集后处理必备。"""
from __future__ import annotations

import html
import re


def parse_count(text: str) -> int:
    """将 '2399.2万' / '1.0万' / '1.2亿' / '531' 转为整数（无效返回 0）。"""
    text = str(text).strip().replace(",", "")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    if "万" in text:
        num_str = re.sub(r"[^\d.]", "", text.replace("万", ""))
        if num_str and num_str != ".":
            return int(float(num_str) * 10000)
    if "亿" in text:
        num_str = re.sub(r"[^\d.]", "", text.replace("亿", ""))
        if num_str and num_str != ".":
            return int(float(num_str) * 100000000)
    try:
        return int(text)
    except ValueError:
        return 0


def normalize_count(text: str | None) -> int | None:
    """中文计数转整数；空/无效返回 None（与 parse_count 的区别）。"""
    if text is None:
        return None
    text = str(text).strip()
    if not text or text == "-":
        return None
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    cleaned = re.sub(r"[,\s]", "", text)
    try:
        return int(cleaned)
    except ValueError:
        pass
    if "万" in text:
        num_str = re.sub(r"[^\d.]", "", text.replace("万", ""))
        if num_str and num_str != ".":
            return int(float(num_str) * 10000)
    if "亿" in text:
        num_str = re.sub(r"[^\d.]", "", text.replace("亿", ""))
        if num_str and num_str != ".":
            return int(float(num_str) * 100000000)
    return None


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
