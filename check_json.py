import json
from pathlib import Path

data = json.loads(Path("test_ebooks/已整理/catalog.json").read_text(encoding="utf-8"))
for b in data:
    print(f"{b['filename']}: author={b['author']}, title={b['title']}, swap={b['swap_detected']}")