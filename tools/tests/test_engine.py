# -*- coding: utf-8 -*-
"""牌型判定/可达性 单元测试(不依赖游戏窗口)。"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)

from baiqu_yanwu import Card, decide, is_hit, can_complete  # noqa: E402

W, T, G = "wan", "tiao", "tong"


def C(text):
    """'4w 2t 5g' -> [Card...]"""
    m = {"w": W, "t": T, "g": G}
    out = []
    for tok in text.split():
        out.append(Card(int(tok[0]), m[tok[1]]))
    return out


CASES = [
    # (牌, 目标, 期望 is_hit, 期望 can_complete)
    ("4w 2t 5t 5w 3g", "straight", True, True),
    ("4w 2t 5t 5w 3g", "flush", False, False),
    ("4w 2t 5t 5w 3g", "two_pair", False, False),
    ("4w 3w 2w 1w", "flush", True, True),
    ("4w 3w 2w", "flush", False, True),
    ("4w 3w 2t", "flush", False, False),   # 绝对同色: 混色直接放弃
    ("4w 3w 2t 1g", "flush", False, False),
    ("1w 2t 3g 4w", "straight", False, True),
    ("1w 2t 3g 5w", "straight", False, True),
    ("1w 2t 4g 5w", "straight", False, True),   # 差一张 3, 仍可补
    ("1w 2t 3g 4w 5t", "straight", True, True),
    ("2w 2t 2g 3w", "three", False, True),
    ("2w 2t 2g 3w 4t", "three", True, True),
    ("2w 2t 2g 3w 3t", "full_house", True, True),
    ("2w 2t 2g 3w 4t", "full_house", False, False),  # 5 张 3+1+1 葫芦不可达
    ("2w 2t 2g 3w 3t", "two_pair", True, True),
    ("2w 2t 2g 3w 4t", "two_pair", False, False),
    ("2w 2t 2g 2w", "four", False, True),
    ("2w 2t 2g 2w 3t", "four", True, True),
    ("2w 2t 2g 2w 2t", "four", True, True),          # 五条算四条
    ("1w 2w 3w 4w", "straight_flush", False, True),
    ("1w 2w 3w 4w 5w", "straight_flush", True, True),
    ("1w 2w 3w 4w 5t", "straight_flush", False, False),
    ("1w 2t 3w 4w 5w", "straight_flush", False, False),
    ("1w 2w 3w 4t", "straight_flush", False, False),
    ("1w 2w 3w 4w 4w", "straight_flush", False, False),   # 有点数重复 -> 五张顺子不成立
]


def main():
    fails = 0
    for text, target, hit, comp in CASES:
        cards = C(text)
        h = is_hit(cards, target)
        c = can_complete(cards, target)
        ok = (h == hit and c == comp)
        if not ok:
            fails += 1
            print(f"FAIL {text:<18} {target:<14} is_hit={h}(期望{hit}) "
                  f"can_complete={c}(期望{comp})")
    # decide 的早期放弃
    checks = [
        ("1w 1t", "straight", "draw"),
        ("1w 2t", "straight", "draw"),
        ("1w 2t 3g 4w", "straight", "draw"),
        ("1w 2t 3g 5w", "straight", "draw"),
        ("4w 3w 2w 1w", "flush", "stop"),
        ("4w 3w 2t 1g", "flush", "abandon"),
        ("5w 5t 5g", "three", "draw"),
        ("5w 5t 5g 5w 2t", "four", "stop"),
        ("5w 5t 5g 2w 2t", "full_house", "stop"),
        ("5w 5t 5g 2w 3t", "full_house", "abandon"),
        ("5w 5t 2w 2t 3g", "two_pair", "stop"),
        ("5w 5t 5g 2w 3t", "two_pair", "abandon"),
        ("4w 2t 5t 5w 3g", "straight", "stop"),
    ]
    for text, target, want in checks:
        got = decide(C(text), target)
        if got != want:
            fails += 1
            print(f"FAIL decide({text!r}, {target}) = {got}, 期望 {want}")
    print(f"用例 {len(CASES) + len(checks)} 个, 失败 {fails} 个")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
