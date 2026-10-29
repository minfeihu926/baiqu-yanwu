#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百趣演武 · 自动抽牌(第一轮)

每张牌识别 点数(1-5) + 花色(万/条/筒), 按你选的目标牌型判断:

  顺子      抽满 5 张, 其中出现四连(1-2-3-4 或 2-3-4-5)即中
  同花      抽到 4 张同花色即停(不必抽满 5 张)
  同花顺    抽满 5 张, 五张同花色且出现四连
  四条      抽满 5 张, 有 4 张同点数(五条也算)
  葫芦      抽满 5 张, 3 张同点 + 2 张同点
  三条      抽满 5 张, 有 3 张同点数
  两对      抽满 5 张, 两个不同点数各至少 2 张

中途只要判定"目标已不可能", 立即「放弃挑战」→「确认」→ 重抽;
凑成目标则停手、打印结果、脚本退出(不点「开始挑战」)。
轮数默认无上限, 随时 Ctrl+C 结束。

目标窗口: 终末地, 窗口模式, 运行时必须在前台。
依赖: numpy, pillow
许可证: MIT

用法:
  python baiqu_yanyu.py                       # 菜单选目标牌型
  python baiqu_yanyu.py -p straight           # 顺子
  python baiqu_yanyu.py -p 同花 -r 10          # 同花, 最多 10 轮
  python baiqu_yanyu.py --check               # 只截图识别, 不点击
  python baiqu_yanyu.py --image x.png         # 离线分析一张截图
