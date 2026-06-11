import zipfile
from pathlib import Path

TEST_DIR = Path(__file__).parent / "test_ebooks"

# 添加一本新书到源目录（模拟用户新增）
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

create_empty_epub("13_新增书籍_余华 - 文城")
print("✅ 新书已添加")