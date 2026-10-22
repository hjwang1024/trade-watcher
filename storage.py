"""
本地状态文件读写，统一存放在 data/ 目录
"""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


def exists(name: str) -> bool:
    return (DATA_DIR / name).exists()


def load_json(name: str, default=None):
    path = DATA_DIR / name
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(name: str, data):
    """先写临时文件再替换，避免写一半时进程退出导致文件损坏"""
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / name
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def remove(name: str):
    (DATA_DIR / name).unlink(missing_ok=True)
