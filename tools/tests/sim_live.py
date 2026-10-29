# -*- coding: utf-8 -*-
"""用假的游戏状态驱动 live()，验证抽牌/放弃/确认的点击流程(不接触真实游戏)。"""
import argparse
import io
import os
import sys
from contextlib import redirect_stdout

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)

import baiqu_yanwu as m  # noqa: E402

CARD = {"w": "wan", "t": "tiao", "g": "tong"}
DRAW_POS = (0.90, 0.75)
ABANDON_POS = (0.60, 0.93)
CONFIRM_POS = (0.50, 0.60)


def cards(text):
    return [m.Card(int(t[0]), CARD[t[1]]) for t in text.split()]


class FakeGame:
    def __init__(self, script):
        """script: 每轮的抽牌序列(list of str)."""
        self.script = script
        self.round = -1
        self.hand = []
        self.pos = 0
        self.confirm = False
        self.clicks = []
        self.abandons = 0
        self._next_round()

    def _next_round(self):
        self.round += 1
        self.pos = 0
        self.hand = []

    def draw_list(self):
        if self.round >= len(self.script):
            return []
        return cards(" ".join(self.script[self.round]))

    def frame(self):
        hand = list(self.hand)
        slots = [m.Slot(i * 100, 0, i * 100 + 90, 400) for i in range(5)]
        for i, c in enumerate(hand):
            slots[i].revealed = True
            slots[i].card = c
        buttons = {
            "draw": None if (self.confirm or len(hand) >= 5) else DRAW_POS,
            "abandon": None if self.confirm else ABANDON_POS,
            "confirm": CONFIRM_POS if self.confirm else None,
        }
        return m.Frame(slots, hand, len(hand), buttons, 0)

    def click(self, fx, fy):
        for name, pos in (("draw", DRAW_POS), ("abandon", ABANDON_POS),
                          ("confirm", CONFIRM_POS)):
            if abs(fx - pos[0]) < 0.02 and abs(fy - pos[1]) < 0.02:
                self.clicks.append(name)
                self._do(name)
                return
        raise AssertionError(f"点击了未知位置 ({fx:.2f},{fy:.2f})")

    def _do(self, name):
        if name == "draw":
            if len(self.hand) >= 5:
                raise AssertionError("手牌已满还要抽牌")
            seq = self.draw_list()
            if self.pos >= len(seq):
                raise AssertionError(f"第 {self.round+1} 轮抽牌超出脚本")
            self.hand.append(seq[self.pos])
            self.pos += 1
        elif name == "abandon":
            self.confirm = True
        elif name == "confirm":
            self.confirm = False
            self.abandons += 1
            self._next_round()


class FakeUser32:
    def __init__(self, hwnd):
        self.hwnd = hwnd

    def GetForegroundWindow(self):
        return self.hwnd

    def GetSystemMetrics(self, i):
        return {76: 0, 77: 0, 78: 2560, 79: 1600}.get(i, 0)


def run(target, script, verbose=False):
    hwnd = 0x1234
    game = FakeGame(script)
    m.enable_dpi_awareness = lambda: None
    m.find_game_window = lambda *a, **k: {
        "hwnd": hwnd, "title": "Endfield", "cls": "UnityWndClass",
        "pid": 1, "exe": "Endfield.exe", "w": 2560, "h": 1600, "min": False}
    m.is_elevated_pid = lambda pid: False
    m.activate = lambda h: True
    m.client_rect = lambda h: (0, 0, 2560, 1600)
    m.grab = lambda rect: None
    m.inspect = lambda img, prev_slots=None, with_start=False, cache=None: game.frame()
    m.user32 = FakeUser32(hwnd)
    m.time.sleep = lambda s: None
    m.time.perf_counter = lambda: 0.0

    def click_norm(fx, fy):
        game.click(fx, fy)

    m.click_norm = click_norm
    m._APP["hwnd"] = hwnd

    args = argparse.Namespace(pattern=target, rounds=0, check=False,
                              hwnd=None, debug=False, image=None)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = m.live(args)
    if verbose:
        print(buf.getvalue())
    return rc, game, buf.getvalue()


def run_case(target, script, want_clicks):
    rc, game, _out = run(target, script)
    got = " ".join({"draw": "D", "abandon": "A", "confirm": "C"}[c]
                   for c in game.clicks)
    ok = (rc == 0 and got == want_clicks)
    print(f"{'OK  ' if ok else 'FAIL'} {target:<14} 点击[{got}] 期望[{want_clicks}] rc={rc}")
    return ok


if __name__ == "__main__":
    cases = [
        # 顺子: 第 1 轮 1,1,2,3,5 无四连放弃; 第 2 轮 1-2-3-4-5 命中
        ("straight", [["1w 1t 2g 3w 5t"], ["1w 2t 3g 4w 5t"]],
         "D D D D D A C D D D D D"),
        # 同花: 四张同色即停, 不抽第 5 张
        ("flush", [["4w 3w 2w 1w"]], "D D D D"),
        # 同花: 三种花色各一张, 第 3 张就放弃
        ("flush", [["1w 2t 3g"], ["4w 3w 2w 1w"]], "D D A C D D D D"),
        # 四条: 4 张点数全不同, 第 4 张放弃
        ("four", [["1w 2t 3g 4w"], ["5w 5t 5g 5w 2t"]], "D D D A C D D D D D"),
        # 葫芦: 3 张点数不同, 第 3 张放弃
        ("full_house", [["1w 2t 3g"], ["2w 2t 2g 3w 3t"]], "D D D A C D D D D D"),
        # 两对: 5 张内两组对子
        ("two_pair", [["5w 5t 2w 2t 3g"]], "D D D D D"),
        # 同花顺: 四张同花但不是四连, 第 4 张放弃
        ("straight_flush", [["1w 3w 2w 5w 5w"], ["1w 2w 3w 4w 5w"]],
         "D D D D D A C D D D D D"),
    ]
    bad = 0
    for t, s, w in cases:
        if not run_case(t, s, w):
            bad += 1
    print(f"\n用例 {len(cases)} 个, 失败 {bad} 个")
    sys.exit(1 if bad else 0)
