import sys
sys.path.insert(0, '.')
from ebook_organizer import guess_from_filename

tests = [
    "01_special_chars.epub",
    "02_special_chars.epub",
    "03_special_chars.epub",
    "04_swap_test.epub",
    "04_活着 - 余华.epub",
    "05_平凡的世界 - 路遥.epub",
    "06_刘慈欣 - 三体 - 重庆出版社 - 2008.epub",
    "07_白夜行（东野圭吾）.epub",
    "08_恶意【东野圭吾】.epub",
    "11_莫言 - 蛙.pdf",
    "12_effective_java_by_bloch.pdf",
]

for f in tests:
    r = guess_from_filename(f)
    print(f"{f}: author={r['author']}, title={r['title']}, swap={r['swap_detected']}")