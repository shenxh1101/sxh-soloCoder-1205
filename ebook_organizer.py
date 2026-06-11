#!/usr/bin/env python3
"""
电子书整理工具 - 扫描 EPUB/PDF，提取元数据，自动分类整理，生成藏书清单
"""

import sys
import re
import json
import shutil
import zipfile
import argparse
import base64
import io
import hashlib
from pathlib import Path
from xml.etree import ElementTree as ET
from datetime import datetime
from typing import Optional, Dict, List, Tuple, Set
from collections import defaultdict

# ── 命名空间 ──────────────────────────────────────────────
NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "opf": "http://www.idpf.org/2007/opf",
}

_RE_CLEAN = re.compile(r"[《》「」『』【】〔〕]")

_ILLEGAL_CHAR_MAP = {
    ':': '：', '/': '／', '\\': '＼', '?': '？', '*': '＊',
    '"': '＂', '<': '＜', '>': '＞', '|': '｜',
}

_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")

_COMMON_CN_SURNAMES = {
    "王", "李", "张", "刘", "陈", "杨", "赵", "黄", "周", "吴",
    "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗",
    "梁", "宋", "郑", "谢", "韩", "唐", "冯", "于", "董", "萧",
    "程", "曹", "袁", "邓", "许", "傅", "沈", "曾", "彭", "吕",
    "苏", "卢", "蒋", "蔡", "贾", "丁", "魏", "薛", "叶", "阎",
    "余", "潘", "杜", "戴", "夏", "钟", "汪", "田", "任", "姜",
    "范", "方", "石", "姚", "谭", "廖", "邹", "熊", "金", "陆",
    "郝", "孔", "白", "崔", "康", "毛", "邱", "秦", "江", "史",
    "顾", "侯", "邵", "孟", "龙", "万", "段", "雷", "钱", "汤",
    "尹", "易", "常", "武", "乔", "贺", "赖", "龚", "文", "鲁迅",
    "莫言", "巴金", "老舍", "茅盾", "曹禺", "冰心", "东野圭吾",
    "村上春树", "加西亚", "马尔克斯", "东野", "司马", "欧阳",
    "慕容", "上官", "诸葛", "令狐", "独孤",
}

_TITLE_INDICATORS = [
    "的", "之", "与", "和", "记", "传", "录", "集", "史", "志",
    "论", "说", "话", "事", "梦", "城", "国", "家", "人", "生",
    "死", "爱", "恨", "情", "仇", "罪", "罚", "战", "争", "和平",
    "世界", "中国", "日本", "美国", "时间", "空间", "宇宙",
    "故事", "小说", "笔记", "日记", "回忆", "自传", "随笔",
    "指南", "手册", "入门", "实战", "编程", "设计", "模式",
    "艺术", "哲学", "历史", "经济", "心理", "社会", "文化",
    "第一卷", "第二卷", "上册", "下册", "全传", "全集",
    "Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "1", "2", "3",
]

_NON_AUTHOR_WORDS = {
    "the", "a", "an", "and", "or", "of", "in", "on", "to", "for",
    "with", "by", "is", "it", "at", "no", "not", "from", "new",
    "old", "big", "red", "one", "two", "all", "end", "top", "web",
    "pdf", "epub", "book", "test", "copy", "draft", "final",
    "中文", "英文", "chs", "eng", "en", "zh", "v1", "v2", "v3",
    "扫描", "高清", "文字", "插图", "完整", "修订", "最新",
    "新增", "书籍", "新增书籍", "未命名", "下载", "default",
}


def sanitize_dirname(name: str, max_len: int = 80) -> str:
    result = name.strip()
    for illegal, safe in _ILLEGAL_CHAR_MAP.items():
        result = result.replace(illegal, safe)
    if len(result) > max_len:
        result = result[:max_len].rstrip()
    return result or "unnamed"


def _get_first_meaningful_char(s: str) -> str:
    cleaned = re.sub(r"[_\-\s\.\,;:!?\'\"\(\)\[\]{}《》「」『』【】〔〕]+", "", s)
    if cleaned:
        ch = cleaned[0]
        if ch.isalpha():
            return ch.upper()
        return ch
    return "X"


def _ns(ns_uri: str) -> str:
    return ns_uri


def safe_tag_text(elem: Optional[ET.Element], tag: str) -> str:
    if elem is None:
        return ""
    for ns_url in (NS["dc"], "http://purl.org/dc/elements/1.1/"):
        el = elem.find(f"{{{ns_url}}}{tag}")
        if el is not None and el.text:
            return _RE_CLEAN.sub("", el.text.strip())
    return ""


# ══════════════════════════════════════════════════════════
#  EPUB / PDF 元数据读取
# ══════════════════════════════════════════════════════════

def read_epub_metadata(filepath: Path) -> Dict[str, str]:
    meta = {"title": "", "author": "", "publisher": "", "date": "", "format": "EPUB"}
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            container_path = None
            if "META-INF/container.xml" in zf.namelist():
                with zf.open("META-INF/container.xml") as f:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    for rootfile in root.iter(f"{{{_ns('urn:oasis:names:tc:opendocument:xmlns:container')}}}rootfile"):
                        container_path = rootfile.get("full-path")
                        break
            if not container_path or container_path not in zf.namelist():
                return meta
            with zf.open(container_path) as f:
                tree = ET.parse(f)
                opf_root = tree.getroot()
            metadata_elem = None
            for ns_url in (NS["opf"], ""):
                tag = f"{{{ns_url}}}metadata" if ns_url else "metadata"
                metadata_elem = opf_root.find(tag)
                if metadata_elem is not None:
                    break
            if metadata_elem is None:
                metadata_elem = opf_root
            meta["title"] = safe_tag_text(metadata_elem, "title")
            meta["author"] = safe_tag_text(metadata_elem, "creator")
            meta["publisher"] = safe_tag_text(metadata_elem, "publisher")
            date_text = safe_tag_text(metadata_elem, "date")
            if date_text:
                for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
                    try:
                        dt = datetime.strptime(date_text[:10], fmt)
                        meta["date"] = dt.strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        continue
                if not meta["date"]:
                    meta["date"] = date_text[:10]
    except (zipfile.BadZipFile, ET.ParseError, KeyError):
        pass
    return meta


def read_pdf_metadata(filepath: Path) -> Dict[str, str]:
    meta = {"title": "", "author": "", "publisher": "", "date": "", "format": "PDF"}
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(str(filepath))
        info = reader.metadata
        if info is None:
            return meta
        def _get(key):
            val = info.get(key, "")
            if isinstance(val, str):
                return _RE_CLEAN.sub("", val.strip())
            return str(val) if val else ""
        meta["title"] = _get("/Title")
        meta["author"] = _get("/Author")
        meta["publisher"] = _get("/Publisher")
        creation = _get("/CreationDate")
        if creation:
            creation = creation.replace("D:", "").replace("'", "").replace("Z", "")
            for fmt in ("%Y%m%d%H%M%S", "%Y%m%d"):
                try:
                    dt = datetime.strptime(creation[:14], fmt)
                    meta["date"] = dt.strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
            if not meta["date"]:
                meta["date"] = creation[:8]
    except ImportError:
        pass
    except Exception:
        pass
    return meta


