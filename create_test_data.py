"""创建综合测试数据，覆盖所有新增功能"""
import zipfile
import base64
import shutil
import struct
import zlib
from pathlib import Path

TEST_DIR = Path(__file__).parent / "test_source"
if TEST_DIR.exists():
    shutil.rmtree(TEST_DIR)
TEST_DIR.mkdir(exist_ok=True)


def _make_png_byte(width, height, r, g, b):
    """生成一个纯色 PNG 字节，确保 > 256 字节"""
    def chunk(chunk_type, data):
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    raw = b""
    for y in range(height):
        raw += b"\x00"
        for x in range(width):
            raw += bytes([r, g, b])

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )

_SMALL_COVER = _make_png_byte(30, 40, 80, 120, 200)


def create_epub(stem, title, author, publisher="", date="", cover=False):
    epub_path = TEST_DIR / f"{stem}.epub"
    pub_xml = f"    <dc:publisher>{publisher}</dc:publisher>\n" if publisher else ""
    date_xml = f"    <dc:date>{date}</dc:date>\n" if date else ""

    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
{pub_xml}{date_xml}    <dc:identifier>urn:uuid:{stem}</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>"""
    if cover:
        opf += '\n    <item id="cover" href="cover.png" media-type="image/png"/>'
    opf += """
  </manifest>
  <spine>
    <itemref idref="ncx"/>
  </spine>
</package>"""

    container = """<?xml version="1.0" encoding="UTF-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    with zipfile.ZipFile(epub_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("content.opf", opf)
        if cover:
            zf.writestr("cover.png", _SMALL_COVER)
    return epub_path


def create_empty_epub(stem):
    epub_path = TEST_DIR / f"{stem}.epub"
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title></dc:title><dc:creator></dc:creator>
  </metadata>
</package>"""
    with zipfile.ZipFile(epub_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("content.opf", opf)
    return epub_path


def create_minimal_pdf(stem):
    pdf_path = TEST_DIR / f"{stem}.pdf"
    pdf_path.write_bytes(b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>
endobj
xref
0 4
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
trailer
<< /Size 4 /Root 1 0 R >>
startxref
190
%%EOF""")
    return pdf_path


# ══════════════════════════════════════════════════════════
# 1. 安全目录名 + 内嵌封面：书名含特殊字符，有封面 >256 字节
# ══════════════════════════════════════════════════════════
create_epub("01_special_chars", "C++ Primer: The Complete Guide", "Stanley B. Lippman",
            "Addison-Wesley", "2012-08-06", cover=True)
create_epub("02_special_chars", "谁杀了她？东野圭吾作品", "东野圭吾",
            "南海出版公司", "2013-06-01", cover=True)
create_epub("03_special_chars", "1984/Brave New World", "George Orwell",
            "Penguin", "1949-06-08", cover=True)

# ══════════════════════════════════════════════════════════
# 2. 文件名猜测 — 作者-书名（正确方向），自动交换测试
# ══════════════════════════════════════════════════════════
create_empty_epub("04_活着 - 余华")
create_empty_epub("05_平凡的世界 - 路遥")
create_epub("06_刘慈欣 - 三体 - 重庆出版社 - 2008", "三体", "刘慈欣",
            "重庆出版社", "2008-01-01")
create_empty_epub("07_白夜行（东野圭吾）")
create_empty_epub("08_恶意【东野圭吾】")

# ══════════════════════════════════════════════════════════
# 3. 内嵌封面测试 — 小尺寸图片 >256 字节
# ══════════════════════════════════════════════════════════
create_epub("09_with_cover", "深入理解计算机系统", "Randal E. Bryant",
            "机械工业出版社", "2016-11-01", cover=True)

# ══════════════════════════════════════════════════════════
# 4. 重复文件 — 用于各去重策略测试
# ══════════════════════════════════════════════════════════
dup_base = create_epub("10_dup_original", "重复测试书", "测试作者",
                        "测试出版社", "2020-01-01", cover=True)
shutil.copy(str(dup_base), str(TEST_DIR / "10_dup_copy.epub"))

# ══════════════════════════════════════════════════════════
# 5. by 模式 + 下划线分隔
# ══════════════════════════════════════════════════════════
create_empty_epub("11_Effective Java by Bloch")
create_empty_epub("12_clean_code_by_robert_martin")

# ══════════════════════════════════════════════════════════
# 6. 书名中带短横线 / 连字符 — 不应被错误拆分
# ══════════════════════════════════════════════════════════
create_empty_epub("13_Spider-Man - 斯坦李")
create_empty_epub("14_Design-Patterns - Gang of Four")
create_empty_epub("15_Thinking_Fast_and_Slow_by_Daniel_Kahneman")

# ══════════════════════════════════════════════════════════
# 7. 不合理作者名过滤
# ══════════════════════════════════════════════════════════
create_empty_epub("16_the_test_book")
create_empty_epub("17_新增书籍 - draft")  # "draft" 不是有效作者
create_empty_epub("18_高清扫描版 - 下载")   # "下载" 不是有效作者

# ══════════════════════════════════════════════════════════
# 8. PDF 文件
# ══════════════════════════════════════════════════════════
create_minimal_pdf("19_莫言 - 蛙")
create_minimal_pdf("20_clean_architecture_by_robert_martin")

# ══════════════════════════════════════════════════════════
# 9. 更多下划线分隔测试
# ══════════════════════════════════════════════════════════
create_empty_epub("21_深入理解计算机系统_第3版_by_Randal_Bryant")
create_empty_epub("22_重构_改善既有代码的设计_by_Martin_Fowler")

print("✅ 测试数据创建完成！")
print(f"   目录: {TEST_DIR}")
for f in sorted(TEST_DIR.iterdir()):
    size_kb = f.stat().st_size / 1024
    print(f"   {f.name} ({size_kb:.1f} KB)")