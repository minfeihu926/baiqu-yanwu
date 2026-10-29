# -*- coding: utf-8 -*-
"""不经过 pip, 直接把 PyInstaller 及其依赖下载解压到 .build-tools(本机打包用)。"""
import json
import os
import sys
import urllib.request
import zipfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(BASE, ".build-tools")
WHEELS = os.path.join(BASE, ".wheels")

PKGS = ["pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile",
        "pywin32-ctypes", "packaging", "setuptools"]

PREF = ("py3-none-any", "py2.py3-none-any", "py3-none-win_amd64",
        "cp312-win_amd64", "cp312-none-win_amd64", "win_amd64")


def pick(urls):
    cands = []
    for u in urls:
        fn = u["filename"]
        if not fn.endswith(".whl"):
            continue
        score = -1
        for i, p in enumerate(PREF):
            if p in fn:
                score = len(PREF) - i
                break
        if "none-any" in fn:
            score = max(score, 1)
        if score > 0:
            cands.append((score, -len(fn), u))
    if not cands:
        return None
    cands.sort(key=lambda t: (-t[0], -t[1]))
    return cands[0][2]


def main():
    os.makedirs(WHEELS, exist_ok=True)
    os.makedirs(TARGET, exist_ok=True)
    for pkg in PKGS:
        data = json.load(urllib.request.urlopen(
            f"https://pypi.org/pypi/{pkg}/json", timeout=60))
        u = pick(data["urls"])
        if not u:
            print(f"[!] 没有可用 wheel: {pkg}")
            continue
        path = os.path.join(WHEELS, u["filename"])
        if not os.path.exists(path):
            print(f"下载 {u['filename']}")
            urllib.request.urlretrieve(u["url"], path)
        with zipfile.ZipFile(path) as z:
            z.extractall(TARGET)
        print(f"解压 {u['filename']}")
    sys.path.insert(0, TARGET)
    import PyInstaller  # noqa: E402
    print("PyInstaller", PyInstaller.__version__)


if __name__ == "__main__":
    main()