# ══════════════════════════════════════════════════════════
#  文件名猜测元数据（增强版 v3）
# ══════════════════════════════════════════════════════════

GUESS_PATTERNS = [
    # 0: 作者 - 书名 - 出版社 - 年份（4段，最明确）
    re.compile(
        r"^(?P<author>.+?)\s*[-_–—]\s*(?P<title>.+?)\s*[-_–—]\s*(?P<publisher>.+?)\s*[-_–—]\s*(?P<year>\d{4})$"
    ),
    # 1: 作者 - 书名 (年份)
    re.compile(
        r"^(?P<author>.+?)\s*[-_–—]\s*(?P<title>.+?)\s*[\(（]\s*(?P<year>\d{4})\s*[\)）]$"
    ),
    # 2: 书名_by_作者（下划线/连字符 by，优先于普通分隔符，避免被3段式误拆）
    re.compile(
        r"^(?P<title>.+?)[-_]by[-_](?P<author>.+)$", re.IGNORECASE
    ),
    # 3: 书名 by 作者（空格 by）
    re.compile(
        r"^(?P<title>.+?)\s+by\s+(?P<author>.+)$", re.IGNORECASE
    ),
    # 4: 书名-带连字符 - 作者[-出版社]（书名内部含连字符，如 Spider-Man）
    re.compile(
        r"^(?P<title>.+?[-].+)\s*[-_–—]\s*(?P<author>.+?)(?:\s*[-_–—]\s*(?P<publisher>.+?))?$"
    ),
    # 5: 作者 - 书名 - 出版社（3段）
    re.compile(
        r"^(?P<author>.+?)\s*[-_–—]\s*(?P<title>.+?)\s*[-_–—]\s*(?P<publisher>.+?)$"
    ),
    # 6: 书名 - 作者 - 出版社（3段）
    re.compile(
        r"^(?P<title>.+?)\s*[-_–—]\s*(?P<author>.+?)\s*[-_–—]\s*(?P<publisher>.+?)$"
    ),
    # 7: 书名（作者）
    re.compile(
        r"^(?P<title>.+?)\s*[（\(](?P<author>[^）\)]+)[）\)]$"
    ),
    # 8: 书名【作者】
    re.compile(
        r"^(?P<title>.+?)\s*[【\[](?P<author>[^】\]]+)[】\]]$"
    ),
    # 9: [作者] 书名
    re.compile(
        r"^[\[【](?P<author>[^\]】]+)[\]】]\s*(?P<title>.+?)$"
    ),
    # 10: (作者) 书名
    re.compile(
        r"^[\(（](?P<author>[^\)）]+)[\)）]\s*(?P<title>.+?)$"
    ),
    # 11: 作者 - 书名（2段，最后匹配）
    re.compile(
        r"^(?P<author>.+?)\s*[-_–—]\s*(?P<title>.+?)$"
    ),
    # 12: 书名 - 作者（2段，最宽泛）
    re.compile(
        r"^(?P<title>.+?)\s*[-_–—]\s*(?P<author>.+?)$"
    ),
]


