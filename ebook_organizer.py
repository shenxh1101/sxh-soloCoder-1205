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
from pathlib import Path
from xml.etree import ElementTree as ET
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from collections import defaultdict

# ── 命名空间 ──────────────────────────────────────────────
NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "opf": "http://www.idpf.org/2007/opf",
    "calibre": "http://calibre.kovidgoyal.net/2009/metadata",
}

# ── 书名号清洗正则 ────────────────────────────────────────
_RE_CLEAN = re.compile(r"[《》「」『』【】〔〕]")

# ── 文件名猜测模式 ───────────────────────────────────────
GUESS_PATTERNS = [
    # 作者 - 书名.扩展名
    re.compile(
        r"^(?P<author>[^\-_]+)\s*[-_]\s*(?P<title>.+?)(?:\s*[-_]\s*(?P<publisher>[^\-_]+?))?$",
        re.IGNORECASE,
    ),
    # 书名 - 作者.扩展名
    re.compile(
        r"^(?P<title>.+?)\s*[-_]\s*(?P<author>[^\-_]+?)(?:\s*[-_]\s*(?P<publisher>[^\-_]+?))?$",
        re.IGNORECASE,
    ),
    # 书名 by 作者
    re.compile(
        r"^(?P<title>.+?)\s+by\s+(?P<author>.+?)$", re.IGNORECASE
    ),
    # [作者] 书名
    re.compile(
        r"^\[(?P<author>[^\]]+)\]\s*(?P<title>.+?)$"
    ),
    # (作者) 书名
    re.compile(
        r"^\((?P<author>[^\)]+)\)\s*(?P<title>.+?)$"
    ),
]

# ── 中文字符检测 ──────────────────────────────────────────
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")


def safe_tag_text(elem: Optional[ET.Element], tag: str) -> str:
    """安全获取 DC 标签文本"""
    if elem is None:
        return ""
    for ns_url in (NS["dc"], "http://purl.org/dc/elements/1.1/"):
        el = elem.find(f"{{{ns_url}}}{tag}")
        if el is not None and el.text:
            return _RE_CLEAN.sub("", el.text.strip())
    return ""


# ══════════════════════════════════════════════════════════
#  EPUB 元数据读取
# ══════════════════════════════════════════════════════════

def read_epub_metadata(filepath: Path) -> Dict[str, str]:
    """从 EPUB 文件读取元数据，返回 dict"""
    meta = {"title": "", "author": "", "publisher": "", "date": "", "format": "EPUB"}

    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            # Step 1: 找到 container.xml → 定位 OPF 文件
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

            # Step 2: 解析 OPF → metadata
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


def _ns(ns_uri: str) -> str:
    return ns_uri


# ══════════════════════════════════════════════════════════
#  PDF 元数据读取
# ══════════════════════════════════════════════════════════

def read_pdf_metadata(filepath: Path) -> Dict[str, str]:
    """从 PDF 文件读取元数据，需要 PyPDF2"""
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
#  文件名猜测元数据
# ══════════════════════════════════════════════════════════

def guess_from_filename(filename: str) -> Dict[str, str]:
    """从文件名猜测 标题/作者/出版社"""
    # 去掉扩展名
    stem = Path(filename).stem
    # 先去掉常见的编号前缀
    stem = re.sub(r"^\d+[\.\s\-_]+", "", stem)
    # 去掉扩展名中的语言标记
    stem = re.sub(r"\[(zh|en|中文|英文|chs|eng)\]", "", stem, flags=re.IGNORECASE)

    result = {"title": "", "author": "", "publisher": ""}

    for pattern in GUESS_PATTERNS:
        m = pattern.match(stem)
        if m:
            author = m.group("author")
            title = m.group("title") if "title" in m.groupdict() else ""
            publisher = m.group("publisher") if "publisher" in m.groupdict() else ""

            result["author"] = author.strip() if author else ""
            result["title"] = title.strip() if title else ""
            result["publisher"] = publisher.strip() if publisher else ""

            if result["title"] or result["author"]:
                break

    # 如果仍然没有匹配到，把整个文件名作为书名
    if not result["title"] and not result["author"]:
        result["title"] = stem.strip()

    return result


