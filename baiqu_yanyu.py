#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百趣演武 · 自动抽牌(第一轮)

逐张抽牌, 每张牌识别 点数(1-5) + 花色(万/条/筒), 按选定的目标牌型判断:

  顺子      抽满 5 张, 出现四连(1-2-3-4 或 2-3-4-5)
  同花      已抽的牌绝对同色, 凑到 4 张即停
  同花顺    五张同花色, 且点数恰好 1-2-3-4-5
  四条      五张内有四张同点数(五条也算)
  葫芦      五张内 三张同点 + 两张同点
  三条      五张内有三张同点数
  两对      五张内两个不同点数各至少两张

判定用"包含"语义: 更大的牌型也算达成。
中途判断出目标已不可能 -> 「放弃挑战」→「确认」→ 重抽;
达成目标 -> 停手、打印结果、脚本退出(不点「开始挑战」)。
轮数默认无上限, Ctrl+C 结束。

识别沿用 1.0 的做法: 模板(带白边的图块)按卡牌高度缩放, 在卡牌左上角取框内
整幅滑动做归一化互相关, 再乘 0.8~1.2 五档缩放, 取最高分。

依赖: numpy, pillow
许可证: MIT

用法:
  python baiqu_yanyu.py                      # 菜单选目标牌型
  python baiqu_yanyu.py -p 顺子               # 指定牌型
  python baiqu_yanyu.py -p 同花 -r 20         # 最多 20 轮
  python baiqu_yanyu.py --check              # 只截图识别, 不点击
  python baiqu_yanyu.py --image x.png        # 离线分析一张截图