def _looks_like_person_name(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    if _CHINESE_RE.search(s):
        for surname in sorted(_COMMON_CN_SURNAMES, key=len, reverse=True):
            if s.startswith(surname) and len(s) <= len(surname) + 3:
                return True
        if len(s) <= 2:
            return False
        if len(s) <= 3 and all(_CHINESE_RE.match(c) for c in s):
            return True
        if len(s) <= 4 and all(_CHINESE_RE.match(c) for c in s):
            if s[:2] in _COMMON_CN_SURNAMES:
                return True
            return False
        return False
    else:
        parts = s.split()
        if 1 <= len(parts) <= 3 and all(p[0].isupper() for p in parts if p):
            return True
    return False


def _looks_like_title(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    if len(s) <= 2:
        return False
    for indicator in _TITLE_INDICATORS:
        if indicator in s:
            return True
    return len(s) >= 6


def _detect_swap(author: str, title: str) -> bool:
    if not author or not title:
        return False
    if '-' in title or '–' in title or '—' in title:
        return False
    an = _looks_like_person_name(author)
    tn = _looks_like_person_name(title)
    at = _looks_like_title(author)
    tt = _looks_like_title(title)
    if tn and not an:
        return True
    if at and not tt:
        return True
    if tn and at and not tt and not an:
        return True
    if len(title) <= 4 and _CHINESE_RE.search(title) and len(author) > 10:
        return True
    return False


def _is_plausible_author(author: str) -> bool:
    """检查猜测的作者名是否合理，过滤明显不是人名的字符串"""
    a = author.strip().lower()
    if not a:
        return True
    if a in _NON_AUTHOR_WORDS:
        return False
    if _CHINESE_RE.search(author):
        if _looks_like_person_name(author):
            return True
        if len(author.strip()) <= 2 and all(_CHINESE_RE.match(c) for c in author.strip()):
            return True
        return False
    else:
        parts = a.split()
        if not parts:
            return False
        if all(p[0].isupper() for p in parts if p):
            return True
        if '_' in author:
            parts_u = author.split('_')
            if len(parts_u) >= 2 and all(p.isalpha() and len(p) >= 2 for p in parts_u):
                return True
        if len(parts) == 1 and a.isalpha() and len(a) >= 2:
            return True
        if len(parts) >= 2:
            return True
        return False
    return True


def _is_plausible_title(title: str) -> bool:
    """检查猜测的书名是否合理"""
    t = title.strip()
    if not t:
        return True
    if len(t) <= 1:
        return False
    if t.lower() in _NON_AUTHOR_WORDS:
        return False
    return True


def guess_from_filename(filename: str) -> Dict[str, str]:
    stem = Path(filename).stem
    stem = re.sub(r"^\d+[\.\s\-_]+", "", stem)
    stem = re.sub(r"\[(zh|en|中文|英文|chs|eng)\]", "", stem, flags=re.IGNORECASE)
    stem = stem.strip()

    result = {"title": "", "author": "", "publisher": "", "date": "",
              "swap_detected": False, "author_implausible": False,
              "title_implausible": False}

    for pattern in GUESS_PATTERNS:
        m = pattern.match(stem)
        if not m:
            continue
        gd = m.groupdict()
        author = (gd.get("author") or "").strip()
        title = (gd.get("title") or "").strip()
        publisher = (gd.get("publisher") or "").strip()
        year = (gd.get("year") or "").strip()
        if not author and not title:
            continue

        if _detect_swap(author, title):
            author, title = title, author
            result["swap_detected"] = True

        result["author"] = author
        result["title"] = title
        result["publisher"] = publisher
        if year:
            result["date"] = f"{year}-01-01"

        if not _is_plausible_author(author):
            result["author_implausible"] = True
        if not _is_plausible_title(title):
            result["title_implausible"] = True
        break

    if not result["title"] and not result["author"]:
        result["title"] = stem.strip()

    return result


# ══════════════════════════════════════════════════════════
#  获取书籍完整元数据
# ══════════════════════════════════════════════════════════

def get_book_meta(filepath: Path) -> Dict[str, str]:
    ext = filepath.suffix.lower()
    error_reading = False

    if ext == ".epub":
        try:
            file_meta = read_epub_metadata(filepath)
        except Exception:
            file_meta = {"title": "", "author": "", "publisher": "", "date": "", "format": "EPUB"}
            error_reading = True
    elif ext == ".pdf":
        try:
            file_meta = read_pdf_metadata(filepath)
        except Exception:
            file_meta = {"title": "", "author": "", "publisher": "", "date": "", "format": "PDF"}
            error_reading = True
    else:
        file_meta = {"title": "", "author": "", "publisher": "", "date": "", "format": ext.upper()}

    guessed = guess_from_filename(filepath.name)

    meta = dict(file_meta)
    meta["guessed"] = {}
    meta["meta_source"] = {}

    for key in ("title", "author", "publisher", "date"):
        file_val = file_meta.get(key, "")
        if file_val:
            meta["guessed"][key] = False
            meta["meta_source"][key] = "embedded"
        elif guessed.get(key, ""):
            implausible = guessed.get(f"{key}_implausible", False)
            meta[key] = guessed.get(key, "")
            meta["guessed"][key] = True
            meta["meta_source"][key] = "filename_implausible" if implausible else "filename"
        else:
            meta["guessed"][key] = False
            meta["meta_source"][key] = "missing"

    meta["swap_detected"] = guessed.get("swap_detected", False) and any(
        meta["guessed"].get(k) for k in ("title", "author"))
    meta["error_reading"] = error_reading
    meta["filepath"] = str(filepath)
    meta["filename"] = filepath.name
    meta["size"] = filepath.stat().st_size
    meta["content_hash"] = compute_content_hash(filepath)

    return meta


# ══════════════════════════════════════════════════════════
#  内容哈希
# ══════════════════════════════════════════════════════════

def compute_content_hash(filepath: Path, max_mb: int = 50) -> str:
    """计算文件内容 SHA256，大文件只读前 50MB"""
    try:
        sha = hashlib.sha256()
        with open(filepath, "rb") as f:
            chunk = f.read(65536)
            total = 0
            while chunk and total < max_mb * 1024 * 1024:
                sha.update(chunk)
                total += len(chunk)
                chunk = f.read(65536)
        return sha.hexdigest()
    except OSError:
        return ""


# ══════════════════════════════════════════════════════════
#  封面缩略图（降低阈值，小尺寸内嵌图也能显示）
# ══════════════════════════════════════════════════════════

def extract_cover_thumbnail(filepath: Path, max_size: int = 8192) -> Optional[str]:
    ext = filepath.suffix.lower()
    if ext == ".epub":
        return _extract_epub_cover(filepath, max_size)
    elif ext == ".pdf":
        return _extract_pdf_cover(filepath, max_size)
    return None


def _extract_epub_cover(filepath: Path, max_size: int) -> Optional[str]:
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            image_files = sorted(
                [n for n in zf.namelist() if Path(n).suffix.lower() in image_exts],
                key=lambda n: (0 if "cover" in n.lower() else 1, n),
            )
            for img_name in image_files[:5]:
                with zf.open(img_name) as f:
                    data = f.read(max_size)
                    if len(data) >= 256:
                        ext = Path(img_name).suffix.lower()
                        mime = {
                            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                            ".png": "image/png", ".gif": "image/gif",
                            ".webp": "image/webp",
                        }.get(ext, "image/jpeg")
                        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception:
        pass
    return None


def _extract_pdf_cover(filepath: Path, max_size: int) -> Optional[str]:
    try:
        from PyPDF2 import PdfReader
        from PIL import Image
        reader = PdfReader(str(filepath))
        if len(reader.pages) == 0:
            return None
        page = reader.pages[0]
        for img_obj in page.images:
            data = img_obj.data
            if len(data) >= 256:
                if len(data) > max_size:
                    with io.BytesIO(data) as buf:
                        im = Image.open(buf)
                        im.thumbnail((120, 180), Image.LANCZOS)
                        out = io.BytesIO()
                        im.save(out, format="JPEG", quality=60)
                        data = out.getvalue()
                        mime = "image/jpeg"
                        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
                else:
                    ext = Path(img_obj.name).suffix.lower() if img_obj.name else ".png"
                    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(ext, "image/png")
                    return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except ImportError:
        pass
    except Exception:
        pass
    return None


def generate_fallback_cover(title: str, author: str, fmt: str) -> str:
    text = (title or author or "?").strip()
    first_char = text[0] if text else "?"
    hash_input = (title or "") + (author or "")
    hue = (hashlib.md5(hash_input.encode()).digest()[0]) % 360
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="80">'
        f'<rect width="60" height="80" fill="hsl({hue},50%,40%)" rx="3"/>'
        f'<text x="30" y="48" text-anchor="middle" fill="white" '
        f'font-size="28" font-family="sans-serif" font-weight="bold">{first_char}</text>'
        f'<text x="30" y="70" text-anchor="middle" fill="rgba(255,255,255,0.7)" '
        f'font-size="8" font-family="sans-serif">{fmt}</text>'
        f'</svg>'
    )
    return f"data:image/svg+xml;base64,{base64.b64encode(svg.encode()).decode()}"


def get_cover_html(book: Dict, extract_covers: bool) -> str:
    if extract_covers:
        filepath = Path(book.get("new_path") or book.get("filepath", ""))
        if filepath.exists():
            cover_data = extract_cover_thumbnail(filepath)
            if cover_data:
                return f'<img class="cover-img" src="{cover_data}" alt="{book.get("title", "")}">'
    title = book.get("title", "") or book.get("author", "") or ""
    fmt = book.get("format", "?")
    author = book.get("author", "")
    fallback = generate_fallback_cover(title, author, fmt)
    return f'<img class="cover-img cover-fallback" src="{fallback}" alt="{title}">'


# ══════════════════════════════════════════════════════════
#  可配置查重策略
# ══════════════════════════════════════════════════════════

DEDUP_STRATEGIES = {
    "filename_size": "按文件名+文件大小",
    "filename": "仅按文件名",
    "content_hash": "按内容哈希（SHA256）",
    "title_author": "按标题+作者组合",
}


def _make_dedup_key(book: Dict, strategy: str) -> str:
    if strategy == "filename_size":
        return f"fn::{book.get('filename', '').lower()}::{book.get('size', 0)}"
    elif strategy == "filename":
        return f"fn::{book.get('filename', '').lower()}"
    elif strategy == "content_hash":
        return f"hash::{book.get('content_hash', '')}"
    elif strategy == "title_author":
        title = (book.get("title") or "").strip().lower()
        author = (book.get("author") or "").strip().lower()
        return f"ta::{title}::{author}"
    return f"fn::{book.get('filename', '').lower()}::{book.get('size', 0)}"


def detect_duplicates(books: List[Dict], strategy: str = "filename_size") -> Tuple[List[List[Dict]], List[Dict]]:
    groups: Dict[str, List[Dict]] = defaultdict(list)
    for book in books:
        key = _make_dedup_key(book, strategy)
        groups[key].append(book)

    duplicates = []
    for key, group in groups.items():
        if len(group) > 1:
            duplicates.append(group)

    unique = []
    seen = set()
    for book in books:
        key = _make_dedup_key(book, strategy)
        if key in seen:
            continue
        unique.append(book)
        seen.add(key)

    for group in duplicates:
        for b in group:
            b["_dup_reason"] = DEDUP_STRATEGIES.get(strategy, strategy)

    return duplicates, unique


# ══════════════════════════════════════════════════════════
#  目标路径 & 文件整理
# ══════════════════════════════════════════════════════════

def get_target_dir(base_dir: Path, book: Dict) -> Path:
    author = book.get("author", "").strip()
    title = book.get("title", "").strip()
    safe_author = sanitize_dirname(author) if author else "未知作者"
    safe_title = sanitize_dirname(title) if title else "未知书名"
    title_key = _get_first_meaningful_char(title) if title else "X"
    author_key = _get_first_meaningful_char(author) if author else "X"
    return base_dir / f"{author_key}_{safe_author}" / f"{title_key}_{safe_title}"


def organize_files(
    books: List[Dict],
    source_base: Path,
    target_base: Path,
    dry_run: bool = False,
    verbose: bool = True,
) -> List[Dict]:
    results = []
    for book in books:
        src = Path(book["filepath"])
        dst_dir = get_target_dir(target_base, book)
        dst = dst_dir / book["filename"]

        counter = 1
        while dst.exists() and dst != src:
            stem = Path(book["filename"]).stem
            suffix = Path(book["filename"]).suffix
            dst = dst_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        if not dry_run:
            dst_dir.mkdir(parents=True, exist_ok=True)
            if src != dst:
                try:
                    shutil.move(str(src), str(dst))
                except OSError as e:
                    print(f"  ⚠ 移动失败: {src.name} → {e}")
                    dst = src

        if verbose:
            try:
                rel_src = src.relative_to(source_base) if source_base in src.parents else src
            except ValueError:
                rel_src = src
            try:
                rel_dst = dst.relative_to(target_base) if target_base in dst.parents else dst
            except ValueError:
                rel_dst = dst
            tag = "[DRY-RUN]" if dry_run else "[MOVE]"
            print(f"  {tag} {rel_src} → {rel_dst}")

        book["new_path"] = str(dst)
        results.append(book)
    return results


def move_duplicates(
    duplicate_groups: List[List[Dict]],
    cleanup_dir: Path,
    dry_run: bool = False,
    verbose: bool = True,
):
    if not duplicate_groups:
        return
    for group in duplicate_groups:
        if len(group) <= 1:
            continue
        for dup in group[1:]:
            src = Path(dup["filepath"])
            if not src.exists():
                dup["filepath"] = dup.get("new_path", str(src))
                src = Path(dup["filepath"])
                if not src.exists():
                    continue
            dst = cleanup_dir / sanitize_dirname(src.name)
            counter = 1
            while dst.exists():
                stem = Path(src.name).stem
                dst = cleanup_dir / f"{sanitize_dirname(stem)}_{counter}{src.suffix}"
                counter += 1
            if not dry_run:
                cleanup_dir.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(str(src), str(dst))
                except OSError as e:
                    print(f"  ⚠ 移动重复文件失败: {src.name} → {e}")
            if verbose:
                tag = "[DRY-RUN]" if dry_run else "[DUP]"
                print(f"  {tag} 重复文件: {src.name} → 待清理/{dst.name}")


# ══════════════════════════════════════════════════════════
#  预览报告
# ══════════════════════════════════════════════════════════

def generate_preview_report(
    books_to_organize: List[Dict],
    duplicate_groups: List[List[Dict]],
    skipped_books: List[Dict],
    target_base: Path,
    dup_count: int,
) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("📋 整理预览报告")
    lines.append("=" * 60)

    if books_to_organize:
        lines.append(f"\n📁 将移动 {len(books_to_organize)} 本书：")
        for b in books_to_organize:
            dst_dir = get_target_dir(target_base, b)
            src_name = b.get("filename", "")
            title = b.get("title") or "?"
            author = b.get("author") or "?"
            implausible = ""
            if b.get("meta_source", {}).get("author") == "filename_implausible":
                implausible = " ⚠ 作者名可能不合理"
            if b.get("meta_source", {}).get("title") == "filename_implausible":
                implausible += " ⚠ 书名可能不合理"
            swap_flag = " 🔄" if b.get("swap_detected") else ""
            lines.append(f"  → {src_name}{swap_flag}{implausible}")
            lines.append(f"    作者: {author} | 书名: {title}")
            lines.append(f"    目标: {dst_dir}")

    if dup_count > 0:
        lines.append(f"\n🗑 将移走 {dup_count} 个重复文件：")
        for group in duplicate_groups:
            kept = group[0]
            for dup in group[1:]:
                reason = dup.get("_dup_reason", "?")
                lines.append(f"  🔄 {dup['filename']} → 待清理 (原因: {reason})")
                lines.append(f"     保留: {kept['filename']}")

    if skipped_books:
        lines.append(f"\n⏭ 将跳过 {len(skipped_books)} 本书：")
        for b in skipped_books:
            lines.append(f"  - {b.get('filename', '')}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
#  变更记录
# ══════════════════════════════════════════════════════════

def save_change_log(
    output_dir: Path,
    organized: List[Dict],
    duplicate_groups: List[List[Dict]],
    skipped: List[Dict],
    dup_count: int,
):
    log_path = output_dir / "changelog.json"
    existing_log = []
    if log_path.exists():
        try:
            existing_log = json.loads(log_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, KeyError):
            pass

    moved = []
    for b in organized:
        moved.append({
            "filename": b.get("filename", ""),
            "from": b.get("filepath", ""),
            "to": b.get("new_path", ""),
            "title": b.get("title", ""),
            "author": b.get("author", ""),
        })

    dups = []
    for group in duplicate_groups:
        for dup in group[1:]:
            dups.append({
                "filename": dup.get("filename", ""),
                "from": dup.get("filepath", ""),
                "reason": dup.get("_dup_reason", ""),
            })

    entry = {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "moved": len(moved),
            "duplicates_removed": dup_count,
            "skipped": len(skipped),
        },
        "moved": moved,
        "duplicates": dups,
        "skipped": [{"filename": s.get("filename", ""), "filepath": s.get("filepath", "")} for s in skipped],
    }

    existing_log.append(entry)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(existing_log, ensure_ascii=False, indent=2), encoding="utf-8")