# ══════════════════════════════════════════════════════════
#  获取书籍完整元数据（合并源 + 猜测）
# ══════════════════════════════════════════════════════════

def get_book_meta(filepath: Path) -> Dict[str, str]:
    """合并文件内嵌元数据 + 文件名猜测"""
    ext = filepath.suffix.lower()

    if ext == ".epub":
        file_meta = read_epub_metadata(filepath)
    elif ext == ".pdf":
        file_meta = read_pdf_metadata(filepath)
    else:
        file_meta = {"title": "", "author": "", "publisher": "", "date": "", "format": ext.upper()}

    guessed = guess_from_filename(filepath.name)

    meta = dict(file_meta)
    meta["guessed"] = {}

    for key in ("title", "author", "publisher"):
        if not meta.get(key):
            meta[key] = guessed.get(key, "")
            meta["guessed"][key] = bool(guessed.get(key))
        else:
            meta["guessed"][key] = False

    meta["filepath"] = str(filepath)
    meta["filename"] = filepath.name
    meta["size"] = filepath.stat().st_size

    return meta


# ══════════════════════════════════════════════════════════
#  封面缩略图提取
# ══════════════════════════════════════════════════════════

def extract_cover_thumbnail(filepath: Path, max_size: int = 8192) -> Optional[str]:
    """提取封面缩略图的 base64 data-uri，用于 HTML 展示"""
    ext = filepath.suffix.lower()

    if ext == ".epub":
        return _extract_epub_cover(filepath, max_size)
    elif ext == ".pdf":
        return _extract_pdf_cover(filepath, max_size)

    return None


def _extract_epub_cover(filepath: Path, max_size: int) -> Optional[str]:
    """从 EPUB 中提取第一张图片"""
    try:
        with zipfile.ZipFile(filepath, "r") as zf:
            image_exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            image_files = sorted(
                [n for n in zf.namelist() if Path(n).suffix.lower() in image_exts],
                key=lambda n: (0 if "cover" in n.lower() else 1, n),
            )
            if not image_files:
                return None

            for img_name in image_files[:5]:
                with zf.open(img_name) as f:
                    data = f.read(max_size)
                    if len(data) > 1024:
                        ext = Path(img_name).suffix.lower()
                        mime = {
                            ".jpg": "image/jpeg",
                            ".jpeg": "image/jpeg",
                            ".png": "image/png",
                            ".gif": "image/gif",
                            ".webp": "image/webp",
                        }.get(ext, "image/jpeg")
                        return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except Exception:
        pass
    return None


def _extract_pdf_cover(filepath: Path, max_size: int) -> Optional[str]:
    """从 PDF 中提取第一页作为缩略图（需 PyPDF2 + Pillow）"""
    try:
        from PyPDF2 import PdfReader
        from PIL import Image

        reader = PdfReader(str(filepath))
        if len(reader.pages) == 0:
            return None

        page = reader.pages[0]
        for img_obj in page.images:
            data = img_obj.data
            if len(data) > 512:
                ext = Path(img_obj.name).suffix.lower() if img_obj.name else ".png"
                mime = {
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".png": "image/png",
                }.get(ext, "image/png")
                # 限制大小
                if len(data) > max_size:
                    with io.BytesIO(data) as buf:
                        im = Image.open(buf)
                        im.thumbnail((120, 180), Image.LANCZOS)
                        out = io.BytesIO()
                        im.save(out, format="JPEG", quality=60)
                        data = out.getvalue()
                        mime = "image/jpeg"
                return f"data:{mime};base64,{base64.b64encode(data).decode()}"
    except ImportError:
        pass
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════
#  重复检测
# ══════════════════════════════════════════════════════════