"""
import argparse
import ctypes
import os
import sys
import time
import unicodedata
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageGrab

__version__ = "1.1.5"

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

# 搜索区(相对单张卡牌矩形): cx, cy, 半宽, 半高 —— 与原版 v1.0 一致, 模板在整个区内滑动
RANK_ROI = (0.1104, 0.1250, 0.097, 0.100)     # 点数(16:9 / 16:10 都覆盖)
SUIT_ROI = (0.2985, 0.1450, 0.080, 0.085)     # 花色(小字形, 16:9 / 16:10 都覆盖)

REF_CARD_H = 452.0             # 模板截图里的卡牌高度(像素), 用来把模板换算到当前卡牌大小
SCALES = (0.80, 0.90, 1.00, 1.10, 1.20)       # 原版的多尺度
MATCH_MIN = 0.75               # 与模板的 NCC 下限
MATCH_MARGIN = 0.05            # 第一名与第二名的差距下限

# 单张卡牌应占窗口的比例(由 2560x1600 截图测得), 用来剔除错误的卡槽
CARD_W_RANGE = (0.10, 0.17)
CARD_H_RANGE = (0.21, 0.35)
CARD_ASPECT = 452 / 335        # 16:10 卡牌 高/宽 (实测)

REVEAL_WHITE = 0.15            # 卡牌"已翻开"判定: 白色像素占比阈值
REVEAL_DARK = 0.02             # 同时要有深色笔画, 防止翻牌中途误判

DRAW_WAIT = 3.0                # 点抽牌后等待翻牌的时间
CONFIRM_WAIT = 3.0             # 点放弃后等待弹窗的时间
RESET_WAIT = 8.0               # 点确认后等待回到抽牌界面的时间
CLICK_SETTLE = 0.05            # 两次点击之间的间隔
UNKNOWN_GIVE_UP = 25           # 识别不确定连续多少帧后放弃本局

SLOT_PITCH_RATIO = 1.0985      # 卡牌间距 / 卡牌宽度 (368/335)

# 找不到 5 张青色卡背时的兜底位置(相对窗口)
# 16:9 窗口(实测 1280x720): 卡面 x0=108/1280, y0=160/720, 宽166, 高250, 间距184
FALLBACK_169 = [(0.0844 + i * 0.14375, 0.2222, 0.1300, 0.3472) for i in range(5)]

FALLBACK_SLOTS = [
    (0.0836, 0.2500, 0.1309, 0.2831),
    (0.2273, 0.2500, 0.1309, 0.2831),
    (0.3711, 0.2500, 0.1309, 0.2831),
    (0.5148, 0.2500, 0.1309, 0.2831),
    (0.6586, 0.2500, 0.1309, 0.2831),
]

# 按钮搜索区域与颜色(比例坐标), 数值来自 2560x1600 截图
BTN_SPECS = {
    # name: (x0, y0, x1, y1, color, min_area_ratio)
    "draw":     (0.80, 0.66, 1.00, 0.90, "cyan",   0.0025),
    "abandon":  (0.56, 0.84, 0.84, 0.995, "red",    0.0015),
    "start":    (0.70, 0.895, 0.94, 0.995, "white", 0.0015),
    "confirm":  (0.38, 0.50, 0.80, 0.76, "yellow",  0.0015),
}

SUITS = ("wan", "tiao", "tong")
SUIT_CN = {"wan": "万", "tiao": "条", "tong": "筒"}
RANKS = (1, 2, 3, 4, 5)

# 目标牌型: key -> (中文名, 说明)
PATTERNS = {
    "straight": ("顺子", "五张内出现四连(1-2-3-4 或 2-3-4-5)"),
    "flush": ("同花", "抽到四张同花色即停"),
    "straight_flush": ("同花顺", "五张同花色, 且点数恰好是 1-2-3-4-5"),
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
    """按 [类名+标题] -> [进程名 Endfield*.exe] -> [Unity 窗口] 打分找窗口。

    最小化的游戏窗口也会被选中(随后会自动还原); 返回窗口信息 dict 或 None。
    """
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
        # 普通窗口优先; 最小化的窗口只有在强匹配(进程/类名+标题)时才要
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
    print("  (完整列表请运行: python tools/probe_windows.py)")


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


# ---------------------------------------------------------------- 卡牌识别

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
    slots = []
    for x0, x1 in runs_of(cols > thr, min_len=max(10, int(W * 0.05))):
        # 单张卡背宽度应约为窗口宽的 13%; 翻牌过程中相邻两张青色可能连成一片,
        # 这种过宽的 run 必须丢掉, 否则会推出离谱的卡槽尺寸(逐像素搜索会卡死)
        if not (CARD_W_RANGE[0] * W <= (x1 - x0 + 1) <= CARD_W_RANGE[1] * W):
            continue
        sub = teal[:, x0:x1 + 1]
        rows = sub.sum(axis=1)
        ys = np.nonzero(rows > max(4, (x1 - x0) * 0.15))[0]
        if len(ys) < 20:
            continue
        # 卡背底部有青色反光, 直接取 ys.max() 会把卡槽拉高; 按卡牌固定长宽比重算
        top = int(ys.min())
        w = int(x1) - int(x0) + 1
        aspect = 1.506 if W / H > 1.65 else CARD_ASPECT   # 16:9 卡牌更高\n        h = int(round(w * aspect))
        if not (CARD_H_RANGE[0] * H <= h <= CARD_H_RANGE[1] * H):
            continue
        slots.append(Slot(int(x0), top, int(x0) + w, top + h))
    slots.sort(key=lambda s: s.x0)
    return slots


def fallback_slots(img: Image.Image):
    W, H = img.size
    table = FALLBACK_169 if W / H > 1.65 else FALLBACK_SLOTS
    return [Slot(int(fx * W), int(fy * H),
                 int((fx + fw) * W), int((fy + fh) * H))
            for fx, fy, fw, fh in table]


def infer_slots(img: Image.Image, teal, prev_slots):
    """5 张青色卡背 -> 直接用; 部分翻开 -> 按等间距推; 都没有 -> 用兜底。"""
    W, H = img.size
    if len(teal) == HAND_SIZE:
        return teal
    if len(teal) >= 1:
        anchor = min(teal, key=lambda s: s.x0)
        # 上一帧已经有准确卡槽(通常来自 5 张卡背那一帧) -> 只按位移平移, 别重新推间距
        if prev_slots is not None and len(prev_slots) == HAND_SIZE:
            idx0 = min(range(HAND_SIZE), key=lambda i: abs(prev_slots[i].x0 - anchor.x0))
            ref = prev_slots[idx0]
            # 翻牌动画里露出的青色区域大小/位置都会变, 尺寸不对就直接忽略, 守住旧卡槽
            if not (0.85 * ref.w <= anchor.w <= 1.15 * ref.w):
                return prev_slots
            dx = anchor.x0 - ref.x0
            dy = anchor.y0 - ref.y0
            if abs(dx) <= W * 0.01 and abs(dy) <= H * 0.01:
                return prev_slots
            if abs(dx) <= W * 0.03 and abs(dy) <= H * 0.01:
                return [Slot(s.x0 + dx, s.y0 + dy, s.x1 + dx, s.y1 + dy)
                        for s in prev_slots]
            return prev_slots
        w = float(np.median([s.w for s in teal]))
        y0 = int(np.median([s.y0 for s in teal]))
        y1 = int(np.median([s.y1 for s in teal]))
        pitch = w * SLOT_PITCH_RATIO
        fb = fallback_slots(img)
        idx = min(range(HAND_SIZE), key=lambda i: abs(fb[i].x0 - anchor.x0))
        x0 = anchor.x0 - idx * pitch
        if 0 <= x0 <= W * 0.92:
            slots = []
            for i in range(HAND_SIZE):
                left = int(round(x0 + i * pitch))
                slots.append(Slot(left, y0, int(round(left + w)), y1))
            return slots
    if prev_slots is not None and len(prev_slots) == HAND_SIZE:
        return prev_slots
    return fallback_slots(img)


def box_of(s: Slot, spec) -> Tuple[int, int, int, int]:
    """取样框(相对卡牌): cx, cy, 半宽, 半高 -> 像素坐标。"""
    cx, cy, hw, hh = spec
    x0 = s.x0 + (cx - hw) * s.w
    y0 = s.y0 + (cy - hh) * s.h
    x1 = s.x0 + (cx + hw) * s.w
    y1 = s.y0 + (cy + hh) * s.h
    return int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))


def is_revealed(a: np.ndarray, s: Slot) -> bool:
    """卡牌左上角既要是白底, 又要有已渲染的深色点数/文字, 才算"翻开的牌"。

    只看白底会把翻牌动画中途的空白牌面也算进去, 导致误判。
    """
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
    """载入点数/花色模板(灰度)。"""
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


def _match(patch: np.ndarray, tpl: Image.Image, size):
    """模板缩放到取样尺寸后, 在 patch 内滑动取最高 NCC。

    滑动窗口会自动吸收卡槽推算带来的几像素偏差(窗口化时 16:9/16:10 的间距差异),
    不需要卡槽完全对准。
    """
    tw, th = size
    if tw < 6 or th < 6 or tw > 240 or th > 240:
        return 0.0, 0, 0
    if patch.shape[0] < th + 1 or patch.shape[1] < tw + 1:
        return 0.0, 0, 0
    t = np.asarray(tpl.resize((tw, th), Image.LANCZOS), dtype=np.float32)
    tz = t - float(t.mean())
    tn = float(np.sqrt((tz * tz).sum())) + 1e-6

    win = np.lib.stride_tricks.sliding_window_view(patch, (th, tw))
    stride = 2 if max(win.shape[0], win.shape[1]) > 12 else 1
    win = win[::stride, ::stride]
    wc = win - win.mean(axis=(2, 3), keepdims=True)
    den = np.sqrt((wc * wc).sum(axis=(2, 3))) * tn + 1e-6
    sc = (wc * tz).sum(axis=(2, 3)) / den
    k = np.unravel_index(int(np.argmax(sc)), sc.shape)
    best = float(sc[k])
    bx, by = int(k[1]) * stride, int(k[0]) * stride

    if stride > 1:  # 粗搜步长 2, 再在最佳点 ±1 内细搜
        for oy in range(max(0, by - 1), min(patch.shape[0] - th, by + 2) + 1):
            for ox in range(max(0, bx - 1), min(patch.shape[1] - tw, bx + 2) + 1):
                w2 = patch[oy:oy + th, ox:ox + tw]
                a2 = w2 - float(w2.mean())
                d2 = float(np.sqrt((a2 * a2).sum())) * tn + 1e-6
                s2 = float((a2 * tz).sum() / d2)
                if s2 > best:
                    best, bx, by = s2, ox, oy
    return max(best, 0.0), bx, by


def _best(scores: Dict[str, float]) -> Tuple[Optional[str], float, float]:
    """返回 (最佳键, 最佳分, 与次佳的差距)。"""
    if not scores:
        return None, 0.0, 0.0
    ordered = sorted(scores.items(), key=lambda kv: -kv[1])
    key, score = ordered[0]
    margin = score - ordered[1][1] if len(ordered) > 1 else 1.0
    return key, float(score), float(margin)


def _score_template(patch, tpl, target_h, scales=SCALES) -> float:
    """模板按 target_h 缩放, 在 patch 内滑动取最高 NCC(原版做法)。"""
    ph, pw = patch.shape[:2]
    best = 0.0
    for f in scales:
        th = int(round(target_h * f))
        tw = int(round(tpl.width * th / tpl.height))
        if th < 6 or tw < 6 or th >= ph or tw >= pw:
            continue
        best = max(best, _match(patch, tpl, (tw, th))[0])
    return best


def read_card(gray: np.ndarray, s: Slot) -> Card:
    """读取一张已翻开的牌: 点数 + 花色, 认不准的项记为 None。"""
    H, W = gray.shape[:2]
    if not (CARD_W_RANGE[0] * W <= s.w <= CARD_W_RANGE[1] * W
            and CARD_H_RANGE[0] * H <= s.h <= CARD_H_RANGE[1] * H):
        return Card(None, None)

    tpl = load_templates()
    rb = box_of(s, RANK_ROI)
    rpatch = gray[max(0, rb[1]):min(H, rb[3]), max(0, rb[0]):min(W, rb[2])]
    sb = box_of(s, SUIT_ROI)
    spatch = gray[max(0, sb[1]):min(H, sb[3]), max(0, sb[0]):min(W, sb[2])]

    rank_scores, suit_scores = {}, {}
    for r in RANKS:
        t = tpl[f"r{r}"]
        th = t.height / REF_CARD_H * s.h
        # 先按原尺寸比一次, 分数已经够高就不再多尺度搜索
        sc = _score_template(rpatch, t, th, (1.0,))
        if sc < 0.82:
            sc = max(sc, _score_template(rpatch, t, th))
        rank_scores[str(r)] = sc
    for s_ in SUITS:
        t = tpl[f"s_{s_}"]
        th = t.height / REF_CARD_H * s.h
        sc = _score_template(spatch, t, th, (1.0,))
        if sc < 0.82:
            sc = max(sc, _score_template(spatch, t, th))
        suit_scores[s_] = sc

    rk, rs, rm = _best(rank_scores)
    sk, ss, sm = _best(suit_scores)
    rank = int(rk) if (rk and rs >= MATCH_MIN and rm >= MATCH_MARGIN) else None
    suit = sk if (sk and ss >= MATCH_MIN and sm >= MATCH_MARGIN) else None
    return Card(rank, suit, rs, ss)


def analyze_slots(img: Image.Image, prev_slots=None, a=None, gray=None):
    """返回 (slots, cards)。cards 只包含已翻开且识别成功的牌(按槽位顺序)。"""
    if a is None:
        a = np.asarray(img.convert("RGB")).astype(np.int16)
    if gray is None:
        gray = np.asarray(img.convert("L")).astype(np.float32)
    teal = detect_teal_slots(img, a)
    slots = infer_slots(img, teal, prev_slots)
    cards = []
    for s in slots:
        s.revealed = is_revealed(a, s)
        if not s.revealed:
            s.card = None
            continue
        old_card = s.card
        c = read_card(gray, s)
        if not c.known and old_card is not None and old_card.known:
            # 这张牌之前已经认准过, 别因为一帧抖动又变成"不确定"
            c = old_card
        s.card = c
        if c is not None and c.known:
            cards.append(c)
    return slots, cards


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


def has_flush4(cards: List[Card]) -> bool:
    return bool(suit_counts(cards)) and max(suit_counts(cards).values()) >= 4


def has_four(cards: List[Card]) -> bool:
    return bool(rank_counts(cards)) and max(rank_counts(cards).values()) >= 4


def has_full_house(cards: List[Card]) -> bool:
    cs = sorted(rank_counts(cards).values(), reverse=True)
    return len(cs) >= 2 and cs[0] >= 3 and cs[1] >= 2


def has_three(cards: List[Card]) -> bool:
    return bool(rank_counts(cards)) and max(rank_counts(cards).values()) >= 3


def has_two_pair(cards: List[Card]) -> bool:
    return sum(1 for v in rank_counts(cards).values() if v >= 2) >= 2


def is_hit(cards: List[Card], target: str) -> bool:
    """当前牌面是否已达成目标(包含语义: 更大的牌型也算)。"""
    if target == "flush":
        # 绝对同色: 已抽的牌必须全是同一花色; 凑到 4 张即停
        return len(cards) >= 4 and len(suit_counts(cards)) == 1
    if len(cards) < HAND_SIZE:
        return False
    if target == "straight":
        return has_straight4(cards)
    if target == "straight_flush":
        # 五张同花色 + 点数恰好 1-2-3-4-5(五张顺子)
        return (len(suit_counts(cards)) == 1
                and set(rank_counts(cards)) == {1, 2, 3, 4, 5})
    if target == "four":
        return has_four(cards)
    if target == "full_house":
        return has_full_house(cards)
    if target == "three":
        return has_three(cards)
    if target == "two_pair":
        return has_two_pair(cards)
    raise ValueError(f"未知牌型: {target}")


def can_complete(cards: List[Card], target: str) -> bool:
    """手牌不变的前提下, 后面的空位是否还有可能凑成目标(保守: 不看牌库剩余)。"""
    if is_hit(cards, target):
        return True
    rem = HAND_SIZE - len(cards)
    if rem <= 0:
        return False
    rc = rank_counts(cards)
    max_rank = max(rc.values()) if rc else 0
    sc = suit_counts(cards)
    max_suit = max(sc.values()) if sc else 0

    if target == "flush":
        # 绝对同色: 已经出现第二种花色就不可能
        if len(sc) > 1:
            return False
        return sum(sc.values()) + rem >= 4
    if target == "straight":
        have = set(rc)
        return any(len([r for r in w if r not in have]) <= rem
                   for w in STRAIGHT_WINDOWS)
    if target == "straight_flush":
        # 五张同花色 + 恰好 1-5 各一张: 花色不齐或出现重复点数都不可能
        if len(sc) > 1:
            return False
        if any(v > 1 for v in rc.values()):
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
                if q == r:
                    continue
                need2 = max(0, 2 - rc.get(q, 0))
                if need3 + need2 <= rem:
                    return True
        return False
    if target == "two_pair":
        pairs = sum(1 for v in rc.values() if v >= 2)
        singles = sum(1 for v in rc.values() if v == 1)
        if pairs >= 2:
            return True
        if pairs == 1:
            need = 1 if singles > 0 else 2
            return need <= rem
        need = 2 if singles >= 2 else (3 if singles == 1 else 4)
        return need <= rem
    raise ValueError(f"未知牌型: {target}")


def decide(cards: List[Card], target: str) -> str:
    """返回 stop(已达成) / abandon(不可能) / draw(继续抽)。"""
    if is_hit(cards, target):
        return "stop"
    if not can_complete(cards, target):
        return "abandon"
    return "draw"


def fmt_cards(cards: List[Card]) -> str:
    return " ".join(str(c) for c in cards) if cards else "-"


def _disp_width(text: str) -> int:
    """终端显示宽度(中文按 2 列算)。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _disp_width(text))


