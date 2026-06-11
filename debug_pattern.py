import re
from pathlib import Path

# Copy the patterns
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

GUESS_PATTERNS = [
    re.compile(r"^(?P<author>.+?)\s*[-_–—]\s*(?P<title>.+?)\s*[-_–—]\s*(?P<publisher>.+?)\s*[-_–—]\s*(?P<year>\d{4})$"),
    re.compile(r"^(?P<author>.+?)\s*[-_–—]\s*(?P<title>.+?)\s*[\(（]\s*(?P<year>\d{4})\s*[\)）]$"),
    re.compile(r"^(?P<author>.+?)\s*[-_–—]\s*(?P<title>.+?)\s*[-_–—]\s*(?P<publisher>.+?)$"),
    re.compile(r"^(?P<title>.+?)\s*[-_–—]\s*(?P<author>.+?)\s*[-_–—]\s*(?P<publisher>.+?)$"),
    re.compile(r"^(?P<title>.+?)\s*[（\(](?P<author>[^）\)]+)[）\)]$"),
    re.compile(r"^(?P<title>.+?)\s*[【\[](?P<author>[^】\]]+)[】\]]$"),
    re.compile(r"^[\[【](?P<author>[^\]】]+)[\]】]\s*(?P<title>.+?)$"),
    re.compile(r"^[\(（](?P<author>[^\)）]+)[\)）]\s*(?P<title>.+?)$"),
    re.compile(r"^(?P<title>.+?)\s+by\s+(?P<author>.+?)$", re.IGNORECASE),
    re.compile(r"^(?P<author>.+?)\s*[-_–—]\s*(?P<title>.+?)$"),
    re.compile(r"^(?P<title>.+?)\s*[-_–—]\s*(?P<author>.+?)$"),
]

stem = "effective_java_by_bloch"
print(f"Stem: {stem}")
for i, p in enumerate(GUESS_PATTERNS):
    m = p.match(stem)
    if m:
        print(f"  Pattern {i} matched: {m.groupdict()}")
        break
else:
    print("  No pattern matched!")