def detect_duplicates(books: List[Dict]) -> Tuple[List[List[Dict]], List[Dict]]:
    """
    按 (文件名, 文件大小) 分组检测重复。
    返回: (重复组列表, 去重后的书籍列表)
    """
    groups: Dict[str, List[Dict]] = defaultdict(list)

    for book in books:
        key = f"{book['filename'].lower()}::{book['size']}"
        groups[key].append(book)

    duplicates = [v for v in groups.values() if len(v) > 1]
    unique = []
    seen = set()

    for book in books:
        key = f"{book['filename'].lower()}::{book['size']}"
        if key in seen:
            continue
        if len(groups[key]) > 1:
            # 保留第一个，其余为重复
            unique.append(book)
            seen.add(key)
        else:
            unique.append(book)
            seen.add(key)

    return duplicates, unique


# ══════════════════════════════════════════════════════════
#  目标路径计算 & 文件整理
# ══════════════════════════════════════════════════════════

def get_target_dir(base_dir: Path, book: Dict) -> Path:
    """计算目标文件夹: base/作者/书名首字母/"""
    author = book.get("author", "").strip()
    title = book.get("title", "").strip()

    if not author:
        author = "未知作者"
    if not title:
        title = "未知书名"

    # 取作者第一个非标点字符
    author_key = _get_first_meaningful_char(author)
    # 取书名第一个非标点字符
    title_key = _get_first_meaningful_char(title)

    return base_dir / author / f"{title_key}_{title}"


def _get_first_meaningful_char(s: str) -> str:
    """提取字符串中第一个有意义的字符（跳过标点、空格）"""
    cleaned = re.sub(r"[_\-\s\.\,;:!?\'\"\(\)\[\]{}《》「」『』【】〔〕]+", "", s)
    if cleaned:
        ch = cleaned[0]
        # 统一大写
        if ch.isalpha():
            return ch.upper()
        return ch
    return "X"


def organize_files(
    books: List[Dict],
    source_base: Path,
    target_base: Path,
    dry_run: bool = False,
    verbose: bool = True,
) -> List[Dict]:
    """按作者/书名首字母 移动文件"""
    results = []

    for book in books:
        src = Path(book["filepath"])
        dst_dir = get_target_dir(target_base, book)
        dst = dst_dir / book["filename"]

        # 如果目标已存在，添加序号
        counter = 1
        while dst.exists() and dst != src:
            stem = Path(book["filename"]).stem
            suffix = Path(book["filename"]).suffix
            dst = dst_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        if not dry_run:
            dst_dir.mkdir(parents=True, exist_ok=True)
            if src != dst:
                shutil.move(str(src), str(dst))

        if verbose:
            rel_src = src.relative_to(source_base) if source_base in src.parents else src
            rel_dst = dst.relative_to(target_base) if target_base in dst.parents else dst
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
    """将重复文件中非首位的移动到待清理文件夹"""
    if not duplicate_groups:
        return

    for group in duplicate_groups:
        if len(group) <= 1:
            continue

        # 保留第一个（已在上面的 unique 中处理），移动其余的
        for dup in group[1:]:
            src = Path(dup["filepath"])
            if not src.exists():
                dup["filepath"] = dup.get("new_path", str(src))
                src = Path(dup["filepath"])
                if not src.exists():
                    continue

            dst = cleanup_dir / src.name
            counter = 1
            while dst.exists():
                stem = src.stem
                dst = cleanup_dir / f"{stem}_{counter}{src.suffix}"
                counter += 1

            if not dry_run:
                cleanup_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))

            if verbose:
                tag = "[DRY-RUN]" if dry_run else "[DUP]"
                print(f"  {tag} 重复文件: {src.name} → 待清理/{dst.name}")


# ══════════════════════════════════════════════════════════
#  HTML 藏书清单生成
# ══════════════════════════════════════════════════════════

