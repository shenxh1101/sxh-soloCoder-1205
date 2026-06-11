"""创建综合测试数据，覆盖所有新增功能"""
import zipfile
import base64
from pathlib import Path

TEST_DIR = Path(__file__).parent / "test_ebooks"
TEST_DIR.mkdir(exist_ok=True)

# 1x1 白色 PNG（用于测试封面提取）
_WHITE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)

def create_epub(name, title, author, publisher="", date="", cover=False):
    epub_path = TEST_DIR / f"{name}.epub"
    pub_xml = f"    <dc:publisher>{publisher}</dc:publisher>\n" if publisher else ""
    date_xml = f"    <dc:date>{date}</dc:date>\n" if date else ""

    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
{pub_xml}{date_xml}    <dc:identifier>urn:uuid:{name}</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>{""
    if not cover else ""}
    <item id="cover" href="cover.png" media-type="image/png"/>
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
            zf.writestr("cover.png", _WHITE_PNG)
    return epub_path

# ── 1. 安全目录名测试：书名含特殊字符 ──────────────────
create_epub("01_special_chars", "C++ Primer: The Complete Guide", "Stanley B. Lippman",
            "Addison-Wesley", "2012-08-06", cover=True)
create_epub("02_special_chars", "谁杀了她？东野圭吾作品", "东野圭吾",
            "南海出版公司", "2013-06-01")
create_epub("03_special_chars", "1984/Brave New World", "George Orwell",
            "Penguin", "1949-06-08")

# ── 2. 书名/作者反置检测 ────────────────────────────────
# 文件名是 "书名 - 作者" 格式，但第二段看起来像人名 → 不应该交换
create_epub("04_swap_test", "", "")  # 文件名: 活着 - 余华.epub  → 此处通过文件名猜测
# 实际上我们需要通过重命名来测试... 让我用创建空 EPUB 然后重命名的方式
# 直接在创建时处理

# 创建空 EPUB 然后通过文件名猜测
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

# 书名 - 作者 (正确方向，作者看起来像人名)
create_empty_epub("04_活着 - 余华")

# 书名 - 作者 (可能反置：第一段长得像人名但实际是书名+作者)
create_empty_epub("05_平凡的世界 - 路遥")

# 作者 - 书名 - 出版社 - 年份
create_empty_epub("06_刘慈欣 - 三体 - 重庆出版社 - 2008")

# 书名（作者）
create_empty_epub("07_白夜行（东野圭吾）")

# 书名【作者】
create_empty_epub("08_恶意【东野圭吾】")

# ── 3. 带封面的 EPUB ─────────────────────────────────────
create_epub("09_with_cover", "深入理解计算机系统", "Randal E. Bryant",
            "机械工业出版社", "2016-11-01", cover=True)

# ── 4. 重复文件 ──────────────────────────────────────────
epub_dup = create_epub("10_dup_original", "重复测试", "测试作者", "测试出版社", "2020-01-01")
import shutil
shutil.copy(str(epub_dup), str(TEST_DIR / "10_dup_copy.epub"))

# ── 5. 简单 PDF ──────────────────────────────────────────
def create_minimal_pdf(name):
    pdf_path = TEST_DIR / f"{name}.pdf"
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

create_minimal_pdf("11_莫言 - 蛙")
create_minimal_pdf("12_effective_java_by_bloch")

print("✅ 测试数据创建完成！")
print(f"   目录: {TEST_DIR}")
for f in sorted(TEST_DIR.iterdir()):
    print(f"   {f.name} ({f.stat().st_size} bytes)")