"""
import argparse
import ctypes
import os
import sys
import time
import unicodedata
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from PIL import Image, ImageGrab

__version__ = "1.3.0"

if getattr(sys, "frozen", False):
    # PyInstaller onefile: 资源解压在 sys._MEIPASS
    BASE = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(BASE, "templates")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

# ---------------------------------------------------------------- 可调参数

WINDOW_CLASS = "UnityWndClass"
WINDOW_TITLE = "Endfield"

HAND_SIZE = 5                  # 手牌张数
MATCH_MIN = 0.70               # 模板匹配最低分
MATCH_MARGIN = 0.05            # 第一名与第二名的差距下限
REVEAL_WHITE = 0.20            # 卡牌"已翻开": 白色像素占比
REVEAL_DARK = 0.03             # 同时要有深色笔画(防翻牌中途误判)

DRAW_WAIT = 1.8                # 点抽牌后等待翻牌(超时就重新点)
CONFIRM_WAIT = 3.0             # 点放弃后等待弹窗
RESET_WAIT = 8.0               # 点确认后等待回到抽牌界面
CLICK_SETTLE = 0.05            # 点击间隔

# 卡牌几何(相对单张卡牌): cx, cy, 半宽, 半高; 由 2560x1600 截图测量
RANK_ROI = (0.1104, 0.1173, 0.115, 0.105)   # 点数搜索框
SUIT_ROI = (0.2985, 0.1283, 0.090, 0.090)   # 花色搜索框
REF_CARD_H = 452.0             # 模板采集时的卡牌高度(像素)
SCALES = (0.80, 0.90, 1.00, 1.10, 1.20)     # 原版的多尺度
SLOT_PITCH_RATIO = 1.0985      # 卡牌间距 / 卡牌宽度(16:10)

# 单张卡牌应占窗口的比例, 用来剔掉错误的卡槽
CARD_W_RANGE = (0.10, 0.17)
CARD_H_RANGE = (0.21, 0.36)

# 16:10 与 16:9 的兜底卡槽(实测): x0, y0, w, h
FALLBACK_1610 = [(0.0836, 0.2500, 0.1309, 0.2831),
                 (0.2273, 0.2500, 0.1309, 0.2831),
                 (0.3711, 0.2500, 0.1309, 0.2831),
                 (0.5148, 0.2500, 0.1309, 0.2831),
                 (0.6586, 0.2500, 0.1309, 0.2831)]
FALLBACK_169 = [(0.0844 + i * 0.14375, 0.2222, 0.1300, 0.3472) for i in range(5)]

# 按钮搜索区域与颜色(比例坐标)
BTN_SPECS = {
    "draw":     (0.80, 0.66, 1.00, 0.90, "cyan",   0.0025),
    "abandon":  (0.56, 0.84, 0.84, 0.995, "red",    0.0015),
    "start":    (0.70, 0.895, 0.94, 0.995, "white", 0.0015),
    "confirm":  (0.38, 0.50, 0.80, 0.76, "yellow",  0.0015),
}

SUITS = ("wan", "tiao", "tong")
SUIT_CN = {"wan": "万", "tiao": "条", "tong": "筒"}
RANKS = (1, 2, 3, 4, 5)

PATTERNS = {
    "straight": ("顺子", "五张内出现四连(1-2-3-4 或 2-3-4-5)"),
    "flush": ("同花", "绝对同色, 凑到四张即停"),
    "straight_flush": ("同花顺", "五张同花色, 且点数恰好 1-2-3-4-5"),
    "four": ("四条", "五张内有四张同点数(五条也算)"),
    "full_house": ("葫芦", "五张内 三张同点 + 两张同点"),
    "three": ("三条", "五张内有三张同点数"),
    "two_pair": ("两对", "五张内两个不同点数各至少两张"),
}
PATTERN_ORDER = ["straight", "flush", "straight_flush", "four",
                 "full_house", "three", "two_pair"]


# ---------------------------------------------------------------- Win32

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def enable_dpi_awareness() -> None:
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x8
TOKEN_ELEVATION = 20


def proc_exe(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def is_elevated_pid(pid: int) -> bool:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        tok = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
            return False
        try:
            elev = wintypes.DWORD(0)
            ret = wintypes.DWORD(0)
            ok = advapi32.GetTokenInformation(
                tok, TOKEN_ELEVATION, ctypes.byref(elev),
                ctypes.sizeof(elev), ctypes.byref(ret))
            return bool(ok and elev.value)
        finally:
            kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(h)


def _enum_windows():
    """枚举可见顶层窗口的基本信息。"""
    rows = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rc = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rc))
        rows.append({
            "hwnd": hwnd,
            "title": buf.value,
            "cls": cls.value,
            "pid": pid.value,
            "exe": proc_exe(pid.value),
            "w": rc.right - rc.left,
            "h": rc.bottom - rc.top,
            "min": bool(user32.IsIconic(hwnd)),
        })
        return True

    user32.EnumWindows(EnumProc(_cb), 0)
    return rows


def find_game_window(want_class: str = WINDOW_CLASS,
                     want_title: str = WINDOW_TITLE):
    """按 [类名+标题] -> [进程名 Endfield*.exe] -> [Unity 窗口] 打分找窗口。"""
    best, best_key = None, (-1, -1)
    want_title_l = want_title.lower()
    for r in _enum_windows():
        cls_l = r["cls"].lower()
        title_l = r["title"].lower()
        base = os.path.basename(r["exe"]).lower()
        score = 0
        if cls_l == want_class.lower() and want_title_l in title_l:
            score += 100
        if want_title_l and want_title_l in title_l:
            score += 30
        if base == "endfield.exe":
            score += 90
        elif "endfield" in base:
            score += 70
        if "unity" in cls_l and r["title"]:
            score += 20
        if score <= 0:
            continue
        if (r["min"] or r["w"] < 640 or r["h"] < 360) and score < 70:
            continue
        key = (score, 0 if r["min"] else r["w"] * r["h"])
        if key > best_key:
            best_key, best = key, r
    return best


def print_window_hint(limit: int = 12):
    print("--- 当前可见窗口(按面积; 含最小化的游戏窗口) ---")
    rows = [r for r in _enum_windows()
            if (r["w"] >= 200 and r["h"] >= 120) or r["min"]]
    rows.sort(key=lambda r: -(r["w"] * r["h"]))
    for r in rows[:limit]:
        flag = " [最小化]" if r["min"] else ""
        print(f"  hwnd={r['hwnd']:#x} {r['w']}x{r['h']}{flag} "
              f"exe={os.path.basename(r['exe'])!r} "
              f"class={r['cls']!r} title={r['title']!r}")


def window_info(hwnd: int):
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    rc = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    return {
        "hwnd": hwnd, "title": buf.value, "cls": cls.value,
        "pid": pid.value, "exe": proc_exe(pid.value),
        "w": rc.right - rc.left, "h": rc.bottom - rc.top,
        "min": bool(user32.IsIconic(hwnd)),
    }


def client_rect(hwnd):
    """客户区在屏幕上的 (x, y, w, h), 物理像素。"""
    rc = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    w, h = rc.right - rc.left, rc.bottom - rc.top
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return pt.x, pt.y, w, h


def activate(hwnd) -> bool:
    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    time.sleep(0.15)
    if user32.GetForegroundWindow() == hwnd:
        return True
    # Windows 前台锁: 先模拟一次 Alt 键, 再 SetForegroundWindow
    user32.keybd_event(0x12, 0, 0, 0)      # VK_MENU down
    user32.keybd_event(0x12, 0, 2, 0)      # VK_MENU up
    fg = user32.GetForegroundWindow()
    cur_tid = user32.GetWindowThreadProcessId(fg, None)
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
    try:
        user32.AttachThreadInput(cur_tid, tgt_tid, True)
    except Exception:
        pass
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    try:
        user32.AttachThreadInput(cur_tid, tgt_tid, False)
    except Exception:
        pass
    try:
        user32.SwitchToThisWindow(hwnd, True)
    except Exception:
        pass
    for _ in range(8):
        time.sleep(0.1)
        if user32.GetForegroundWindow() == hwnd:
            return True
    return False


def grab(rect) -> Image.Image:
    x, y, w, h = rect
    return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True)


_APP = {"hwnd": None}


def click_norm(fx: float, fy: float) -> None:
    """按窗口内比例坐标点击; 点击前校验前台窗口, 防止误点其它程序。"""
    hwnd = _APP.get("hwnd")
    if not hwnd:
        raise RuntimeError("窗口未初始化")
    if user32.GetForegroundWindow() != hwnd:
        raise RuntimeError("游戏窗口不在前台, 已取消本次点击")
    rect = client_rect(hwnd)
    x, y, w, h = rect
    sx, sy = int(x + fx * w), int(y + fy * h)
    user32.SetCursorPos(sx, sy)
    time.sleep(0.04)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    time.sleep(CLICK_SETTLE)


# ---------------------------------------------------------------- 图像分析

@dataclass
class Card:
    rank: Optional[int]
    suit: Optional[str]
    rank_score: float = 0.0
    suit_score: float = 0.0

    @property
    def known(self) -> bool:
        return self.rank is not None and self.suit is not None

    def __str__(self) -> str:
        if self.rank is None or self.suit is None:
            return "??"
        return f"{self.rank}{SUIT_CN[self.suit]}"


@dataclass
class Slot:
    x0: int
    y0: int
    x1: int
    y1: int
    revealed: bool = False
    card: Optional[Card] = None

    @property
    def w(self):
        return self.x1 - self.x0

    @property
    def h(self):
        return self.y1 - self.y0


def runs_of(mask1d: np.ndarray, min_len: int):
    out, start = [], None
    for i, v in enumerate(mask1d):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i - 1))
            start = None
    if start is not None and len(mask1d) - start >= min_len:
        out.append((start, len(mask1d) - 1))
    return out


def card_aspect(W: int, H: int) -> float:
    """卡牌高/宽: 16:10 是 452/335, 16:9 的窗口里卡牌被拉得更高。"""
    return 1.506 if W / H > 1.65 else 452 / 335


def detect_teal_slots(img: Image.Image, a=None):
    """找青色卡背(未翻开的牌)。返回 Slot 列表(可能 0~5 个)。"""
    if a is None:
        a = np.asarray(img.convert("RGB")).astype(np.int16)
    H, W = a.shape[:2]
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    teal = (np.abs(R - 116) <= 22) & (np.abs(G - 164) <= 18) & (np.abs(B - 174) <= 18)
    m = np.zeros((H, W), bool)
    m[int(H * 0.17):int(H * 0.66), int(W * 0.02):int(W * 0.90)] = True
    teal &= m
    cols = teal.sum(axis=0)
    thr = max(8, int(H * 0.02))
    aspect = card_aspect(W, H)
    slots = []
    for x0, x1 in runs_of(cols > thr, min_len=max(10, int(W * 0.05))):
        w = int(x1) - int(x0) + 1
        if not (CARD_W_RANGE[0] * W <= w <= CARD_W_RANGE[1] * W):
            continue
        sub = teal[:, x0:x1 + 1]
        rows = sub.sum(axis=1)
        ys = np.nonzero(rows > max(4, (x1 - x0) * 0.15))[0]
        if len(ys) < 20:
            continue
        # 卡背底部有青色反光, 直接取 ys.max() 会把卡槽拉高; 按固定长宽比重算
        top = int(ys.min())
        h = int(round(w * aspect))
        if not (CARD_H_RANGE[0] * H <= h <= CARD_H_RANGE[1] * H):
            continue
        slots.append(Slot(int(x0), top, int(x0) + w, top + h))
    slots.sort(key=lambda s: s.x0)
    return slots


def fallback_slots(img: Image.Image):
    W, H = img.size
    table = FALLBACK_169 if W / H > 1.65 else FALLBACK_1610
    return [Slot(int(fx * W), int(fy * H),
                 int((fx + fw) * W), int((fy + fh) * H))
            for fx, fy, fw, fh in table]


def infer_slots(img: Image.Image, teal, prev_slots):
    """5 张青色卡背 -> 直接用; 部分翻开 -> 按等间距推; 都没有 -> 用兜底。"""
    W, H = img.size
    if len(teal) == HAND_SIZE:
        return teal
    if len(teal) >= 1:
        w = float(np.median([s.w for s in teal]))
        y0 = int(np.median([s.y0 for s in teal]))
        h = int(np.median([s.h for s in teal]))
        pitch = w * SLOT_PITCH_RATIO
        anchor = min(teal, key=lambda s: s.x0)
        fb = fallback_slots(img)
        idx = min(range(HAND_SIZE), key=lambda i: abs(fb[i].x0 - anchor.x0))
        x0 = anchor.x0 - idx * pitch
        if 0 <= x0 <= W * 0.92:
            return [Slot(int(round(x0 + i * pitch)), y0,
                         int(round(x0 + i * pitch + w)), y0 + h)
                    for i in range(HAND_SIZE)]
    if prev_slots is not None and len(prev_slots) == HAND_SIZE:
        return prev_slots
    return fallback_slots(img)


def box_of(s: Slot, spec):
    cx, cy, hw, hh = spec
    return (int(round(s.x0 + (cx - hw) * s.w)), int(round(s.y0 + (cy - hh) * s.h)),
            int(round(s.x0 + (cx + hw) * s.w)), int(round(s.y0 + (cy + hh) * s.h)))


def is_revealed(a: np.ndarray, s: Slot) -> bool:
    """卡牌左上角既要是白底, 又要有已渲染的深色点数/文字, 才算"翻开的牌"。"""
    x0, y0, x1, y1 = box_of(s, RANK_ROI)
    H, W = a.shape[:2]
    x0, x1 = max(0, x0), min(W, x1)
    y0, y1 = max(0, y0), min(H, y1)
    if x1 - x0 < 5 or y1 - y0 < 5:
        return False
    patch = a[y0:y1, x0:x1]
    R, G, B = patch[..., 0], patch[..., 1], patch[..., 2]
    white = (R > 205) & (G > 200) & (B > 200)
    dark = (R < 115) & (G < 115) & (B < 115)
    return float(white.mean()) > REVEAL_WHITE and float(dark.mean()) > REVEAL_DARK


_TPL = {}


def load_templates() -> Dict[str, Image.Image]:
    if _TPL:
        return _TPL
    for r in RANKS:
        p = os.path.join(TPL_DIR, f"r{r}.png")
        if not os.path.exists(p):
            raise FileNotFoundError(f"缺少模板: {p}")
        _TPL[f"r{r}"] = Image.open(p).convert("L")
    for s in SUITS:
        p = os.path.join(TPL_DIR, f"s_{s}.png")
        if not os.path.exists(p):
            raise FileNotFoundError(f"缺少模板: {p}")
        _TPL[f"s_{s}"] = Image.open(p).convert("L")
    return _TPL


def _match_at(patch: np.ndarray, tpl: Image.Image, th: int) -> float:
    """模板缩放到 th 高, 在取框内整幅滑动做 NCC, 返回最高分。"""
    ph, pw = patch.shape[:2]
    tw = int(round(tpl.width * th / tpl.height))
    if th < 8 or tw < 6 or th >= ph or tw >= pw:
        return 0.0
    t = np.asarray(tpl.resize((tw, th), Image.LANCZOS), dtype=np.float32)
    tz = t - float(t.mean())
    tn = float(np.sqrt((tz * tz).sum())) + 1e-6
    win = np.lib.stride_tricks.sliding_window_view(patch, (th, tw))
    wc = win - win.mean(axis=(2, 3), keepdims=True)
    den = np.sqrt((wc * wc).sum(axis=(2, 3))) * tn + 1e-6
    score = (wc * tz).sum(axis=(2, 3)) / den
    return float(score.max())


def match_group(patch: np.ndarray, items, card_h: float,
                fast_ok: float = 0.86) -> Dict[str, float]:
    """一组模板(点数或花色)比一次原尺寸; 最高分不够才补上 0.8~1.2 五档。

    items: [(key, 模板, ...)]; 缩放基准是各模板自身高度 / 452 * 当前卡高。
    """
    out = {}
    for key, tpl, _h in items:
        th = tpl.height / REF_CARD_H * card_h
        out[key] = _match_at(patch, tpl, int(round(th)))
    if max(out.values(), default=0.0) >= fast_ok:
        return out
    for key, tpl, _h in items:
        th = tpl.height / REF_CARD_H * card_h
        best = out[key]
        for f in SCALES:
            if abs(f - 1.0) < 1e-6:
                continue
            best = max(best, _match_at(patch, tpl, int(round(th * f))))
        out[key] = best
    return out


def best_of(scores: Dict[str, float]):
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    key, sc = ordered[0]
    margin = sc - ordered[1][1] if len(ordered) > 1 else 1.0
    return key, float(sc), float(margin)


def read_card(gray: np.ndarray, s: Slot) -> Card:
    H, W = gray.shape[:2]
    if not (CARD_W_RANGE[0] * W <= s.w <= CARD_W_RANGE[1] * W
            and CARD_H_RANGE[0] * H <= s.h <= CARD_H_RANGE[1] * H):
        return Card(None, None)
    tpl = load_templates()
    x0, y0, x1, y1 = box_of(s, RANK_ROI)
    rpatch = gray[max(0, y0):min(H, y1), max(0, x0):min(W, x1)]
    x0, y0, x1, y1 = box_of(s, SUIT_ROI)
    spatch = gray[max(0, y0):min(H, y1), max(0, x0):min(W, x1)]

    rank_scores = match_group(rpatch, [(str(r), tpl[f"r{r}"], s.h) for r in RANKS], s.h)
    suit_scores = match_group(spatch, [(su, tpl[f"s_{su}"], s.h) for su in SUITS], s.h)

    rk, rs, rm = best_of(rank_scores)
    sk, ss, sm = best_of(suit_scores)
    rank = int(rk) if rs >= MATCH_MIN and rm >= MATCH_MARGIN else None
    suit = sk if ss >= MATCH_MIN and sm >= MATCH_MARGIN else None
    return Card(rank, suit, rs, ss)


def color_mask(spec: str, a: np.ndarray) -> np.ndarray:
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    if spec == "cyan":
        return (R > 85) & (R < 165) & (G > 185) & (B > 205) & ((G - R) > 55) & ((B - R) > 65)
    if spec == "red":
        return (R > 225) & (G > 45) & (G < 135) & (B > 45) & (B < 135) & ((R - G) > 100)
    if spec == "white":
        return (R > 238) & (G > 233) & (B > 233)
    if spec == "yellow":
        return (R > 225) & (G > 160) & (B < 145) & ((R - B) > 100) & ((G - B) > 70)
    return np.zeros(a.shape[:2], bool)


def find_button(a: np.ndarray, name: str):
    """返回按钮中心的比例坐标 (fx, fy); 找不到返回 None。"""
    x0f, y0f, x1f, y1f, color, min_ratio = BTN_SPECS[name]
    H, W = a.shape[:2]
    x0, y0 = int(x0f * W), int(y0f * H)
    x1, y1 = int(x1f * W), int(y1f * H)
    sub = color_mask(color, a[y0:y1, x0:x1])
    cnt = int(sub.sum())
    if cnt < max(50, int(min_ratio * W * H)):
        return None
    ys, xs = np.nonzero(sub)
    fx = (x0 + float(np.median(xs))) / W
    fy = (y0 + float(np.median(ys))) / H
    return fx, fy


# ---------------------------------------------------------------- 牌型判定

def rank_counts(cards: List[Card]) -> Dict[int, int]:
    out: Dict[int, int] = {}
    for c in cards:
        if c.rank is not None:
            out[c.rank] = out.get(c.rank, 0) + 1
    return out


def suit_counts(cards: List[Card]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in cards:
        if c.suit is not None:
            out[c.suit] = out.get(c.suit, 0) + 1
    return out


STRAIGHT_WINDOWS = ((1, 2, 3, 4), (2, 3, 4, 5))


def has_straight4(cards: List[Card]) -> bool:
    have = set(rank_counts(cards))
    return any(all(r in have for r in w) for w in STRAIGHT_WINDOWS)


def is_hit(cards: List[Card], target: str) -> bool:
    """当前牌面是否已经达成目标(包含语义: 更大的牌型也算)。"""
    if target == "flush":
        return len(cards) >= 4 and len(suit_counts(cards)) == 1
    if len(cards) < HAND_SIZE:
        return False
    rc = rank_counts(cards)
    cs = sorted(rc.values(), reverse=True)
    if target == "straight":
        return has_straight4(cards)
    if target == "straight_flush":
        return len(suit_counts(cards)) == 1 and set(rc) == {1, 2, 3, 4, 5}
    if target == "four":
        return cs[0] >= 4
    if target == "full_house":
        return len(cs) >= 2 and cs[0] >= 3 and cs[1] >= 2
    if target == "three":
        return cs[0] >= 3
    if target == "two_pair":
        return sum(1 for v in cs if v >= 2) >= 2
    raise ValueError(target)


def can_complete(cards: List[Card], target: str) -> bool:
    """剩下的空位是否还有可能凑成目标(保守: 不看牌库剩余)。"""
    if is_hit(cards, target):
        return True
    rem = HAND_SIZE - len(cards)
    if rem <= 0:
        return False
    rc = rank_counts(cards)
    sc = suit_counts(cards)
    max_rank = max(rc.values()) if rc else 0

    if target == "flush":
        if len(sc) > 1:
            return False
        return sum(sc.values()) + rem >= 4
    if target == "straight":
        have = set(rc)
        return any(len([r for r in w if r not in have]) <= rem
                   for w in STRAIGHT_WINDOWS)
    if target == "straight_flush":
        if len(sc) > 1 or any(v > 1 for v in rc.values()):
            return False
        return len({1, 2, 3, 4, 5} - set(rc)) <= rem
    if target == "four":
        return max_rank + rem >= 4
    if target == "three":
        return max_rank + rem >= 3
    if target == "full_house":
        ranks = set(rc) | set(range(1, 6))
        for r in ranks:
            need3 = max(0, 3 - rc.get(r, 0))
            if need3 > rem:
                continue
            for q in ranks:
                if q != r and need3 + max(0, 2 - rc.get(q, 0)) <= rem:
                    return True
        return False
    if target == "two_pair":
        pairs = sum(1 for v in rc.values() if v >= 2)
        singles = sum(1 for v in rc.values() if v == 1)
        if pairs >= 2:
            return True
        if pairs == 1:
            return (1 if singles else 2) <= rem
        return (2 if singles >= 2 else (3 if singles == 1 else 4)) <= rem
    raise ValueError(target)


def decide(cards: List[Card], target: str) -> str:
    if is_hit(cards, target):
        return "stop"
    if not can_complete(cards, target):
        return "abandon"
    return "draw"


def fmt_cards(cards: List[Card]) -> str:
    return " ".join(str(c) for c in cards) if cards else "-"


def _disp_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _disp_width(text))


# ---------------------------------------------------------------- 状态与动作

@dataclass
class Frame:
    slots: list
    cards: List[Card]
    revealed: int
    buttons: dict = field(default_factory=dict)
    unknown: int = 0
    teal: int = 0


def analyze_slots(img: Image.Image, prev_slots=None, a=None, gray=None,
                  cache=None):
    if a is None:
        a = np.asarray(img.convert("RGB")).astype(np.int16)
    if gray is None:
        gray = np.asarray(img.convert("L")).astype(np.float32)
    teal = detect_teal_slots(img, a)
    slots = infer_slots(img, teal, prev_slots)
    cards = []
    for i, s in enumerate(slots):
        s.revealed = is_revealed(a, s)
        if not s.revealed:
            s.card = None
            continue
        c = cache.get(i) if cache is not None else None
        if c is None or not c.known:
            c = read_card(gray, s)
            if cache is not None and c is not None and c.known:
                cache[i] = c   # 同一局里这张牌不会变, 认准一次就复用
        s.card = c
        if c is not None and c.known:
            cards.append(c)
    return slots, cards, len(teal)


def inspect(img: Image.Image, prev_slots=None, with_start: bool = False,
            cache=None) -> Frame:
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    gray = np.asarray(img.convert("L")).astype(np.float32)
    slots, cards, teal_n = analyze_slots(img, prev_slots, a=a, gray=gray, cache=cache)
    revealed = sum(1 for s in slots if s.revealed)
    names = ("draw", "abandon", "confirm") + (("start",) if with_start else ())
    buttons = {name: find_button(a, name) for name in names}
    return Frame(slots, cards, revealed, buttons,
                 unknown=max(0, revealed - len(cards)), teal=teal_n)


def wait_frame(grabber, pred, timeout, prev_slots=None, interval=0.0, cache=None):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        fr = inspect(grabber(), prev_slots, cache=cache)
        last = fr
        if pred(fr):
            return fr, True
        if interval:
            time.sleep(interval)
    return last, False


# ---------------------------------------------------------------- 主流程

def cmd_check(img: Image.Image):
    fr = inspect(img, with_start=True)
    print(f"窗口图像: {img.size[0]}x{img.size[1]}")
    print(f"识别到卡槽: {len(fr.slots)}")
    for i, s in enumerate(fr.slots):
        if s.revealed and s.card is not None:
            c = s.card
            print(f"  #{i+1} {str(c):>4}  点数分={c.rank_score:.3f} 花色分={c.suit_score:.3f}")
        else:
            print(f"  #{i+1} 未翻开/无法识别")
    print(f"已翻开: {fr.revealed}  识别成功: {len(fr.cards)}  -> {fmt_cards(fr.cards)}")
    for k, v in fr.buttons.items():
        print(f"按钮 {k}: {'(%.3f, %.3f)' % v if v else '未找到'}")
    return fr


def choose_pattern() -> str:
    print("请选择目标牌型:")
    for i, key in enumerate(PATTERN_ORDER, 1):
        cn, desc = PATTERNS[key]
        print(f"  {i}. {pad(cn, 6)} {desc}")
    raw = input("输入编号 [1]: ").strip() or "1"
    if raw.isdigit() and 1 <= int(raw) <= len(PATTERN_ORDER):
        return PATTERN_ORDER[int(raw) - 1]
    for key, (cn, _d) in PATTERNS.items():
        if raw in (cn, key):
            return key
    print(f"无效选择 {raw!r}, 默认用顺子")
    return PATTERN_ORDER[0]


def live(args) -> int:
    enable_dpi_awareness()
    target = args.pattern
    pat_cn, pat_desc = PATTERNS[target]

    if getattr(args, "hwnd", None):
        info = window_info(int(args.hwnd))
        if not info["hwnd"]:
            print(f"指定的 hwnd 无效: {args.hwnd}")
            return 2
    else:
        info = find_game_window()
    if not info:
        print("找不到游戏窗口。已尝试: 类名 UnityWndClass / 标题含 Endfield / 进程名 Endfield*.exe。")
        print_window_hint()
        return 2
    hwnd = info["hwnd"]
    print(f"百趣演武 · 自动抽牌  v{__version__}")
    print(f"目标牌型: {pat_cn} —— {pat_desc}")
    print(f"找到窗口: hwnd={hwnd:#x} exe={os.path.basename(info['exe'])!r} "
          f"class={info['cls']!r} title={info['title']!r}")
    if is_elevated_pid(info["pid"]) and not is_elevated_pid(os.getpid()):
        print("[!] 游戏进程是管理员权限, 当前脚本不是; 系统会拦截模拟点击。")
        print("    请右键「以管理员身份运行」run.bat 或 python。")
        return 2
    rect = client_rect(hwnd)
    print(f"客户区 {rect[2]}x{rect[3]} @ ({rect[0]},{rect[1]})")
    if not activate(hwnd):
        print("无法把游戏窗口切到前台, 退出(不点击其它窗口)。")
        return 2
    rect = client_rect(hwnd)
    if rect[2] < 640 or rect[3] < 360:
        print(f"还原后窗口客户区仍为 {rect[2]}x{rect[3]}, 请确认游戏不是最小化/全屏独占。")
        return 2
    vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    if rect[0] < vx or rect[1] < vy or rect[0] + rect[2] > vx + vw or rect[1] + rect[3] > vy + vh:
        print(f"游戏窗口有一部分在屏幕外: 客户区 {rect[2]}x{rect[3]} @ ({rect[0]},{rect[1]})")
        print("请把游戏窗口完整移回屏幕内(或最大化)后再运行。")
        return 2
    print(f"还原后客户区 {rect[2]}x{rect[3]} @ ({rect[0]},{rect[1]})")
    load_templates()
    _APP["hwnd"] = hwnd

    def snap() -> Image.Image:
        return grab(client_rect(hwnd))

    def safe_click(pos) -> bool:
        try:
            click_norm(*pos)
            return True
        except RuntimeError as e:
            print(f"[!] {e}")
            return False

    def do_abandon(fr):
        """放弃挑战 -> 确认 -> 等回到抽牌界面。返回 (成功?, 返回码)。"""
        pos = fr.buttons.get("abandon")
        if not pos:
            # 可能是翻牌动画中途, 重新截一帧再找一次
            fr = inspect(snap())
            pos = fr.buttons.get("abandon")
        if not pos:
            print("[!] 没找到「放弃挑战」按钮, 停止。")
            return False, 7
        if not safe_click(pos):
            return False, 3
        fr2, ok = wait_frame(snap, lambda f_: f_.buttons.get("confirm") is not None,
                             CONFIRM_WAIT)
        if not ok:
            print("[!] 放弃确认弹窗没出现, 重试一次")
            if not safe_click(pos):
                return False, 3
            fr2, ok = wait_frame(snap, lambda f_: f_.buttons.get("confirm") is not None,
                                 CONFIRM_WAIT)
            if not ok:
                print("[!] 放弃弹窗仍未出现, 停止。")
                return False, 8
        if not safe_click(fr2.buttons["confirm"]):
            return False, 3
        _, ok = wait_frame(
            snap,
            lambda f_: f_.revealed == 0 and f_.buttons.get("draw") is not None,
            RESET_WAIT)
        if not ok:
            print("[!] 确认后没有回到抽牌界面, 停止。")
            return False, 9
        return True, 0

    shot = snap()
    fr = inspect(shot)
    print(f"初始: 手牌 {fr.revealed}/{HAND_SIZE} -> {fmt_cards(fr.cards)}")
    dump = getattr(args, "dump", None)
    if dump:
        shot.save(dump)
        print(f"已保存窗口截图: {dump}")
        if not args.check:
            return 0
    if args.check:
        cmd_check(shot)
        return 0
    print("─" * 56)

    t_start = time.time()
    round_no = 0
    abandons = 0

    while True:
        round_no += 1
        if args.rounds and round_no > args.rounds:
            break
        prev_slots = None
        card_cache = {}
        prev_cards: List[Card] = []
        low_since = None
        stalls = 0
        no_draw = 0
        last_draw_t = 0.0

        while True:
            if user32.GetForegroundWindow() != hwnd:
                print("游戏窗口不在前台, 停止。")
                return 3
            fr = inspect(snap(), prev_slots, cache=card_cache)
            prev_slots = fr.slots
            if fr.revealed == 0:
                card_cache.clear()   # 桌面清空 = 这一局重置了, 别复用旧牌
            if fr.buttons.get("confirm"):
                if not safe_click(fr.buttons["confirm"]):
                    return 3
                continue

            # 同一局里牌只增不减: 某一帧漏识别不能让张数倒退;
            # 但如果持续 0.8s 都变少(说明这一局确实重置了), 才接受新结果
            if len(fr.cards) >= len(prev_cards):
                prev_cards = fr.cards
                low_since = None
            else:
                if low_since is None:
                    low_since = time.time()
                elif time.time() - low_since > 0.8:
                    prev_cards = fr.cards
                    low_since = None
            cards = prev_cards
            n = len(cards)
            action = decide(cards, target)

            if action == "stop":
                print("─" * 56)
                cost = time.time() - t_start
                print(f"[命中] 第 {round_no} 轮  {n}/{HAND_SIZE}  {fmt_cards(cards)}")
                print(f"       牌型 {pat_cn} 达成; 用时 {cost/60:.1f} 分钟, "
                      f"共 {round_no} 轮, 放弃 {abandons} 次。")
                print("       已停手, 手牌保留; 需要你自己点「开始挑战」。")
                return 0

            if action == "abandon":
                # 放弃不可逆: 先复查一帧, 防止翻牌动画/识别抖动误判
                reason = (f"抽满 5 张仍未达成{pat_cn}" if n >= HAND_SIZE
                          else f"已抽 {n} 张, {pat_cn}已不可能")
                fr2 = inspect(snap(), prev_slots, cache=card_cache)
                prev_slots = fr2.slots
                if (len(fr2.cards) >= n and fr2.cards
                        and decide(fr2.cards, target) != "abandon"):
                    print(f"[~] 复查后不放弃({fmt_cards(fr2.cards)}), 继续")
                    prev_cards = fr2.cards
                    continue
                print(f"[轮 {round_no:>3}] {n}/{HAND_SIZE}  {pad(fmt_cards(cards), 26)} "
                      f"-> 放弃: {reason}")
                ok, code = do_abandon(fr)
                if not ok:
                    return code
                abandons += 1
                break

            pos = fr.buttons.get("draw")
            if not pos:
                if fr.revealed >= HAND_SIZE:
                    # 手牌已经满了, 抽牌按钮当然没有了; 有牌没认出来就放弃重抽
                    print(f"[轮 {round_no:>3}] 手牌已满但只认出 {len(cards)} 张 -> 放弃重抽")
                    ok, code = do_abandon(fr)
                    if not ok:
                        return code
                    abandons += 1
                    break
                no_draw += 1
                if no_draw > 18:
                    print(f"[!] 没找到可用的抽牌按钮 (已翻开 {fr.revealed}/{HAND_SIZE})")
                    print("    可能原因: 牌库为空 / 不在抽牌界面 / 窗口被挡住。")
                    ok, code = do_abandon(fr)
                    if not ok:
                        return code
                    abandons += 1
                    break
                continue
            no_draw = 0
            gap = (time.perf_counter() - last_draw_t) if last_draw_t else 0.0
            print(f"[轮 {round_no:>3}] {n}/{HAND_SIZE}  {pad(fmt_cards(cards), 26)} -> 继续"
                  + (f" (上一抽 {gap:.1f}s)" if gap else ""))
            before = fr.revealed
            before_teal = fr.teal
            if not safe_click(pos):
                return 3
            last_click = time.perf_counter()
            last_draw_t = last_click
            deadline = last_click + DRAW_WAIT
            got_new = False
            while time.perf_counter() < deadline:
                fr2 = inspect(snap(), prev_slots, cache=card_cache)
                prev_slots = fr2.slots
                if fr2.buttons.get("confirm"):
                    safe_click(fr2.buttons["confirm"])
                    got_new = True
                    break
                if fr2.revealed > before or fr2.teal < before_teal:
                    got_new = True
                    break
            if got_new:
                stalls = 0
            else:
                stalls += 1
                print(f"[!] 抽牌后未检测到新翻开的牌 (重试 {stalls}/3)")
                if stalls >= 3:
                    print("[!] 连续 3 次抽牌无反应, 放弃本局重抽")
                    ok, code = do_abandon(fr)
                    if not ok:
                        return 5
                    abandons += 1
                    break

    print(f"达到设定轮数 {args.rounds}, 仍未达成{pat_cn}, 停止。")
    return 10


def main():
    ap = argparse.ArgumentParser(
        prog="baiqu_yanyu",
        description="百趣演武自动抽牌: 按选定牌型抽牌, 不可能就放弃重抽。",
    )
    ap.add_argument("--check", action="store_true", help="只截图识别, 不点击")
    ap.add_argument("--image", metavar="PATH", help="离线分析一张截图")
    ap.add_argument("-p", "--pattern", metavar="牌型",
                    help="目标牌型: " + " / ".join(f"{k}({PATTERNS[k][0]})" for k in PATTERN_ORDER))
    ap.add_argument("-r", "--rounds", type=int, default=0,
                    help="最多跑多少轮, 0=无限制(默认)")
    ap.add_argument("--hwnd", type=lambda s: int(s, 0),
                    help="手动指定窗口句柄(如 0x123456)")
    ap.add_argument("--dump", metavar="PATH", help="把脚本看到的画面存成 PNG")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args()

    if args.image:
        cmd_check(Image.open(args.image).convert("RGB"))
        return 0

    if args.pattern:
        raw = args.pattern.strip()
        key = None
        for k, (cn, _d) in PATTERNS.items():
            if raw == k or raw == cn:
                key = k
                break
        if key is None:
            print(f"未知牌型: {raw}")
            print("可选: " + " / ".join(f"{k}({PATTERNS[k][0]})" for k in PATTERN_ORDER))
            return 2
        args.pattern = key
    elif args.check:
        args.pattern = PATTERN_ORDER[0]
    else:
        args.pattern = choose_pattern()

    try:
        return live(args)
    except KeyboardInterrupt:
        print("\n用户中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