def generate_html_catalog(
    books: List[Dict],
    output_path: Path,
    extract_covers: bool = True,
):
    """生成按作者排序的 HTML 藏书清单"""
    sorted_books = sorted(books, key=lambda b: (
        _pinyin_sort_key(b.get("author", "")),
        _pinyin_sort_key(b.get("title", "")),
    ))

    rows_html = ""
    author_stats = defaultdict(int)

    for idx, book in enumerate(sorted_books):
        title = book.get("title") or "未知书名"
        author = book.get("author") or "未知作者"
        publisher = book.get("publisher") or "-"
        date = book.get("date") or "-"
        fmt = book.get("format") or "-"
        size_mb = book.get("size", 0) / (1024 * 1024)
        guessed = book.get("guessed", {})
        filename = book.get("filename", "")

        author_stats[author] += 1

        # 封面缩略图
        cover_html = '<div class="cover-placeholder">📖</div>'
        if extract_covers:
            filepath = Path(book.get("filepath") or book.get("new_path", ""))
            if filepath.exists():
                cover_data = extract_cover_thumbnail(filepath)
                if cover_data:
                    cover_html = f'<img class="cover-img" src="{cover_data}" alt="{title}">'

        # 猜测标记
        guess_badges = []
        for field, is_guessed in guessed.items():
            if is_guessed:
                guess_badges.append(
                    f'<span class="badge badge-guess" title="从文件名猜测">{field}</span>'
                )

        guess_html = " ".join(guess_badges) if guess_badges else ""

        rows_html += f"""
        <tr>
            <td class="cover-cell">{cover_html}</td>
            <td class="title-cell">{title}</td>
            <td class="author-cell">{author}</td>
            <td class="publisher-cell">{publisher}</td>
            <td class="date-cell">{date}</td>
            <td class="format-cell">{fmt}</td>
            <td class="size-cell">{size_mb:.2f} MB</td>
            <td class="guess-cell">{guess_html}</td>
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
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Microsoft YaHei", sans-serif;
    background: #f5f5f5;
    color: #333;
    line-height: 1.6;
}}
.header {{
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: white;
    padding: 40px 20px;
    text-align: center;
}}
.header h1 {{ font-size: 2em; margin-bottom: 8px; }}
.header .stats {{ font-size: 1em; opacity: 0.9; }}
.container {{
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    background: white;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 2px 12px rgba(0,0,0,0.08);
}}
th {{
    background: #667eea;
    color: white;
    padding: 14px 12px;
    text-align: left;
    font-weight: 600;
    font-size: 0.9em;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}
td {{
    padding: 12px;
    border-bottom: 1px solid #eee;
    vertical-align: middle;
}}
tr:hover {{ background: #f8f9ff; }}
.cover-cell {{ width: 80px; text-align: center; }}
.cover-placeholder {{
    width: 60px;
    height: 80px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: #f0f0f0;
    border-radius: 4px;
    font-size: 2em;
}}
.cover-img {{
    width: 60px;
    height: 80px;
    object-fit: cover;
    border-radius: 4px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.15);
}}
.title-cell {{ font-weight: 500; }}
.author-cell {{ color: #667eea; font-weight: 500; }}
.badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.75em;
    margin: 1px 2px;
}}
.badge-guess {{
    background: #fff3cd;
    color: #856404;
}}
.footer {{
    text-align: center;
    padding: 20px;
    color: #999;
    font-size: 0.85em;
}}
.group-header {{
    background: #f0f2ff !important;
    font-weight: 700 !important;
}}
</style>
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
        <thead>
            <tr>
                <th>封面</th>
                <th>书名</th>
                <th>作者</th>
                <th>出版社</th>
                <th>出版日期</th>
                <th>格式</th>
                <th>大小</th>
                <th>标记</th>
            </tr>
        </thead>
        <tbody>
{rows_html}
        </tbody>
    </table>
</div>
<div class="footer">
    <p>由 Ebook Organizer 自动生成</p>
</div>
</body>
</html>"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return str(output_path)


def _pinyin_sort_key(s: str) -> str:
    """简化的排序键：中文按笔画/拼音近似排序"""
    if not s:
        return "zzzzz"
    ch = s[0]
    if _CHINESE_RE.match(ch):
        # 中文字符放在字母后面
        return "zzz" + s
    return s.lower()


# ══════════════════════════════════════════════════════════
#  JSON 导出
# ══════════════════════════════════════════════════════════

def export_json(books: List[Dict], output_path: Path):
    """导出书籍清单为 JSON"""
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
            "filepath": book.get("new_path") or book.get("filepath", ""),
            "guessed_fields": book.get("guessed", {}),
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(export_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(output_path)


# ══════════════════════════════════════════════════════════
#  交互式确认
# ══════════════════════════════════════════════════════════

def confirm_guessed_metadata(books: List[Dict]) -> List[Dict]:
    """
    列出所有有猜测字段的书籍，让用户确认或修改。
    返回用户确认后的书籍列表。
    """
    guessed_books = [b for b in books if any(b.get("guessed", {}).values())]

    if not guessed_books:
        print("\n✅ 所有书籍元数据完整，无需确认。\n")
        return books

    print(f"\n{'='*60}")
    print(f"📋 以下 {len(guessed_books)} 本书的元数据是从文件名猜测的，请确认：")
    print(f"{'='*60}")

    book_map = {b["filepath"]: b for b in books}

    for i, book in enumerate(guessed_books):
        print(f"\n--- [{i+1}/{len(guessed_books)}] ---")
        print(f"  文件: {book['filename']}")
        print(f"  格式: {book.get('format', '-')}")

        for field in ("title", "author", "publisher"):
            val = book.get(field, "")
            is_guessed = book.get("guessed", {}).get(field, False)
            flag = " (猜测)" if is_guessed else ""
            print(f"  {field}: {val}{flag}")

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
                    if b.get("guessed"):
                        b["guessed"] = {}
                return books

            if user_input.lower() == "s":
                # 跳过此书
                print("    ⏭ 跳过此书")
                break
            elif user_input:
                book[field] = user_input
                book["guessed"][field] = False
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

def scan_books(source_dir: Path) -> List[Dict]:
    """递归扫描目录，收集所有 EPUB/PDF"""
    books = []
    exts = {".epub", ".pdf"}

    for filepath in source_dir.rglob("*"):
        if filepath.is_file() and filepath.suffix.lower() in exts:
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
  python ebook_organizer.py ./books --json ./catalog.json --html ./catalog.html
  python ebook_organizer.py ./books --dry-run              # 仅预览，不实际移动
  python ebook_organizer.py ./books --no-confirm           # 跳过交互式确认
        """,
    )

    parser.add_argument(
        "source",
        type=str,
        help="电子书源文件夹路径",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="输出/整理后的目标文件夹（默认为源文件夹下的 '已整理'）",
    )
    parser.add_argument(
        "--cleanup",
        type=str,
        default=None,
        help="重复文件待清理文件夹（默认为目标文件夹下的 '待清理'）",
    )
    parser.add_argument(
        "--html",
        type=str,
        default=None,
        help="HTML 藏书清单输出路径（默认为目标文件夹下的 catalog.html）",
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="JSON 清单输出路径（默认为目标文件夹下的 catalog.json）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅预览，不实际移动文件",
    )
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="跳过元数据猜测的交互式确认",
    )
    parser.add_argument(
        "--no-covers",
        action="store_true",
        help="不为 HTML 清单提取封面缩略图",
    )

    args = parser.parse_args()

    source_dir = Path(args.source).resolve()
    if not source_dir.exists() or not source_dir.is_dir():
        print(f"❌ 错误: 源文件夹不存在: {source_dir}")
        sys.exit(1)

    output_dir = Path(args.output).resolve() if args.output else source_dir / "已整理"
    cleanup_dir = Path(args.cleanup).resolve() if args.cleanup else output_dir / "待清理"
    html_path = Path(args.html).resolve() if args.html else output_dir / "catalog.html"
    json_path = Path(args.json).resolve() if args.json else output_dir / "catalog.json"

    print("=" * 60)
    print("📚 电子书整理工具")
    print("=" * 60)
    print(f"  源文件夹:   {source_dir}")
    print(f"  目标文件夹: {output_dir}")
    print(f"  待清理文件夹: {cleanup_dir}")
    if args.dry_run:
        print(f"  🔍 模式: 仅预览 (DRY-RUN)")

    # ── Step 1: 扫描 ──
    print(f"\n🔍 正在扫描电子书...")
    books = scan_books(source_dir)
    print(f"  发现 {len(books)} 本电子书 ({sum(1 for b in books if b['format'] == 'EPUB')} EPUB, "
          f"{sum(1 for b in books if b['format'] == 'PDF')} PDF)")

    if not books:
        print("❌ 未发现任何 EPUB 或 PDF 文件。")
        return

    # ── Step 2: 确认猜测的元数据 ──
    if not args.no_confirm:
        books = confirm_guessed_metadata(books)

    # ── Step 3: 去重检测 ──
    print(f"\n🔍 正在检测重复文件...")
    duplicate_groups, unique_books = detect_duplicates(books)

    dup_count = sum(len(g) - 1 for g in duplicate_groups)
    if dup_count > 0:
        print(f"  发现 {dup_count} 个重复文件（{len(duplicate_groups)} 组）：")
        for group in duplicate_groups:
            kept = group[0]
            for dup in group[1:]:
                print(f"    🔄 重复: {dup['filename']} ({dup.get('filepath', '')})")
                print(f"        保留: {kept['filename']} ({kept.get('filepath', '')})")
    else:
        print("  ✅ 未发现重复文件。")

    # ── Step 4: 移动重复文件到待清理 ──
    if dup_count > 0:
        print(f"\n🧹 移动重复文件到待清理文件夹...")
        move_duplicates(duplicate_groups, cleanup_dir, dry_run=args.dry_run)

    # ── Step 5: 整理唯一文件 ──
    print(f"\n📁 正在整理 {len(unique_books)} 本唯一书籍...")

    # 移除 output_dir 和 cleanup_dir，以免误把自己的输出当源
    books_to_organize = []
    for b in unique_books:
        p = Path(b["filepath"])
        try:
            p.relative_to(output_dir)
            print(f"  ⏭ 跳过（已在输出目录）: {b['filename']}")
        except ValueError:
            try:
                p.relative_to(cleanup_dir)
                print(f"  ⏭ 跳过（已在待清理目录）: {b['filename']}")
            except ValueError:
                books_to_organize.append(b)

    organized = organize_files(
        books_to_organize,
        source_dir,
        output_dir,
        dry_run=args.dry_run,
    )

    # ── Step 6: 生成 HTML 清单 ──
    print(f"\n📄 正在生成 HTML 藏书清单...")
    gen_html = generate_html_catalog(
        organized,
        html_path,
        extract_covers=not args.no_covers,
    )
    print(f"  ✅ HTML 清单: {gen_html}")

    # ── Step 7: 导出 JSON ──
    print(f"\n📄 正在导出 JSON 清单...")
    gen_json = export_json(organized, json_path)
    print(f"  ✅ JSON 清单: {gen_json}")

    # ── 总结 ──
    print(f"\n{'='*60}")
    print("✅ 整理完成！")
    print(f"  书籍总数: {len(books)}")
    print(f"  唯一书籍: {len(organized)}")
    print(f"  重复文件: {dup_count} (已移至: {cleanup_dir})")
    print(f"  HTML 清单: {html_path}")
    print(f"  JSON 清单: {json_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()