# ---------------------------------------------------------------- 状态与动作

def save_dump(img: Image.Image, tag: str) -> None:
    """出错时自动把脚本看到的画面存下来, 方便排查(不会影响游戏)。"""
    try:
        name = f"shot_{tag}_{time.strftime('%H%M%S')}.png"
        img.save(name)
        print(f"    已保存当时的画面: {os.path.abspath(name)}")
    except Exception as e:  # noqa: BLE001
        print(f"    保存截图失败: {e}")


@dataclass
class Frame:
    slots: list
    cards: List[Card]
    revealed: int
    buttons: dict = field(default_factory=dict)
    unknown: int = 0
    overlay: float = 0.0     # 手牌区域被别的窗口遮住的黑色占比


def overlay_ratio(a: np.ndarray, slots) -> float:
    """手牌区域里"几乎纯黑"的占比: 控制台/其它窗口挡在游戏前面时会很高。"""
    if not slots:
        return 0.0
    H, W = a.shape[:2]
    x0 = min(s.x0 for s in slots)
    x1 = max(s.x1 for s in slots)
    y0 = min(s.y0 for s in slots)
    y1 = max(s.y1 for s in slots)
    x0, x1 = max(0, x0), min(W, x1)
    y0, y1 = max(0, y0), min(H, y1)
    if x1 - x0 < 10 or y1 - y0 < 10:
        return 0.0
    p = a[y0:y1, x0:x1]
    dark = (p[..., 0] < 35) & (p[..., 1] < 35) & (p[..., 2] < 35)
    return float(dark.mean())