# ══════════════════════════════════════════════════════════
#  HTML 藏书清单
# ══════════════════════════════════════════════════════════

HTML_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Microsoft YaHei", sans-serif;
    background: #f5f5f5; color: #333; line-height: 1.6;
}
.header {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white; padding: 40px 20px; text-align: center;
}
.header h1 { font-size: 2em; margin-bottom: 8px; }
.header .stats { font-size: 1em; opacity: 0.9; }
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }
table {
    width: 100%; border-collapse: collapse; background: white;
    border-radius: 8px; overflow: hidden; box-shadow: 0 2px 12px rgba(0,0,0,0.08);
}
th {
    background: #667eea; color: white; padding: 14px 12px; text-align: left;
    font-weight: 600; font-size: 0.9em; text-transform: uppercase; letter-spacing: 0.5px;
}
td { padding: 12px; border-bottom: 1px solid #eee; vertical-align: middle; }
tr:hover { background: #f8f9ff; }
.cover-cell { width: 80px; text-align: center; }
.cover-img {
    width: 60px; height: 80px; object-fit: cover; border-radius: 4px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.15);
}
.cover-fallback { opacity: 0.85; }
.title-cell { font-weight: 500; }
.author-cell { color: #667eea; font-weight: 500; }
.badge {
    display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 0.75em; margin: 1px 2px;
}
.badge-guess { background: #fff3cd; color: #856404; }
.badge-embedded { background: #d4edda; color: #155724; }
.badge-swap { background: #f8d7da; color: #721c24; }
.badge-error { background: #f8d7da; color: #721c24; }
.badge-implausible { background: #ffe0e0; color: #c00; }
.footer { text-align: center; padding: 20px; color: #999; font-size: 0.85em; }
"""


def generate_html_catalog(books: List[Dict], output_path: Path, extract_covers: bool = True):
    sorted_books = sorted(books, key=lambda b: (
        _pinyin_sort_key(b.get("author", "")),
        _pinyin_sort_key(b.get("title", "")),
    ))
    rows_html = ""
    author_stats = defaultdict(int)
    for book in sorted_books:
        title = book.get("title") or "未知书名"
        author = book.get("author") or "未知作者"
        publisher = book.get("publisher") or "-"
        date = book.get("date") or "-"
        fmt = book.get("format") or "-"
        size_mb = book.get("size", 0) / (1024 * 1024)
        guessed = book.get("guessed", {})
        author_stats[author] += 1
        cover_html = get_cover_html(book, extract_covers)
        badges = []
        for field, is_guessed in guessed.items():
            if is_guessed:
                sources = book.get("meta_source", {})
                if sources.get(field) == "filename_implausible":
                    badges.append(f'<span class="badge badge-implausible" title="从文件名猜测，可能不合理">{field}</span>')
                else:
                    badges.append(f'<span class="badge badge-guess" title="从文件名猜测">{field}</span>')
        if book.get("swap_detected"):
            badges.append('<span class="badge badge-swap" title="检测到书名/作者可能反置">已交换</span>')
        if book.get("error_reading"):
            badges.append('<span class="badge badge-error" title="读取元数据时出错">读取错误</span>')
        badge_html = " ".join(badges) if badges else ""
        rows_html += f"""
        <tr>
            <td class="cover-cell">{cover_html}</td>
            <td class="title-cell">{title}</td>
            <td class="author-cell">{author}</td>
            <td class="publisher-cell">{publisher}</td>
            <td class="date-cell">{date}</td>
            <td class="format-cell">{fmt}</td>
            <td class="size-cell">{size_mb:.2f} MB</td>
            <td class="guess-cell">{badge_html}</td>
        </tr>"""

    total = len(sorted_books)
    authors_count = len(author_stats)
    total_size = sum(b.get("size", 0) for b in sorted_books) / (1024 * 1024)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>藏书清单</title>
<style>{HTML_CSS}</style>
</head>
<body>
<div class="header">
    <h1>📚 藏书清单</h1>
    <p class="stats">
        共 {total} 本书 · {authors_count} 位作者 · 总大小 {total_size:.2f} MB ·
        生成于 {datetime.now().strftime("%Y-%m-%d %H:%M")}
    </p>
</div>
<div class="container">
    <table>
        <thead><tr>
            <th>封面</th><th>书名</th><th>作者</th><th>出版社</th>
            <th>出版日期</th><th>格式</th><th>大小</th><th>标记</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
    </table>
</div>
<div class="footer"><p>由 Ebook Organizer 自动生成</p></div>
</body>
</html>"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return str(output_path)


def _pinyin_sort_key(s: str) -> str:
    if not s:
        return "zzzzz"
    if _CHINESE_RE.match(s[0]):
        return "zzz" + s
    return s.lower()


# ══════════════════════════════════════════════════════════
#  JSON 导出
# ══════════════════════════════════════════════════════════

def export_json(books: List[Dict], output_path: Path):
    export_data = []
    for book in books:
        export_data.append({
            "title": book.get("title", ""),
            "author": book.get("author", ""),
            "publisher": book.get("publisher", ""),
            "date": book.get("date", ""),
            "format": book.get("format", ""),
            "filename": book.get("filename", ""),
            "size_bytes": book.get("size", 0),
            "content_hash": book.get("content_hash", ""),
            "filepath": book.get("new_path") or book.get("filepath", ""),
            "meta_source": book.get("meta_source", {}),
            "swap_detected": book.get("swap_detected", False),
            "error_reading": book.get("error_reading", False),
        })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(export_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(output_path)


# ══════════════════════════════════════════════════════════
#  整理报告 (HTML + JSON)
# ══════════════════════════════════════════════════════════

def _classify_books(books: List[Dict]):
    embedded, guessed, implausible, swapped, errors = [], [], [], [], []
    for b in books:
        sources = b.get("meta_source", {})
        if any(v == "embedded" for v in sources.values()):
            embedded.append(b)
        if any(v == "filename" for v in sources.values()):
            guessed.append(b)
        if any(v == "filename_implausible" for v in sources.values()):
            implausible.append(b)
        if b.get("swap_detected"):
            swapped.append(b)
        if b.get("error_reading"):
            errors.append(b)
    return embedded, guessed, implausible, swapped, errors


def generate_report_json(
    books: List[Dict],
    duplicate_groups: List[List[Dict]],
    skipped_books: List[Dict],
    dup_count: int,
    output_dir: Path,
    dup_strategy: str = "filename_size",
) -> str:
    embedded, guessed, implausible, swapped, errors = _classify_books(books)
    report = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_books": len(books),
            "embedded_metadata": len(embedded),
            "filename_guessed": len(guessed),
            "implausible_guesses": len(implausible),
            "swap_detected": len(swapped),
            "read_errors": len(errors),
            "duplicates": dup_count,
            "skipped": len(skipped_books),
            "dedup_strategy": DEDUP_STRATEGIES.get(dup_strategy, dup_strategy),
        },
        "categories": {
            "embedded": [_book_summary(b) for b in embedded],
            "filename_guessed": [_book_summary(b) for b in guessed],
            "implausible_guesses": [_book_summary(b) for b in implausible],
            "swap_detected": [_book_summary(b) for b in swapped],
            "read_errors": [_book_summary(b) for b in errors],
            "skipped": [_book_summary(b) for b in skipped_books],
        },
        "duplicates": [
            {
                "reason": g[0].get("_dup_reason", ""),
                "files": [_book_summary(b) for b in g],
            }
            for g in duplicate_groups
        ],
    }
    path = output_dir / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _book_summary(b: Dict) -> Dict:
    return {
        "title": b.get("title", ""),
        "author": b.get("author", ""),
        "filename": b.get("filename", ""),
        "format": b.get("format", ""),
        "meta_source": b.get("meta_source", {}),
        "filepath": b.get("new_path") or b.get("filepath", ""),
    }


def generate_report_html(
    books: List[Dict],
    duplicate_groups: List[List[Dict]],
    skipped_books: List[Dict],
    dup_count: int,
    output_dir: Path,
    dup_strategy: str = "filename_size",
) -> str:
    report_path = output_dir / "report.html"
    embedded, guessed, implausible, swapped, errors = _classify_books(books)

    def _rows(book_list):
        r = ""
        for b in book_list:
            title = b.get("title") or "?"
            author = b.get("author") or "?"
            filename = b.get("filename", "")
            sources = b.get("meta_source", {})
            source_str = ", ".join(f"{k}: {v}" for k, v in sources.items() if v != "missing")
            paths = b.get("new_path") or b.get("filepath", "")
            r += f"""<tr>
                <td>{title}</td><td>{author}</td><td>{filename}</td>
                <td>{source_str}</td><td style="font-size:0.8em;word-break:break-all">{paths}</td>
            </tr>"""
        return r

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>整理报告</title>
<style>{HTML_CSS}
.summary-box {{ display: flex; flex-wrap: wrap; gap: 16px; margin-bottom: 24px; }}
.summary-card {{
    flex: 1; min-width: 170px; background: white; padding: 20px;
    border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); text-align: center;
}}
.summary-card .num {{ font-size: 2em; font-weight: 700; color: #667eea; }}
.summary-card .label {{ color: #888; font-size: 0.9em; margin-top: 4px; }}
.section {{ margin-bottom: 30px; }}
.section h2 {{
    background: #f0f2ff; padding: 10px 16px; border-radius: 6px;
    font-size: 1.1em; margin-bottom: 12px; color: #444;
}}
</style>
</head>
<body>
<div class="header">
    <h1>📊 整理报告</h1>
    <p class="stats">生成于 {datetime.now().strftime("%Y-%m-%d %H:%M")} | 查重策略: {DEDUP_STRATEGIES.get(dup_strategy, dup_strategy)}</p>
</div>
<div class="container">
    <div class="summary-box">
        <div class="summary-card"><div class="num">{len(books)}</div><div class="label">整理书籍</div></div>
        <div class="summary-card"><div class="num">{len(embedded)}</div><div class="label">内嵌元数据</div></div>
        <div class="summary-card"><div class="num">{len(guessed)}</div><div class="label">文件名猜测</div></div>
        <div class="summary-card"><div class="num">{len(implausible)}</div><div class="label">可能不合理</div></div>
        <div class="summary-card"><div class="num">{len(swapped)}</div><div class="label">反置检测</div></div>
        <div class="summary-card"><div class="num">{dup_count}</div><div class="label">重复文件</div></div>
        <div class="summary-card"><div class="num">{len(skipped_books)}</div><div class="label">跳过</div></div>
        <div class="summary-card"><div class="num">{len(errors)}</div><div class="label">读取错误</div></div>
    </div>"""

    sections = [
        ("📖 使用内嵌元数据", embedded),
        ("🔍 从文件名猜测", guessed),
        ("⚠ 猜测可能不合理", implausible),
        ("🔄 书名/作者反置", swapped),
        ("❌ 读取错误", errors),
        ("⏭ 跳过", skipped_books),
    ]
    for title, book_list in sections:
        if not book_list:
            continue
        html += f"""<div class="section">
    <h2>{title} ({len(book_list)})</h2>
    <table>
        <thead><tr><th>书名</th><th>作者</th><th>文件名</th><th>元数据来源</th><th>路径</th></tr></thead>
        <tbody>{_rows(book_list)}</tbody>
    </table>
</div>"""

    if duplicate_groups:
        dup_rows = ""
        for g in duplicate_groups:
            for b in g:
                reason = b.get("_dup_reason", "?")
                dup_rows += f"<tr><td>{b.get('title','?')}</td><td>{b.get('author','?')}</td><td>{b.get('filename','')}</td><td>{reason}</td></tr>"
        html += f"""<div class="section">
    <h2>🗑 重复文件 ({dup_count})</h2>
    <table>
        <thead><tr><th>书名</th><th>作者</th><th>文件名</th><th>判定原因</th></tr></thead>
        <tbody>{dup_rows}</tbody>
    </table>
</div>"""

    html += """</div>
<div class="footer"><p>由 Ebook Organizer 自动生成</p></div>
</body>
</html>"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(html, encoding="utf-8")
    return str(report_path)


# ══════════════════════════════════════════════════════════
#  增量 / 同步
# ══════════════════════════════════════════════════════════

def load_existing_catalog(json_path: Path) -> List[Dict]:
    if not json_path.exists():
        return []
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        for item in data:
            item["_existing"] = True
        return data
    except (json.JSONDecodeError, KeyError):
        return []


def find_existing_books(output_dir: Path) -> List[Dict]:
    existing = []
    exts = {".epub", ".pdf"}
    if not output_dir.exists():
        return existing
    for filepath in output_dir.rglob("*"):
        if filepath.is_file() and filepath.suffix.lower() in exts:
            book = get_book_meta(filepath)
            book["_existing"] = True
            book["new_path"] = str(filepath)
            existing.append(book)
    return existing


def merge_catalogs(existing: List[Dict], new_books: List[Dict]) -> List[Dict]:
    merged = list(existing)
    existing_paths = {b.get("new_path") or b.get("filepath", "") for b in existing}
    existing_files = {(b.get("filename", "").lower(), b.get("size", 0)) for b in existing}
    for book in new_books:
        path = book.get("new_path") or book.get("filepath", "")
        if path in existing_paths:
            continue
        fkey = (book.get("filename", "").lower(), book.get("size", 0))
        if fkey in existing_files:
            continue
        merged.append(book)
    return merged


def sync_library(
    library_dir: Path,
    output_dir: Path,
    cleanup_dir: Path,
    dry_run: bool,
    dup_strategy: str,
    extract_covers: bool,
):
    """同步模式：直接对已整理书库运行，检测新增/删除/改名"""
    print(f"\n📂 正在扫描书库: {library_dir}")
    all_books = find_existing_books(library_dir)
    print(f"  发现 {len(all_books)} 本已整理书籍")

    if not all_books:
        print("❌ 未发现任何 EPUB 或 PDF 文件。")
        return

    # 检测重复
    print(f"\n🔍 正在检测重复文件（策略: {DEDUP_STRATEGIES.get(dup_strategy, dup_strategy)}）...")
    dup_groups, unique_books = detect_duplicates(all_books, strategy=dup_strategy)
    dup_count = sum(len(g) - 1 for g in dup_groups)

    if dup_count > 0:
        print(f"  发现 {dup_count} 个重复文件：")
        for group in dup_groups:
            for dup in group[1:]:
                reason = dup.get("_dup_reason", "?")
                print(f"    🔄 {dup['filename']} (原因: {reason})")
    else:
        print("  ✅ 未发现重复文件。")

    # 重新整理到同一目录（标准化的作者/书名结构）
    print(f"\n📁 正在同步整理 {len(unique_books)} 本唯一书籍...")
    organized = organize_files(unique_books, library_dir, output_dir, dry_run=dry_run)

    # 移动重复文件
    if dup_count > 0:
        print(f"\n🧹 移动重复文件...")
        move_duplicates(dup_groups, cleanup_dir, dry_run=dry_run)

    # 生成输出
    html_path = output_dir / "catalog.html"
    json_path = output_dir / "catalog.json"

    print(f"\n📄 正在生成 HTML 藏书清单...")
    generate_html_catalog(organized, html_path, extract_covers=extract_covers)
    print(f"  ✅ {html_path}")

    print(f"\n📄 正在导出 JSON 清单...")
    export_json(organized, json_path)
    print(f"  ✅ {json_path}")

    print(f"\n📊 正在生成整理报告...")
    generate_report_html(organized, dup_groups, [], dup_count, output_dir, dup_strategy)
    generate_report_json(organized, dup_groups, [], dup_count, output_dir, dup_strategy)
    print(f"  ✅ {output_dir / 'report.html'}")
    print(f"  ✅ {output_dir / 'report.json'}")

    if not dry_run:
        save_change_log(output_dir, organized, dup_groups, [], dup_count)
        print(f"  ✅ 变更记录: {output_dir / 'changelog.json'}")

    # 清理空目录
    if not dry_run:
        _cleanup_empty_dirs(output_dir)

    print(f"\n{'='*60}")
    print("✅ 同步完成！")
    print(f"  书籍总数: {len(organized)}")
    print(f"  重复文件: {dup_count}")
    embedded_count = sum(1 for b in organized if any(v == "embedded" for v in b.get("meta_source", {}).values()))
    guessed_count = sum(1 for b in organized if any(v in ("filename", "filename_implausible") for v in b.get("meta_source", {}).values()))
    swap_count = sum(1 for b in organized if b.get("swap_detected"))
    print(f"  内嵌元数据: {embedded_count} | 文件名猜测: {guessed_count} | 反置检测: {swap_count}")
    print(f"{'='*60}")


def _cleanup_empty_dirs(directory: Path):
    for d in sorted(directory.rglob("*"), key=lambda x: len(str(x)), reverse=True):
        if d.is_dir() and d.name != "待清理":
            try:
                if not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass


# ══════════════════════════════════════════════════════════
#  交互式确认
# ══════════════════════════════════════════════════════════

def confirm_guessed_metadata(books: List[Dict]) -> List[Dict]:
    guessed_books = [b for b in books if any(b.get("guessed", {}).values())]
    if not guessed_books:
        print("\n✅ 所有书籍元数据完整，无需确认。\n")
        return books

    print(f"\n{'='*60}")
    print(f"📋 以下 {len(guessed_books)} 本书的元数据是从文件名猜测的，请确认：")
    print(f"{'='*60}")

    for i, book in enumerate(guessed_books):
        print(f"\n--- [{i+1}/{len(guessed_books)}] ---")
        print(f"  文件: {book['filename']}")
        print(f"  格式: {book.get('format', '-')}")

        if book.get("swap_detected"):
            print(f"  ⚠ 检测到书名/作者可能反置，已自动交换")

        for field in ("title", "author", "publisher"):
            val = book.get(field, "")
            is_guessed = book.get("guessed", {}).get(field, False)
            src = book.get("meta_source", {}).get(field, "")
            flag = " (猜测)" if is_guessed else ""
            impl = " ⚠" if src == "filename_implausible" else ""
            print(f"  {field}: {val}{flag}{impl}")

        if book.get("error_reading"):
            print(f"  ⚠ 读取元数据时出错")

        print(f"\n  按 Enter 确认，或输入 's' 跳过此书，或输入新值修改：")

        for field in ("title", "author", "publisher"):
            is_guessed = book.get("guessed", {}).get(field, False)
            if not is_guessed:
                continue
            current = book.get(field, "")
            try:
                user_input = input(f"    {field} [{current}]: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n跳过确认...")
                for b in books:
                    for k in ("title", "author", "publisher"):
                        b["guessed"][k] = False
                return books
            if user_input.lower() == "s":
                print("    ⏭ 跳过此书")
                break
            elif user_input:
                book[field] = user_input
                book["guessed"][field] = False
                book["meta_source"][field] = "manual"
                if book.get("swap_detected"):
                    book["swap_detected"] = False
                print(f"    ✅ {field} 已更新为: {user_input}")
            elif user_input == "":
                book["guessed"][field] = False
                print(f"    ✅ {field} 已确认: {current}")

    print(f"\n{'='*60}")
    print("✅ 确认完成。\n")
    return books


# ══════════════════════════════════════════════════════════
#  扫描
# ══════════════════════════════════════════════════════════

def scan_books(source_dir: Path, exclude_dirs: List[Path] = None) -> List[Dict]:
    books = []
    exts = {".epub", ".pdf"}
    exclude_dirs = [Path(d).resolve() for d in (exclude_dirs or [])]
    for filepath in source_dir.rglob("*"):
        if filepath.is_file() and filepath.suffix.lower() in exts:
            skip = False
            for ed in exclude_dirs:
                try:
                    filepath.resolve().relative_to(ed)
                    skip = True
                    break
                except ValueError:
                    pass
            if not skip:
                books.append(get_book_meta(filepath))
    return books


# ══════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="电子书整理工具 - 扫描 EPUB/PDF，提取元数据，自动分类整理",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ebook_organizer.py ~/Downloads/ebooks --output ~/Library
  python ebook_organizer.py ./books --dry-run                   # 仅预览
  python ebook_organizer.py ./books --no-confirm --auto-confirm  # 跳过交互，直接执行
  python ebook_organizer.py ./Library --sync                    # 同步模式：直接整理已整理书库
  python ebook_organizer.py ./books --dedup content_hash        # 按内容哈希查重
  python ebook_organizer.py ./Library --incremental             # 增量模式
""",
    )

    parser.add_argument("source", type=str, help="电子书源文件夹路径")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出/整理后的目标文件夹")
    parser.add_argument("--cleanup", type=str, default=None,
                        help="重复文件待清理文件夹")
    parser.add_argument("--html", type=str, default=None,
                        help="HTML 藏书清单输出路径")
    parser.add_argument("--json", type=str, default=None,
                        help="JSON 清单输出路径")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅预览，不实际移动文件")
    parser.add_argument("--no-confirm", action="store_true",
                        help="跳过元数据猜测的交互式确认")
    parser.add_argument("--auto-confirm", action="store_true",
                        help="预览后自动确认执行（不询问）")
    parser.add_argument("--no-covers", action="store_true",
                        help="不为 HTML 清单提取封面缩略图")
    parser.add_argument("--incremental", action="store_true",
                        help="增量模式：合并已有书库，只处理新增书籍")
    parser.add_argument("--sync", action="store_true",
                        help="同步模式：直接对已整理书库运行，检测新增/删除/改名")
    parser.add_argument("--dedup", type=str, default="filename_size",
                        choices=list(DEDUP_STRATEGIES.keys()),
                        help="查重策略 (默认: filename_size)")

    args = parser.parse_args()

    source_dir = Path(args.source).resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        print(f"❌ 错误: 源文件夹不存在: {source_dir}")
        sys.exit(1)

    output_dir = Path(args.output).resolve() if args.output else (source_dir if args.sync else source_dir / "已整理")
    cleanup_dir = Path(args.cleanup).resolve() if args.cleanup else (output_dir / "待清理" if not args.sync else source_dir / "待清理")
    html_path = Path(args.html).resolve() if args.html else output_dir / "catalog.html"
    json_path = Path(args.json).resolve() if args.json else output_dir / "catalog.json"

    # ── 同步模式 ──
    if args.sync:
        print("=" * 60)
        print("📚 电子书整理工具 - 同步模式")
        print("=" * 60)
        print(f"  书库目录:   {source_dir}")
        print(f"  查重策略:   {DEDUP_STRATEGIES.get(args.dedup, args.dedup)}")
        if args.dry_run:
            print(f"  🔍 模式: 仅预览 (DRY-RUN)")
        sync_library(source_dir, output_dir, cleanup_dir, args.dry_run,
                     args.dedup, not args.no_covers)
        return

    print("=" * 60)
    print("📚 电子书整理工具")
    print("=" * 60)
    print(f"  源文件夹:   {source_dir}")
    print(f"  目标文件夹: {output_dir}")
    print(f"  待清理文件夹: {cleanup_dir}")
    print(f"  查重策略:   {DEDUP_STRATEGIES.get(args.dedup, args.dedup)}")
    if args.dry_run:
        print(f"  🔍 模式: 仅预览 (DRY-RUN)")
    if args.incremental:
        print(f"  🔄 模式: 增量扫描")

    # ── 增量模式：加载已有书库 ──
    existing_books = []
    if args.incremental:
        print(f"\n📂 正在加载已有书库...")
        existing_books = find_existing_books(output_dir)
        existing_from_json = load_existing_catalog(json_path)
        if existing_from_json and not existing_books:
            existing_books = existing_from_json
        print(f"  已有 {len(existing_books)} 本已整理书籍")

    # ── Step 1: 扫描 ──
    print(f"\n🔍 正在扫描电子书...")
    books = scan_books(source_dir, exclude_dirs=[output_dir, cleanup_dir])
    print(f"  发现 {len(books)} 本电子书 ({sum(1 for b in books if b['format'] == 'EPUB')} EPUB, "
          f"{sum(1 for b in books if b['format'] == 'PDF')} PDF)")

    # ── 增量模式：过滤已有书籍 ──
    skipped_books = []
    if args.incremental and existing_books:
        existing_files = {(b.get("filename", "").lower(), b.get("size", 0)) for b in existing_books}
        new_books = []
        for b in books:
            if (b.get("filename", "").lower(), b.get("size", 0)) in existing_files:
                skipped_books.append(b)
            else:
                new_books.append(b)
        books = new_books
        print(f"  其中 {len(new_books)} 本为新书，{len(skipped_books)} 本已存在")

    if not books and not args.incremental:
        print("❌ 未发现任何 EPUB 或 PDF 文件。")
        return
    if not books and args.incremental:
        print("✅ 没有新书需要整理。")
        if existing_books:
            print(f"\n📄 正在更新清单...")
            generate_html_catalog(existing_books, html_path, extract_covers=not args.no_covers)
            export_json(existing_books, json_path)
            generate_report_html(existing_books, [], [], 0, output_dir, args.dedup)
            generate_report_json(existing_books, [], [], 0, output_dir, args.dedup)
            print(f"  ✅ 清单已更新（基于 {len(existing_books)} 本已有书籍）")
        return

    # ── Step 2: 确认猜测的元数据 ──
    if not args.no_confirm:
        books = confirm_guessed_metadata(books)

    # ── Step 3: 去重检测 ──
    print(f"\n🔍 正在检测重复文件（策略: {DEDUP_STRATEGIES.get(args.dedup, args.dedup)}）...")
    duplicate_groups, unique_books = detect_duplicates(books, strategy=args.dedup)
    dup_count = sum(len(g) - 1 for g in duplicate_groups)
    if dup_count > 0:
        print(f"  发现 {dup_count} 个重复文件（{len(duplicate_groups)} 组）：")
        for group in duplicate_groups:
            kept = group[0]
            for dup in group[1:]:
                reason = dup.get("_dup_reason", "?")
                print(f"    🔄 {dup['filename']} (原因: {reason})")
                print(f"     保留: {kept['filename']}")
    else:
        print("  ✅ 未发现重复文件。")

    # ── Step 3.5: 整理前处理 ──
    organize_skipped = []
    books_to_organize = []
    for b in unique_books:
        p = Path(b["filepath"])
        try:
            p.resolve().relative_to(output_dir.resolve())
            print(f"  ⏭ 跳过（已在输出目录）: {b['filename']}")
            organize_skipped.append(b)
        except ValueError:
            try:
                p.resolve().relative_to(cleanup_dir.resolve())
                print(f"  ⏭ 跳过（已在待清理目录）: {b['filename']}")
                organize_skipped.append(b)
            except ValueError:
                books_to_organize.append(b)

    # ── Step 3.6: 预览报告 + 确认 ──
    if not args.dry_run and not args.auto_confirm:
        all_skipped_preview = skipped_books + organize_skipped
        if args.incremental:
            all_skipped_preview = skipped_books + organize_skipped
        preview = generate_preview_report(
            books_to_organize, duplicate_groups, all_skipped_preview, output_dir, dup_count
        )
        print("\n" + preview)
        try:
            resp = input("\n⚠ 确认执行整理？(y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n❌ 已取消。")
            return
        if resp != "y":
            print("❌ 已取消。")
            return

    # ── Step 4: 移动重复文件 ──
    if dup_count > 0:
        print(f"\n🧹 移动重复文件到待清理文件夹...")
        move_duplicates(duplicate_groups, cleanup_dir, dry_run=args.dry_run)

    # ── Step 5: 整理唯一文件 ──
    print(f"\n📁 正在整理 {len(books_to_organize)} 本唯一书籍...")
    organized = organize_files(books_to_organize, source_dir, output_dir, dry_run=args.dry_run)

    # ── 增量模式：合并 ──
    all_books = organized
    if args.incremental:
        all_books = merge_catalogs(existing_books, organized)
        all_skipped = skipped_books + organize_skipped
    else:
        all_skipped = organize_skipped

    # ── Step 6: 生成 HTML 清单 ──
    print(f"\n📄 正在生成 HTML 藏书清单...")
    generate_html_catalog(all_books, html_path, extract_covers=not args.no_covers)
    print(f"  ✅ {html_path}")

    # ── Step 7: 导出 JSON ──
    print(f"\n📄 正在导出 JSON 清单...")
    export_json(all_books, json_path)
    print(f"  ✅ {json_path}")

    # ── Step 8: 生成整理报告 ──
    print(f"\n📊 正在生成整理报告...")
    generate_report_html(all_books, duplicate_groups, all_skipped, dup_count, output_dir, args.dedup)
    generate_report_json(all_books, duplicate_groups, all_skipped, dup_count, output_dir, args.dedup)
    print(f"  ✅ {output_dir / 'report.html'}")
    print(f"  ✅ {output_dir / 'report.json'}")

    # ── Step 9: 保存变更记录 ──
    if not args.dry_run:
        save_change_log(output_dir, organized, duplicate_groups, all_skipped, dup_count)
        print(f"\n📝 变更记录: {output_dir / 'changelog.json'}")

    # ── 总结 ──
    print(f"\n{'='*60}")
    print("✅ 整理完成！")
    print(f"  书籍总数: {len(all_books)}")
    print(f"  本次新增: {len(organized)}")
    print(f"  重复文件: {dup_count} (已移至: {cleanup_dir})")
    print(f"  跳过: {len(all_skipped)}")
    embedded_count = sum(1 for b in all_books if any(v == "embedded" for v in b.get("meta_source", {}).values()))
    guessed_count = sum(1 for b in all_books if any(v in ("filename", "filename_implausible") for v in b.get("meta_source", {}).values()))
    swap_count = sum(1 for b in all_books if b.get("swap_detected"))
    print(f"  内嵌元数据: {embedded_count} | 文件名猜测: {guessed_count} | 反置检测: {swap_count}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()