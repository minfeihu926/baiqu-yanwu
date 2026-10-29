#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百趣演武 · 自动抽牌(第一轮)

逻辑(一张一张抽):
  - 抽到第 n 张时统计点数 5 的个数 f;
  - 若 f < n-1(剩下 5-n 个位置全出 5 也凑不够 4 张), 立即「放弃挑战」→「确认」→ 重抽;
  - 抽满 5 张且 f >= 4 -> 停手, 手牌保留给你, 脚本退出;
  - 抽满 5 张仍不足 4 张 -> 放弃重抽。
不点「开始挑战」, 不参与战斗。
目标窗口: 终末地, 窗口化(1280x720 / 1280x800 均可), 运行时必须在前台。

依赖: numpy, pillow
许可证: MIT

用法:
  python baiqu_yanyu.py --check            # 只截图识别, 不点击(首次建议)
  python baiqu_yanyu.py                    # 正式运行
  python baiqu_yanyu.py --image x.png --check   # 离线分析一张截图
"""
import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageGrab

__version__ = "1.0.0"

if getattr(sys, "frozen", False):
    # PyInstaller onefile: 资源解压在 sys._MEIPASS
    BASE = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_FIVE = os.path.join(BASE, "templates", "digit5.png")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

# ---------------------------------------------------------------- 可调参数

WINDOW_CLASS = "UnityWndClass"
WINDOW_TITLE = "Endfield"

HAND_SIZE = 5          # 手牌张数
DIGIT_FIVE_THRESHOLD = 0.75   # 数字 5 模板匹配阈值(0~1)
FIVE_BOX_MEAN = 0.25   # 卡牌"已翻开"判定: 左上角白色像素占比阈值
MAX_ROUNDS = 60        # 最多重抽多少轮
DRAW_WAIT = 3.0        # 点抽牌后等待翻牌的时间
CONFIRM_WAIT = 3.0     # 点放弃后等待弹窗的时间
RESET_WAIT = 8.0       # 点确认后等待回到抽牌界面的时间
CLICK_SETTLE = 0.05    # 两次点击之间的间隔

# 卡牌几何: 以 2560x1600 截图测量, 全部用比例表示
SLOT_DX = 0.1075       # 点数中心相对卡牌左边的位置
SLOT_DY = 0.1082       # 点数中心相对卡牌上边的位置
SLOT_HW = 0.090        # 搜索半径(相对卡牌宽)
SLOT_HH = 0.085        # 搜索半径(相对卡牌高)
SLOT_PITCH_RATIO = 1.0985  # 卡牌间距 / 卡牌宽度 (368/335)

# 找不到 5 张青色卡背时的兜底位置(相对窗口)
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


# ---------------------------------------------------------------- 图像分析

@dataclass
class Slot:
    x0: int
    y0: int
    x1: int
    y1: int
    revealed: bool = False
    five: bool = False
    score: float = 0.0

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
        if (x1 - x0) > W * 0.45:
            continue
        sub = teal[:, x0:x1 + 1]
        rows = sub.sum(axis=1)
        ys = np.nonzero(rows > max(4, (x1 - x0) * 0.15))[0]
        if len(ys) < 20:
            continue
        slots.append(Slot(int(x0), int(ys.min()), int(x1), int(ys.max())))
    slots.sort(key=lambda s: s.x0)
    return slots


def fallback_slots(img: Image.Image):
    W, H = img.size
    return [Slot(int(fx * W), int(fy * H),
                 int((fx + fw) * W), int((fy + fh) * H))
            for fx, fy, fw, fh in FALLBACK_SLOTS]


def digit_roi(s: Slot):
    cx = s.x0 + SLOT_DX * s.w
    cy = s.y0 + SLOT_DY * s.h
    hw = SLOT_HW * s.w
    hh = SLOT_HH * s.h
    return int(cx - hw), int(cy - hh), int(cx + hw), int(cy + hh)


def is_revealed(a: np.ndarray, s: Slot) -> bool:
    """卡牌左上角既要是白底, 又要有已渲染的深色点数/文字, 才算"翻开的牌"。

    只看白底会把翻牌动画中途的空白牌面也算进去, 导致点数为 0 的误判。
    """
    x0, y0, x1, y1 = digit_roi(s)
    H, W = a.shape[:2]
    x0, x1 = max(0, x0), min(W, x1)
    y0, y1 = max(0, y0), min(H, y1)
    if x1 - x0 < 5 or y1 - y0 < 5:
        return False
    patch = a[y0:y1, x0:x1]
    R, G, B = patch[..., 0], patch[..., 1], patch[..., 2]
    white = (R > 205) & (G > 200) & (B > 200)
    dark = (R < 115) & (G < 115) & (B < 115)
    return float(white.mean()) > FIVE_BOX_MEAN and float(dark.mean()) > 0.05


_TEMPLATE_CACHE = {}


def load_template():
    if "tpl" not in _TEMPLATE_CACHE:
        tpl = Image.open(TEMPLATE_FIVE).convert("L")
        _TEMPLATE_CACHE["tpl"] = tpl
    return _TEMPLATE_CACHE["tpl"]


def _resize_gray(tpl: Image.Image, size) -> np.ndarray:
    return np.asarray(tpl.resize(size, Image.LANCZOS), dtype=np.float32)


def match_five(a_gray: np.ndarray, s: Slot) -> float:
    """在卡牌左上角搜索数字 5, 返回最高 NCC 分数。"""
    x0, y0, x1, y1 = digit_roi(s)
    H, W = a_gray.shape[:2]
    x0, x1 = max(0, x0), min(W, x1)
    y0, y1 = max(0, y0), min(H, y1)
    patch = a_gray[y0:y1, x0:x1]
    if patch.shape[0] < 10 or patch.shape[1] < 10:
        return 0.0
    tpl = load_template()
    target_h = 0.1236 * s.h
    base = target_h / tpl.height
    best = 0.0
    for factor in (0.80, 0.90, 1.00, 1.10, 1.20):
        s_ = base * factor
        tw = max(6, int(round(tpl.width * s_)))
        th = max(8, int(round(tpl.height * s_)))
        if th >= patch.shape[0] or tw >= patch.shape[1]:
            continue
        t = _resize_gray(tpl, (tw, th))
        tz = t - t.mean()
        tn = float(np.sqrt((tz * tz).sum())) + 1e-6
        win = np.lib.stride_tricks.sliding_window_view(patch, (th, tw))
        wm = win.mean(axis=(2, 3), keepdims=True)
        wc = win - wm
        den = np.sqrt((wc * wc).sum(axis=(2, 3))) * tn + 1e-6
        score = (wc * tz).sum(axis=(2, 3)) / den
        best = max(best, float(score.max()))
    return best


def infer_slots(img: Image.Image, teal, prev_slots):
    """5 张青色卡背 -> 直接用; 部分翻开 -> 按等间距推; 都没有 -> 用兜底。"""
    W, H = img.size
    if len(teal) == HAND_SIZE:
        return teal
    if len(teal) >= 1:
        w = float(np.median([s.w for s in teal]))
        y0 = int(np.median([s.y0 for s in teal]))
        y1 = int(np.median([s.y1 for s in teal]))
        pitch = w * SLOT_PITCH_RATIO
        anchor = min(teal, key=lambda s: s.x0)
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


def analyze_slots(img: Image.Image, prev_slots=None, a=None, gray=None):
    """返回 (slots, revealed_count, fives_count)。"""
    if a is None:
        a = np.asarray(img.convert("RGB")).astype(np.int16)
    if gray is None:
        gray = np.asarray(img.convert("L")).astype(np.float32)
    teal = detect_teal_slots(img, a)
    slots = infer_slots(img, teal, prev_slots)
    revealed = 0
    fives = 0
    for s in slots:
        s.revealed = is_revealed(a, s)
        s.score = match_five(gray, s) if s.revealed else 0.0
        s.five = s.revealed and s.score >= DIGIT_FIVE_THRESHOLD
        revealed += int(s.revealed)
        fives += int(s.five)
    return slots, revealed, fives


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


# ---------------------------------------------------------------- 状态与动作

@dataclass
class Frame:
    slots: list
    revealed: int
    fives: int
    buttons: dict = field(default_factory=dict)


def inspect(img: Image.Image, prev_slots=None, with_start: bool = False) -> Frame:
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    gray = np.asarray(img.convert("L")).astype(np.float32)
    slots, revealed, fives = analyze_slots(img, prev_slots, a=a, gray=gray)
    names = ("draw", "abandon", "confirm") + (("start",) if with_start else ())
    buttons = {name: find_button(a, name) for name in names}
    return Frame(slots, revealed, fives, buttons)


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
    fr = inspect(img, with_start=True)
    print(f"窗口图像: {img.size[0]}x{img.size[1]}")
    print(f"识别到卡槽: {len(fr.slots)}")
    for i, s in enumerate(fr.slots):
        print(f"  #{i+1} 翻开={s.revealed} 是5={s.five} 分数={s.score:.3f}")
    print(f"已翻开: {fr.revealed}  点数5: {fr.fives}")
    for k, v in fr.buttons.items():
        print(f"按钮 {k}: {'(%.3f, %.3f)' % v if v else '未找到'}")
    return fr


def decide_action(n: int, f: int) -> str:
    """已抽 n 张、其中点数 5 有 f 张时的决策: draw / abandon / stop。

    - 抽满 5 张: f>=4 停手, 否则放弃;
    - 未满: f < n-1 说明剩余位置全出 5 也凑不够 4 张, 立即放弃;
    - 其它情况继续抽(即使第 4 张就已经 4 个 5, 也按你的要求抽满 5 张)。
    """
    if n >= HAND_SIZE:
        return "stop" if f >= 4 else "abandon"
    if f < n - 1:
        return "abandon"
    return "draw"


def live(args) -> int:
    enable_dpi_awareness()
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
    title = info["title"]
    print(f"找到窗口: hwnd={hwnd:#x} exe={os.path.basename(info['exe'])!r} "
          f"class={info['cls']!r} title={title!r}")
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

    prev_slots = None
    fr = inspect(snap())
    print(f"初始: 手牌 {fr.revealed}/{HAND_SIZE}, 5 的数量 {fr.fives}, "
          f"按钮 {[k for k, v in fr.buttons.items() if v]}")
    if args.check:
        cmd_check(snap())
        return 0

    for round_no in range(1, args.rounds + 1):
        prev_slots = None
        stalls = 0
        no_draw = 0
        prev_n = 0
        prev_f = 0
        last_draw_t = 0.0
        while True:
            if user32.GetForegroundWindow() != hwnd:
                print("游戏窗口不在前台, 停止。")
                return 3
            fr = inspect(snap(), prev_slots)
            prev_slots = fr.slots
            if fr.buttons.get("confirm"):
                if not safe_click(fr.buttons["confirm"]):
                    return 3
                time.sleep(0.8)
                continue

            n_raw, f_raw = fr.revealed, fr.fives
            if f_raw < prev_f:
                scores = " ".join(f"#{i+1}:{'5' if s.five else '-'}({s.score:.2f})"
                                  for i, s in enumerate(fr.slots))
                print(f"[!] 本帧 5 只读到 {f_raw} 张, 已确认过 {prev_f} 张 | {scores}")
            # 同一轮内牌只增不减: 已翻开数和已确认的 5 都取历史最大值, 防抖
            n = max(n_raw, prev_n)
            f = max(f_raw, prev_f)
            prev_n, prev_f = n, f
            action = decide_action(n, f)
            reason = None
            if action == "stop":
                print(f"[OK] 抽满 {HAND_SIZE} 张, 其中 5 有 {f} 张; 手牌已保留, 脚本退出。")
                return 0
            if action == "abandon":
                if n >= HAND_SIZE:
                    reason = f"抽满 {HAND_SIZE} 张只有 {f} 张 5"
                else:
                    reason = f"已抽 {n} 张只有 {f} 张 5(至少需要 {n - 1} 张)"

            if reason:
                # 放弃是不可逆操作: 先复查一帧, 防止翻牌动画/识别抖动误判
                time.sleep(0.20)
                fr2 = inspect(snap(), prev_slots)
                prev_slots = fr2.slots
                n2 = max(fr2.revealed, n)
                f2 = max(fr2.fives, f)
                act2 = decide_action(n2, f2)
                if act2 == "stop":
                    print(f"[OK] 复查: 抽满 5 张, 其中 5 有 {f2} 张; 手牌已保留, 脚本退出。")
                    return 0
                if act2 == "draw":
                    print(f"[~] 复查后: 已抽 {n2} 张, 5 有 {f2} 张 -> 不放弃, 继续抽")
                    fr, n, f = fr2, n2, f2
                    prev_n, prev_f = n2, f2
                    reason = None
                else:
                    scores = " ".join(f"#{i+1}:{'5' if s.five else '-'}({s.score:.2f})"
                                      for i, s in enumerate(fr2.slots))
                    print(f"    复查确认放弃 | {scores}")
                    fr = fr2

            if reason:
                pos = fr.buttons.get("abandon")
                if not pos:
                    print("[!] 判定该放弃, 但没找到「放弃挑战」按钮, 停止。")
                    return 7
                print(f"[x] {reason}, 放弃重抽")
                if not safe_click(pos):
                    return 3
                fr2, ok = wait_frame(snap, lambda f_: f_.buttons.get("confirm") is not None,
                                     CONFIRM_WAIT, prev_slots)
                if not ok:
                    print("[!] 放弃确认弹窗没出现, 重试一次")
                    if not safe_click(pos):
                        return 3
                    fr2, ok = wait_frame(snap, lambda f_: f_.buttons.get("confirm") is not None,
                                         CONFIRM_WAIT, prev_slots)
                    if not ok:
                        print("[!] 放弃弹窗仍未出现, 停止。")
                        return 8
                if not safe_click(fr2.buttons["confirm"]):
                    return 3
                fr3, ok = wait_frame(
                    snap,
                    lambda f_: f_.revealed == 0 and f_.buttons.get("draw") is not None,
                    RESET_WAIT, prev_slots)
                if not ok:
                    print("[!] 确认后没有回到抽牌界面, 停止。")
                    return 9
                break  # 进入下一轮

            # 继续抽一张
            pos = fr.buttons.get("draw")
            if not pos:
                # 抽牌按钮可能还在翻牌动画/置灰, 稍等后重新判断(不要直接报错)
                no_draw += 1
                if no_draw > 25:
                    print(f"[!] 没找到可用的抽牌按钮 (已翻开 {fr.revealed}/{HAND_SIZE})")
                    print("    可能原因: 牌库为空 / 不在抽牌界面 / 分辨率或 UI 缩放不匹配。")
                    return 4
                time.sleep(0.12)
                continue
            no_draw = 0
            before = fr.revealed
            gap = (time.perf_counter() - last_draw_t) if last_draw_t else 0.0
            print(f"[轮次 {round_no}] 已抽 {n} 张, 5 有 {f} 张 -> 继续抽"
                  + (f" (上一抽耗时 {gap:.2f}s)" if gap else ""))
            if not safe_click(pos):
                return 3
            last_click = time.perf_counter()
            last_draw_t = last_click
            deadline = last_click + DRAW_WAIT
            got_new = False
            fr2 = fr
            while time.perf_counter() < deadline:
                time.sleep(0.05)
                fr2 = inspect(snap(), prev_slots)
                prev_slots = fr2.slots
                if fr2.buttons.get("confirm"):
                    if not safe_click(fr2.buttons["confirm"]):
                        return 3
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
            prev_f = f
            if got_new:
                stalls = 0
            else:
                stalls += 1
                print(f"[!] 抽牌后未检测到新翻开的牌 (重试 {stalls}/3)")
                if stalls >= 3:
                    print("连续 3 次抽牌无反应, 停止。")
                    return 5

    print(f"达到最大轮数 {args.rounds}, 仍未凑够 4 张 5, 停止。")
    return 10


def main():
    ap = argparse.ArgumentParser(
        prog="baiqu_yanyu",
        description="百趣演武自动抽牌: 抽满 5 张, 至少 4 张点数 5; 否则放弃重抽。",
    )
    ap.add_argument("--check", action="store_true", help="只截图识别, 不点击")
    ap.add_argument("--image", metavar="PATH", help="离线分析一张截图")
    ap.add_argument("--rounds", type=int, default=MAX_ROUNDS, help="最多放弃重抽轮数")
    ap.add_argument("--hwnd", type=lambda s: int(s, 0),
                    help="手动指定窗口句柄(如 0x123456)")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = ap.parse_args()

    if args.image:
        img = Image.open(args.image).convert("RGB")
        cmd_check(img)
        return 0
    if not os.path.exists(TEMPLATE_FIVE):
        print(f"缺少模板: {TEMPLATE_FIVE}")
        return 2
    try:
        return live(args)
    except KeyboardInterrupt:
        print("\n用户中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