def inspect(img: Image.Image, prev_slots=None, with_start: bool = False) -> Frame:
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    gray = np.asarray(img.convert("L")).astype(np.float32)
    slots, cards = analyze_slots(img, prev_slots, a=a, gray=gray)
    revealed = sum(1 for s in slots if s.revealed)
    names = ("draw", "abandon", "confirm") + (("start",) if with_start else ())
    buttons = {name: find_button(a, name) for name in names}
    return Frame(slots, cards, revealed, buttons,
                 unknown=max(0, revealed - len(cards)),
                 overlay=overlay_ratio(a, slots))


def wait_frame(grabber, pred, timeout, prev_slots=None, interval=0.06):
    """轮询截图直到 pred(Frame) 为真; 返回 (Frame, True) 或 (最后一帧, False)。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        fr = inspect(grabber(), prev_slots)
        last = fr
        if pred(fr):
            return fr, True
        time.sleep(interval)
    return last, False


# ---------------------------------------------------------------- 主流程

def cmd_check(img: Image.Image):
    load_templates()
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
    for name, (cn, _desc) in PATTERNS.items():
        print(f"  {pad(cn, 6)} {'可达成' if can_complete(fr.cards, name) else '不可能'}"
              f" / {'已达成' if is_hit(fr.cards, name) else '-'}")
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

    state = {"abandons": 0}

    def abandon_round(reason: str) -> bool:
        """放弃当前这一局并确认, 等到重新回到抽牌界面。失败返回 False。"""
        fr_a = inspect(snap())
        pos = fr_a.buttons.get("abandon")
        if not pos:
            print(f"[!] {reason}, 但没找到「放弃挑战」按钮。")
            return False
        print(f"[轮 {round_no:>3}] 放弃: {reason}")
        if not safe_click(pos):
            return False
        fr_b, ok = wait_frame(snap, lambda f_: f_.buttons.get("confirm") is not None,
                              CONFIRM_WAIT)
        if not ok:
            print("[!] 放弃确认弹窗没出现, 重试一次")
            if not safe_click(pos):
                return False
            fr_b, ok = wait_frame(snap, lambda f_: f_.buttons.get("confirm") is not None,
                                  CONFIRM_WAIT)
            if not ok:
                print("[!] 放弃弹窗仍未出现。")
                return False
        if not safe_click(fr_b.buttons["confirm"]):
            return False
        state["abandons"] += 1
        _, ok = wait_frame(
            snap,
            lambda f_: f_.revealed == 0 and f_.buttons.get("draw") is not None,
            RESET_WAIT)
        if not ok:
            print("[!] 确认后没有回到抽牌界面。")
            return False
        return True

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

    # 开局桌上已经有牌也没关系, 直接接着判断/抽牌, 不主动放弃
    if fr.revealed > 0:
        print(f"开局已有 {fr.revealed} 张手牌, 接着判断")

    while True:
        round_no += 1
        if args.rounds and round_no > args.rounds:
            break
        prev_slots = None
        prev_cards: List[Card] = []
        stalls = 0
        no_draw = 0
        unknown_streak = 0
        last_draw_t = 0.0
        printed_n = -1
        debug_n = -1

        while True:
            if user32.GetForegroundWindow() != hwnd:
                print("游戏窗口不在前台, 停止。")
                return 3
            fr = inspect(snap(), prev_slots)
            prev_slots = fr.slots
            if fr.overlay > 0.25:
                # 手牌区域被别的窗口(常见是控制台)挡住, 这时候识别结果不可信, 等它让开
                now = time.time()
                if now - state.get("overlay_warn", 0) > 3:
                    state["overlay_warn"] = now
                    print(f"[!] 手牌区域有 {fr.overlay*100:.0f}% 被其它窗口遮住, "
                          f"识别暂停。请把控制台/其它窗口移开游戏画面。")
                time.sleep(0.5)
                continue
            if fr.buttons.get("confirm"):
                if not safe_click(fr.buttons["confirm"]):
                    return 3
                time.sleep(0.8)
                continue

            if fr.unknown:
                unknown_streak += 1
                if unknown_streak > UNKNOWN_GIVE_UP:
                    for i, s in enumerate(fr.slots):
                        if s.revealed and (s.card is None or not s.card.known):
                            print(f"    [!] 槽位 #{i+1} 认不准 "
                                  f"(点数 {s.card.rank_score if s.card else 0:.2f} / "
                                  f"花色 {s.card.suit_score if s.card else 0:.2f})")
                    print(f"[轮 {round_no:>3}] 有 {fr.unknown} 张牌识别不确定 -> 放弃本局重抽")
                    if not abandon_round("识别不确定"):
                        save_dump(snap(), "unknown")
                        return 6
                    break
                time.sleep(0.12)
                continue
            unknown_streak = 0

            # 新翻开的牌再复检一次, 两次读数一致才采信(防翻牌动画期间的误识别)
            if len(fr.cards) > len(prev_cards):
                sig = tuple((c.rank, c.suit) for c in fr.cards)
                time.sleep(0.12)
                fr2 = inspect(snap(), fr.slots)
                prev_slots = fr2.slots
                if fr2.unknown or tuple((c.rank, c.suit) for c in fr2.cards) != sig:
                    time.sleep(0.06)
                    continue
                fr = fr2

            # 同一轮内牌只增不减: 识别抖动时沿用上一帧结果
            if len(fr.cards) >= len(prev_cards):
                cards = fr.cards
                prev_cards = cards
            else:
                cards = prev_cards

            n = len(cards)
            if args.debug and n != debug_n:
                debug_n = n
                detail = " ".join(
                    f"#{i+1}:{s.card}({s.card.rank_score:.2f}/{s.card.suit_score:.2f})"
                    for i, s in enumerate(fr.slots)
                    if s.revealed and s.card is not None)
                print(f"      [调试] {detail}")
            action = decide(cards, target)

            if action == "stop":
                print("─" * 56)
                cost = time.time() - t_start
                print(f"[命中] 第 {round_no} 轮  {n}/{HAND_SIZE}  {fmt_cards(cards)}")
                print(f"       牌型 {pat_cn} 达成; 用时 {cost/60:.1f} 分钟, "
                      f"共 {round_no} 轮, 放弃 {state['abandons']} 次。")
                print("       已停手, 手牌保留; 需要你自己点「开始挑战」。")
                return 0

            if action == "abandon":
                if n >= HAND_SIZE:
                    reason = f"抽满 5 张仍未达成{pat_cn}"
                else:
                    reason = f"已抽 {n} 张, {pat_cn}已不可能"
                # 放弃是不可逆操作: 多给一点时间复查(翻牌动画/识别抖动), 确认后才放弃
                reverted = False
                for _ in range(3):
                    time.sleep(0.30)
                    fr2 = inspect(snap(), prev_slots)
                    prev_slots = fr2.slots
                    if fr2.unknown or len(fr2.cards) < len(cards):
                        continue
                    if decide(fr2.cards, target) != "abandon":
                        print(f"[~] 复查后放弃条件不成立({fmt_cards(fr2.cards)}), 继续")
                        prev_cards = fr2.cards
                        reverted = True
                        break
                if reverted:
                    continue
                print(f"[轮 {round_no:>3}] {n}/{HAND_SIZE}  {pad(fmt_cards(cards), 26)} "
                      f"-> {reason}")
                if not abandon_round(reason):
                    save_dump(snap(), "abandon")
                    return 7
                break  # 进入下一轮

            # 继续抽一张
            pos = fr.buttons.get("draw")
            if not pos:
                no_draw += 1
                if no_draw > 25:
                    print(f"[!] 没找到可用的抽牌按钮 (已翻开 {fr.revealed}/{HAND_SIZE})")
                    print("    可能原因: 牌库为空 / 不在抽牌界面 / 分辨率或 UI 缩放不匹配。")
                    if abandon_round("抽牌按钮不可用"):
                        break
                    save_dump(snap(), "nodraw")
                    return 4
                time.sleep(0.12)
                continue
            no_draw = 0
            if n != printed_n:
                print(f"[轮 {round_no:>3}] {n}/{HAND_SIZE}  {pad(fmt_cards(cards), 26)} -> 继续")
                printed_n = n
            before = fr.revealed
            if not safe_click(pos):
                return 3
            last_click = time.perf_counter()
            last_draw_t = last_click
            deadline = last_click + DRAW_WAIT
            got_new = False
            while time.perf_counter() < deadline:
                time.sleep(0.05)
                fr2 = inspect(snap(), prev_slots)
                prev_slots = fr2.slots
                if fr2.buttons.get("confirm"):
                    safe_click(fr2.buttons["confirm"])
                    time.sleep(0.5)
                    got_new = True
                    break
                if fr2.revealed > before:
                    got_new = True
                    time.sleep(0.12)  # 等卡面/点数渲染稳定
                    break
                # 点击可能没被接收(按钮还在动画/置灰): 按钮仍可见就补点一次
                p2 = fr2.buttons.get("draw")
                if p2 and (time.perf_counter() - last_click) >= 0.7:
                    if not safe_click(p2):
                        return 3
                    last_click = time.perf_counter()
            if got_new:
                stalls = 0
            else:
                stalls += 1
                print(f"[!] 抽牌后未检测到新翻开的牌 (重试 {stalls}/3)")
                if stalls >= 3:
                    print("[!] 连续 3 次抽牌无反应")
                    if abandon_round("抽牌无响应"):
                        break
                    save_dump(snap(), "stall")
                    return 5

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
    ap.add_argument("--debug", action="store_true", help="打印每张牌的识别分数")
    ap.add_argument("--dump", metavar="PATH",
                    help="把脚本看到的窗口画面存成 PNG(排查识别问题用)")
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
        # --check 只看识别结果, 不需要目标牌型
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
