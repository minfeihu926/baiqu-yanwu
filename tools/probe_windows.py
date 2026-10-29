#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断工具: 列出所有可见顶层窗口 + 可能的游戏进程, 用于排查"找不到游戏窗口"。

用法:
  python tools/probe_windows.py
  python tools/probe_windows.py > probe.txt

把输出发到 issue 里即可。
"""
import ctypes
import os
import sys
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x8
TOKEN_ELEVATION = 20


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


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


def is_elevated(pid: int) -> bool:
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


def is_cloaked(hwnd) -> bool:
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
        cloaked = wintypes.DWORD(0)
        dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked),
                                     ctypes.sizeof(cloaked))  # DWMWA_CLOAKED
        return bool(cloaked.value)
    except Exception:
        return False


def list_windows():
    rows = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rc = RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rc))
        w, h = rc.right - rc.left, rc.bottom - rc.top
        exe = proc_exe(pid.value)
        rows.append({
            "hwnd": hwnd,
            "title": title,
            "cls": cls.value,
            "pid": pid.value,
            "exe": os.path.basename(exe),
            "path": exe,
            "w": w,
            "h": h,
            "min": bool(user32.IsIconic(hwnd)),
            "cloaked": is_cloaked(hwnd),
            "elevated": is_elevated(pid.value),
        })
        return True

    user32.EnumWindows(EnumProc(_cb), 0)
    return rows


def list_processes():
    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    out = []
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == ctypes.c_void_p(-1).value:
        return out
    try:
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(pe)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            out.append((pe.th32ProcessID, pe.szExeFile))
            ok = kernel32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        kernel32.CloseHandle(snap)
    return out


def main():
    lines = []
    lines.append("=== 所有可见顶层窗口 ===")
    rows = list_windows()
    for r in sorted(rows, key=lambda x: -(x["w"] * x["h"])):
        lines.append(
            f"hwnd={r['hwnd']:#x} pid={r['pid']} exe={r['exe']!r} "
            f"class={r['cls']!r} title={r['title']!r} "
            f"client={r['w']}x{r['h']} minimized={r['min']} "
            f"cloaked={r['cloaked']} elevated={r['elevated']}")

    lines.append("")
    lines.append("=== 名称可能相关的进程 ===")
    keys = ("endfield", "终末地", "unity", "maa", "mxu")
    for pid, name in list_processes():
        low = name.lower()
        if any(k in low for k in keys):
            lines.append(f"pid={pid} name={name!r} exe={proc_exe(pid)!r} "
                         f"elevated={is_elevated(pid)}")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
