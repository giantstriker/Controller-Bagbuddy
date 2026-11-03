#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Controller Bagbuddy —— 注释版源码

用途：在背包截图上框大区→切格子；激活后热键移鼠。多界面、绿/白区线、屏幕辅助线。
依赖：Python3 + Pillow。
配置：APP_DIR/configs/<名>.json（layouts+hotkeys+cross_region）；
      APP_DIR/active_config.json 记当前配置名；兼容迁入旧 layouts.json/hotkeys.json。

结构：Win32移鼠 → 路径/图标 → 键位 → Region/Layout/compute_region
      → HotkeyDialog/Switcher/ScreenOverlay → App（UI/热键/轮询/多配置存盘）
热键：Tk 与 GetAsyncKeyState 双通道；格子防抖约 0.03s，其它约 0.18s；连跳极快真实滑动。
颜色：当前区绿 #00e676，其它区白。
跨区：右键链接相邻区域后边缘步进可切区；「跨区域移动」为总开关（关=仅环绕，链接成功自动打开）。
格子：选中/框选/改行列后按区域自动计算 cell_w/cell_h。
启动：Windows 下强制管理员权限（无则 UAC 提权重启）。
"""

import json
import math
import os
import sys
import threading
import time
from dataclasses import dataclass, asdict, field
from typing import Optional
from tkinter import (
    Tk, Toplevel, Canvas, Frame, Label, Entry, Button, StringVar,
    Listbox, Scrollbar, Checkbutton, filedialog, messagebox, BOTH, LEFT, RIGHT, X, Y, END,
    IntVar,
)
from tkinter import ttk, simpledialog, Menu

try:
    from PIL import Image, ImageTk, ImageGrab
except ImportError:
    raise SystemExit("请先安装 Pillow：pip install pillow")

# ========== 平台与 Win32 移鼠 ==========
IS_WIN = sys.platform.startswith("win")
if IS_WIN:
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
else:
    user32 = None
    kernel32 = None


if IS_WIN:
    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = (
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        )

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = (
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        )

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = (
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        )

    class _INPUNION(ctypes.Union):
        _fields_ = (("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT))

    class _INPUT(ctypes.Structure):
        _fields_ = (("type", wintypes.DWORD), ("union", _INPUNION))

    class _KBDLLHOOK(ctypes.Structure):
        _fields_ = (
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        )

    class _POINT(ctypes.Structure):
        _fields_ = (("x", ctypes.c_long), ("y", ctypes.c_long))

    def get_cursor():
        pt = _POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def _send_rel(dx, dy):
        inp = _INPUT()
        inp.type = 0
        inp.union.mi.dx = int(dx)
        inp.union.mi.dy = int(dy)
        inp.union.mi.mouseData = 0
        inp.union.mi.dwFlags = 0x0001  # MOUSEEVENTF_MOVE 相对移动
        inp.union.mi.time = 0
        inp.union.mi.dwExtraInfo = None
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))

    def send_cursor(x, y, fast=True):
        """移鼠。始终极快真实滑动：相对移动插值多步，末尾 SetCursorPos 校正。
        fast 保留兼容（默认/True/False 行为相同）；总时长约 8–25ms。"""
        tx, ty = int(x), int(y)
        try:
            x0, y0 = get_cursor()
        except Exception:
            try:
                user32.SetCursorPos(tx, ty)
            except Exception:
                pass
            return
        dx_total = tx - x0
        dy_total = ty - y0
        dist = max(abs(dx_total), abs(dy_total))
        if dist <= 0:
            try:
                user32.SetCursorPos(tx, ty)
            except Exception:
                pass
            return
        # 按距离自适应步数：短 6–10、中 12–18、远 20–28；线性插值，无缓动
        if dist < 80:
            steps = 8
        elif dist < 250:
            steps = 14
        elif dist < 500:
            steps = 22
        else:
            steps = 26
        # 极短间隔，使总时长落在约 8–25ms（含循环开销）
        delay = 0.0005 if steps <= 14 else (0.0006 if steps <= 22 else 0.0007)
        for i in range(1, steps + 1):
            x1 = x0 + dx_total * i / steps
            y1 = y0 + dy_total * i / steps
            try:
                cx, cy = get_cursor()
            except Exception:
                cx, cy = x1, y1
            _send_rel(x1 - cx, y1 - cy)
            if delay > 0:
                time.sleep(delay)
        try:
            user32.SetCursorPos(tx, ty)
        except Exception:
            pass

    LRESULT = ctypes.c_ssize_t
    _LLPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
    try:
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t,
        ]
        user32.CallNextHookEx.restype = LRESULT
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
        ]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
    except Exception:
        pass
else:
    def get_cursor():
        return 0, 0

    def send_cursor(x, y, fast=True):
        pass

# ========== 运行目录 / 配置 / 图标 ==========
def _runtime_dir():
    """EXE/脚本所在目录；打包后切到该目录，保证 layouts.json 等落在当前目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _runtime_dir()
try:
    os.chdir(APP_DIR)
except Exception:
    pass
HOTKEY_FILE = os.path.join(APP_DIR, "hotkeys.json")  # 兼容旧默认热键
LAYOUT_FILE = os.path.join(APP_DIR, "layouts.json")  # 兼容旧布局（迁入 configs）
CONFIGS_DIR = os.path.join(APP_DIR, "configs")
ACTIVE_CONFIG_FILE = os.path.join(APP_DIR, "active_config.json")
DEFAULT_CONFIG_NAME = "default"

ICON_FILE = os.path.join(APP_DIR, "backpack_tool.ico")
ICON_PNG = os.path.join(APP_DIR, "backpack_tool_icon_256.png")
APP_USER_MODEL_ID = "GundamSlave.BackpackGridTool"

# 内嵌小图标：无外部 ico 时仍能设置窗口/任务栏图标，避免落到 Tk 羽毛默认图
EMBEDDED_ICON_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAVRElEQVR4nL2bebQeZX3HP88z8753zb25"
    "yQ0JhCQs2QhEErJAQoIBNEFAAhwUKkUQEWlVant6emy1/tH2tLaeU1ul1bphFTlaPQVRWZRNiQFJCAFZ"
    "ZMlGWBIgyc297z7zPE//eOaZeeZ9515CtX3OuefOO/Msv/33/T3zjFBKGSEErhlj0FoRhiUAtm/fwn0P"
    "3MkTT2zl5Vf2UG800FoDAtDJKIHE2PGAQSCFwRiBSXoa11OAMfZOtq7tZYyx45BgwKSjDMKbxZDR6zch"
    "JH09vRx77CwWL17Gueecz9IlKwGI45ggkAk1IISdS2itjTEmN5GUku3bH+Xr3/wiWx57mFqtRqkUEgQl"
    "pJSFi/uMWJGYcQlFGDDZM5HwZop754cmM9vlTDLYJ0ETxS2iSNHT3cOK5au47tobWbr09ERxpIIXAoRS"
    "ys2GEBJjDDf92+f41rf/nVYU09/fjxABxmjaBeUz/H/TJpg/NanOR0IIpJQorahWKoRBwNUfvIE/+cRf"
    "IYREa42UAiGEtQBryoJWq8WnPv0x7rr7NoaGpiKERCmVrnMkGvr/aUcu+CAIMNpw4OCbnH/eRv7xH75M"
    "udxl3U1KpPV5O+FnPvtJ7rzrNoaHp2MMKfNuyd+N4N/nmCO3OqUU2mimTZvOnXffzqc/e6Od3RgrBK01"
    "QRDyzW/dxB0/+T7Dw9OJoqjA3H+X9r9xk9+va8VxxLRpM7jjxz/kGzd/iSAI0FpZF9i583muuPI8ZBAg"
    "hGhj/sjMTSR9DaIwNoHLAG+PcJFkkyObyw/CmQ05fqS0MU4rxfdvvYfjj5+HFELwzW/dRKVWsf7SMesR"
    "MC+MjezCpbciwkyOYCFoS4NFY4oFZs23kJLcbFIYSNZxig2CkEq1wjduvskGy917dvCLh+5lUv8AOvX5"
    "8dXkM+eujRE2jZmMiE7C/PtWU7HSyUrtY0wyotj6Jk7FTvsGbTpZUSpm0qQBfrnpXl7auwt5//13MTJy"
    "iDAIKc4teXfwJZ/XgpzQVnzLklLSaDQ5ee5sZgwPEcVxPjcLmQKqDuYERxCfnODyAM+1MAwZGTnI/fff"
    "idzy2GaCMMSkwKVdbCJ3LQqcWwgBIlm2yPmT+850tTaUyyX+6PL1lKTA4hPjWdT4caeY97d2W+cGbn4p"
    "Ax7duhm5a/eLlMISRo/HdDsBWT9/0qLnRUSEgWS0Uueic1Zw+qI5jNWbBIGbx/6lV8K5wXjzjY+Giuhw"
    "97QxlEoldu/egRw9PEIQSIyH64+0uQndfyeLcYwAgFYUMzxlgOs2nkWj0USKxHTItNMeSqQotizHY5GI"
    "xrNENzCQASOHDyFVWthM1CayiMy87cLOFTr7BlJSqTe46oI1zDh6iHqj2Wb2+XmNAeMKBY8pGycyut6S"
    "+nHcVitFmN2SFMuSCe7bZ6km/ABZsGC90WTenJlcsWElcaWKlO3ZoV2YLp63u9kE5BRRWOQOyX/Zecsn"
    "5u0tNFGTAppxzEcuOYuBSWVUrBP/ztY1xqCUSmKLT1MiiFQwvx+UaoyhMKFaE+s0u7fbnKlKKanUm6xc"
    "PI/zzzyFxlidIOgMnkEgmdTXSxwrSBCl5dnk1JMhw99NQ0IIWwwVPEoXyZ4f+WKZXzuQpAkCwR9fdjZh"
    "kG2jYLL6vFpvsGTBcfztx99PK447Jz2i9PdWzbe4BB63BwjfBx3vDnwcqelpL6UGUjBabbB+1amsXHwc"
    "9WrDYvJEqy7lCyG5eN0Szly1kHPPWMxYtU6QID5XW0wY2Y+oeZkmIVHa23k/LBwq0u5H4A7ObQSxUgz0"
    "9/HRS9aiWy23it3ZSRir15ucOn8271w2n2hklI+/7xwG+nuJlUoNz7lCRs84gGyc5luyj1+kI8cxluVy"
    "kTNlvD5vFYVdAJNCMFZrcPn6FZwwZ5hGvWELFK0wWiMFhFISKc0VG1bSVZY0qjVOnDWFyzeczlitQSCy"
    "MJVZZD5THIll+H3861wQzIJOZ8XVLsG3boJmK2L20cNc9Z4VRGO1RHgKoxVCGBqtmAOjFU464VjOXT6f"
    "VrVBEEha1SoffM/pzDlmGs1WNCHabL8+Etp8ADdOFhBt/9OhuHTk9ylaNJCSeiviQxeuZupgL60oQhgD"
    "BrTShOWQW+95hJf2HeTK96ykp7dEFEdIDFGkmDLQxXUb11JvRQleyAs3b6GZciaE4gV8FgqgCJUV9XFA"
    "pX0PQApBrdHk1PmzuPisk6mNVWwmNxoVK7q7S/x21z6+/ZPNvGPeLDasXEirUkMK0EohBNTGKlx05kks"
    "W3Q8lWrDQmb8FG2FmRe+HyMKYkKBomT2bPyCInvm/puCe954AdrA9RtX0yUVSmkwGm0UBo2Qkq/d9hAH"
    "Dle47NzTGOwv0YpijFaABq1QSlESihsuWQvJbrVPV2JMb2H2eYEUWUfOApxZFYGf7Fp4mreL2NiR5lXG"
    "ag3WLZvPOxfPZmysjjAKra0g+nu7eWjbi9z98FMsPH4mF65aQKtWR6DRSmO0xhgbIMeqdc5cdDTnrV7E"
    "aK3huYLwaB2v8muD5uN4xjhbK37Ay5j0xJHuqrZ7ltaa3u5urrtwBXHcwiSa10YhgVqjydfu+BX1Zsx7"
    "15zCUUO9NFoxUkC5JFEqtv1VjFExzVqN6y5YwdBAH0rptv1G8bbrgnY+UwFkDPnSMh2S9IWQm8oYAikZ"
    "qzfZuPYU3jFniGq1bjGGMcSxor+3zG2/+A1bn32JOccMs3H1AhrVGmFgOFxt8OTO/XSXQ1QUYVSERFOt"
    "t5g3YxLvP2dpsncg0/WMyZdJ43nC+C6SBMFcKZvkfHeddwvTYW7ZjqugFcXMmDrIVe9aTLVat/11jFaK"
    "rkCy78AY371nK9oY1q9cwOyjBqg2Inp7urhj0zP85VfutNaAQRmNNhopDGOVKlecvYgTZk6j0YzSzRKr"
    "ojz0LmoTZQbpSygDGsVZoH1XNzeRENSaER949xJmTu6i1miBUmilUXFEb3fALT/bxu7XDnLUlAEuXrOQ"
    "Rr1Gd0my97URfvDAE7y07yC33vsE/b1lVKzQscLomFYrYkqP5JrzltGMVAKEOumbCAOM9yiXBdol6RBX"
    "vjTtBElCCOrNiAVzjuKSM+ZyuFJHYt8yxyqmuxzw9J43uf2hpxASzl56IgtmDDJWbdBTltx81xZefXOU"
    "KYM93HrvNna+NkJ3KFGxtR6B4fBYjfNOm82Kk2ZRrbfasIHx/oqZLTICIYwthvx8nh/UWRQVSlFArOGa"
    "9UvoK0EUtdAJ3EXb2HDzndsYqTSY1NfDxavnUavX6esKefyFffxk87MM9JYJhOTg4So337WNchiglcIY"
    "mxmUUsi4ybUbTkV2vL+wGytFllpEd6ZorxxOS9c2iWX1QfHLCCkFlXqL0xcdy7pTZjAyWrEYRWuU0vR1"
    "hTzy7Cs8uH0nUghWnzybk2dNoVZvEsWKL93+KCPVJo3IUGnGDPb3ctevn+fR516lr7tEHCtrBUZxuFJn"
    "2fFDbFgxj7Fak0DmEWtR893F32hxQ0LL7EQVYPqrsI/WmjAMufrckzGtFkppAmMwSYnbUpqb79lu7weS"
    "i1bNI2q1wGiqjSZXr1/C1euXEEqQQcB//PQxtj69h6/f/ThfuP5sjFZok0Hweq3BH569gF89tYd6s0UY"
    "yAmCnCm0Xl/RspNRv2P+r70FUlCpR7x72fGcOmuQ0VrTAhqtiGOr/Z9t28X2F19DCMNZ75jDupNnUgoE"
    "PV0BJaFZftwga+ZP5dQ5k/nRQ0/x9I5XmTLYx5bnXuOerbvoLwdEsUowgKLaaDJrcheXrV1IrRETTFj8"
    "5C3axy3ud+h+vJ3mYkYca4YGevmDtXOp1evJnp5dIAgkh6pNbrnvKcqhRCcCu/nnv+FwtcGx0yaxduHR"
    "xCJmx8Eqn/3OJp7c9TpDfd0obeguBXz7vmc4Y8F0ysIQK4PbSxqt1ti4Yg73bd/D3v0jdJWCcfeG/PLd"
    "D+Y204ksDearOrv742cGn3l7lkdQbUZsXDWXOVN6qNWtWWutiWJFb1fI7Y+8yM5XD1EuBZTDgPu37eLz"
    "P3iEL97+GK2WYnhSF7995RB/+pV7eW7vAaZM6kEbg9aa7nLA7v0j/PBXL9DbFaSo0mhNK1L0hZor1y0g"
    "1hOXwJ3pMl+72CIrRYGO6ewwUbt1uLTYbClmTR/kotNmMjpWRRuDUjFKKcoB7D0wxn9vfoGe7jBhytDX"
    "U6YUSC48/UQ+cNY87ty6kz//2oPsH6nR1122RVPSYmXo7y5x++YX2bHvMF2hJI5jtNIIoxip1Fkzf5jl"
    "82dQbXSWzFaBvuYz+v0mXVXlUkm75IqEK4WgqRSXrTqBwS5BI4rA2AouiiLKYcAPN+3g9ZE6pSBIEWUU"
    "K7rLIR897xRueeAZ/vqWh2nFmq5yiNK6Y50wkIxUm3z3ly8QBgKldFJU2eJKR02uPGse5XKI1sVHsrJX"
    "bsWt7b1AVuF1Mm6twyK+mEWzp7Ju4TCHK3WMtvv5KlZ0lwTPvXqInz2+h/5E+1ZoUG/FXLp6Lg9s38Pf"
    "/9ejdJdDwkBa4gskrbShv6fEg795la073qCnJIkTKzPaMFZvctKMXtYvnU210Q6OZEq3E0R7tZsoM8ul"
    "7UAij/jytfH7zphDqGPiWGGURWxKawTw/U07qDYiAilTv4uUYdpgHzv3Hear9zzFQG8XYF9UCoE7W1II"
    "sbVWfG/TTvu+QNnTajoBWbVqjYuXzWR4ch+tWOXiWD57mZSW3Kv69hecTtPWbE3H/kC1GbFi7lGcNnuA"
    "Sr0FWqcW0FsSbN9ziIef309fTzllDgSBtHB56wv76esupwJN/TT54Qodd19rQ19XiSd2H+TBZ/fTV5ao"
    "WIHWYAyNlmJ6r+TilcfRiBSZERSZfudRvzDTvl/bJ1ox+VdUxmi6u0pcunwmUaNJrAxSgkg2KFpRzPc2"
    "7aDZUonmshMHSS2JQNCKs6N32eykffH6Y0BJicDwvU27WDprkK7AuocL3qPVBucunMovnxli177DdJXC"
    "VLiWX7d95irczDLCvNYzWCxEQkTSMZCCsXrMeacdw9zhMoerLcJAYLRAG0FfV8iWPYfZe6jJMcOT2gTq"
    "u4/zrzbleN0zQWT0hFIyWo/4xfNvcsEp02g2ImRCc6w0fYHg0hXH8vkfjyLc8Ri83F+wvW6MQZx+5lyT"
    "vfVpNxvrNwLrq91dJf7m0kUMlaGlTZJDrZVIKWhqibAmYYkjcZ+EI/+e8VThajGTECeTbS5t3AsUk1qD"
    "1ooeaekxHjdKGfp6u/jCz3fw6+dfp6+rlLigSPnIBOIhwfZ3f+2HHUgIrrcUG1fM4KgeweFaRBCAMgIh"
    "3QksQZc0CGNfbHq7dwmREmGEtQCXcHzglW6zZdZhshxtX6Ul2ohVPjjbbTdoNZpcvPRontp7CO1OAHsO"
    "6BiXibINaQwg13zfEULQUppZw/2cPXeQsXrTakIl2lUSLezZW639Lcb8u4OEhXR722ED39gdGb7mIa9p"
    "jHunaBIg63aIBdUoZvakEutOms6PH3uZge4SypdoOn+SEoHQRenkkZcOTaI/QaQ05y+eRo9UjDU1oQSj"
    "JUIahLDR2II4m4bcMTbt9uy8NXTiMql5pgrKDunYsYmOhLcBm9ufsBUnaZFjW6XeYsNJU9my8wAjlRah"
    "dG6XaN23LmsBPvrzt5qFBS9RzIJjBjltZh+jtQghpMXf0iCUQUiR+K81T4FAaJOmNGcNVgQBCJ1amMsK"
    "jiAhROrrPpHOQpU7TZbKIklrQiMQKGMFO6lbsP6U6fznQ7sZ7AntuHGqpbDT57OjqdpAuRRy+bKjGChB"
    "HYn0agVXMOGwQhLXMo1bFYmO314kEr45O0JMJiAvaeRyuBEWZSbxxSRuozEYrdmwYDJP7h3i6ZdH6AoD"
    "MDKLP6nCTXZGqL3oEUCsDZP7Qp58aYRtu4w9CikywrLzgSJBcdl3HU7n6UmTtiCbsxDh7ot07Rw5zuQ9"
    "2lxc8Dwofa61oSuUlDyM4jJMjkchEKvWzjdxHCd+27ntpY2hHumCcqIYxqTmnaJA+yyfhkRGvHDPDZl1"
    "tMGndhBD9jhbwkXuLMKXAmG17yj1mDNAKAPCyYND7H99X3KS2peOHRBIwUB3iPEXTBO3S2s22OlMzxnT"
    "beAmD7BEGzDKJ2v7ZU2eMeei9hQ5mTv5qA0yd2iLJ3Z6gYojJk+dRnjCCfN4+ZWXKJfLeQmZTHPau2nw"
    "Ccrn6cx0vfCXO4Gah6Eii2beGN9Ps7WMLyC/jyumdMacfagTBYicYKxbSlpRxAnHz0MuP20VWuvCctSV"
    "jy4NZUT4YdUFNC9vtxVW/twZLX51Jjrnc4w6kbbtWnVudrgU6SwlCdY55l1xB0Yrli9bhVy3bgOTJw8R"
    "eyezMsaznOtXaHkpJcsJB3Fd4PMJyqpKvyZ3ON32b8fqaTpJWj6X+XHB9c+SiwuYmfB84cZxzNDgEGe/"
    "cz3yuDknctaad1GpjBIEYTKJM/pMK24h+5cRZ7QtPNwzP8M5ohykdbnb12y2RW3v+z7rYR9PGKZtDYqV"
    "k9KTPRBCEAQBY2OjrFlzLnPmnGj3Az50zcfo7elDqTglqj0bZHm/PU54PdLF/N0YPzAmGMOFD0MazDLB"
    "QrYPke1NuFrAZZjONYtcOHNf1+I4pr+3j2uv+RjGGKTWinlzF3LD9X/GoZGDyYcTvmT9MwIiZS6Tvkgj"
    "ux+k/PSW7PV4Msl+O6vJx4n8KVE7v/PrvLaz+GQ6aGoXShiEjIwc4PqPfJK5c09Ca4W03wbGfPjaT/De"
    "Cy7jjQNvUCqVPYJcSZzfM/BISEGIb7L2unOjMw3s/o3kf96Mi3GEbymOWZ/R/JhsoVKpxBsHXue9F7yP"
    "6z58I0rFSBnYL0cdDm9FLf7iUzdw9z0/YurUaTZfet8OugU63YNx+hRF9uL+jrmJnmfQOS+AzvMNWXMf"
    "gh08+CYb1l/EP33uy5RLdj9SSonQWhnnV1JKtFb887/8Hd/57lcxRtPXNwkpJLrw09m3x0hn85BS+nuC"
    "+b2Lok/pMjqyr8QqlTGklFx91Uf55I2fSQCfTuoZ3KezmS/Zb2olW7Zu5uvf+Fcee/zXNJtNSqUSYVgq"
    "xAudBCTsmOx75PFbJgQbEH2heGt1fHCd4YjcbMYQRS2iKKKrq5sVy1Zx3Yc/wfJlq9HaKjEIZGqhqQX4"
    "+wDGKILAfj6/Zetm7r3vp2x7/FFee22vPfA4jhDezknNDLF5+DEJpjplNNucTTOKcIc2igVQKpWYecws"
    "lixZwbvOuYAVy1cD9nO5PCiz1/8DjBptvYExmqAAAAAASUVORK5CYII="
)



def _resource_path(*parts):
    """运行目录或 PyInstaller 临时目录里找资源。"""
    bases = [APP_DIR]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bases.append(meipass)
    bases.append(os.path.dirname(os.path.abspath(__file__)))
    for base in bases:
        p = os.path.join(base, *parts)
        if os.path.isfile(p):
            return p
    return os.path.join(APP_DIR, *parts)


def _set_windows_app_id():
    if not IS_WIN:
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def _ensure_admin():
    """启动时若无管理员权限，则以 runas 重新拉起自身（UAC 提示）。"""
    if not IS_WIN:
        return
    try:
        if bool(ctypes.windll.shell32.IsUserAnAdmin()):
            return
    except Exception:
        return
    try:
        if getattr(sys, "frozen", False):
            exe = sys.executable
            params = " ".join(f'"{a}"' for a in sys.argv[1:])
        else:
            exe = sys.executable
            args = [os.path.abspath(sys.argv[0]), *sys.argv[1:]]
            params = " ".join(f'"{a}"' for a in args)
        # SW_SHOWNORMAL = 1；失败（用户取消 UAC）则直接退出
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params or None, None, 1)
        if rc <= 32:
            try:
                from tkinter import messagebox
                messagebox.showerror("需要管理员权限", "未能获取管理员权限，程序无法启动。")
            except Exception:
                pass
    except Exception:
        pass
    raise SystemExit(0)





# 名称 -> (宽, 高)
# ========== 分辨率预设 ==========
RES_PRESETS = [
    ("640p (1024×640)", 1024, 640),
    ("720p (1280×720)", 1280, 720),
    ("768p (1366×768)", 1366, 768),
    ("900p (1600×900)", 1600, 900),
    ("1K / 1080p (1920×1080)", 1920, 1080),
    ("2K / 1440p (2560×1440)", 2560, 1440),
    ("4K / 2160p (3840×2160)", 3840, 2160),
    ("超宽 1080 (2560×1080)", 2560, 1080),
    ("超宽 2K (3440×1440)", 3440, 1440),
    ("自定义", 0, 0),
]
RES_PRESET_NAMES = [n for n, _, _ in RES_PRESETS]


def preset_name_for(w, h):
    for name, pw, ph in RES_PRESETS:
        if pw == w and ph == h:
            return name
    return "自定义"


# ========== 键位映射 ==========
KEYSYM_VK = {
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "space": 0x20, "tab": 0x09, "escape": 0x1B, "return": 0x0D,
    "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "prior": 0x21, "next": 0x22,
    "plus": 0xBB, "minus": 0xBD, "equal": 0xBB,
    "bracketleft": 0xDB, "bracketright": 0xDD,
    "comma": 0xBC, "period": 0xBE, "slash": 0xBF, "backslash": 0xDC,
    "semicolon": 0xBA, "apostrophe": 0xDE, "grave": 0xC0,
}
for i in range(1, 13):
    KEYSYM_VK[f"f{i}"] = 0x6F + i
for i in range(10):
    KEYSYM_VK[str(i)] = 0x30 + i
    KEYSYM_VK[f"numpad{i}"] = 0x60 + i
    KEYSYM_VK[f"kp_{i}"] = 0x60 + i
for c in "abcdefghijklmnopqrstuvwxyz":
    KEYSYM_VK[c] = ord(c.upper())

DISPLAY_NAME = {
    "left": "←", "right": "→", "up": "↑", "down": "↓",
    "prior": "PageUp", "next": "PageDown",
    "return": "Enter", "escape": "Esc",
    "bracketleft": "[", "bracketright": "]",
    "numpad0": "小键盘0", "numpad1": "小键盘1", "numpad2": "小键盘2",
    "numpad3": "小键盘3", "numpad4": "小键盘4", "numpad5": "小键盘5",
    "numpad6": "小键盘6", "numpad7": "小键盘7", "numpad8": "小键盘8",
    "numpad9": "小键盘9",
    "kp_0": "小键盘0", "kp_1": "小键盘1", "kp_2": "小键盘2",
    "kp_3": "小键盘3", "kp_4": "小键盘4", "kp_5": "小键盘5",
    "kp_6": "小键盘6", "kp_7": "小键盘7", "kp_8": "小键盘8",
    "kp_9": "小键盘9",
}


def norm_keysym(sym: str) -> str:
    s = (sym or "").lower()
    aliases = {
        "kp_left": "left", "kp_right": "right", "kp_up": "up", "kp_down": "down",
        "kp_enter": "return", "kp_add": "plus", "kp_subtract": "minus",
    }
    return aliases.get(s, s)


def vk_of(keysym: str) -> int:
    s = norm_keysym(keysym)
    if s in KEYSYM_VK:
        return KEYSYM_VK[s]
    if len(s) == 1:
        return ord(s.upper())
    return 0


def pretty_key(keysym: str) -> str:
    s = norm_keysym(keysym)
    if not s:
        return ""
    if s in DISPLAY_NAME:
        return DISPLAY_NAME[s]
    if s.startswith("f") and s[1:].isdigit():
        return s.upper()
    return s.upper() if len(s) == 1 else s


def tk_seq(keysym: str) -> str:
    s = norm_keysym(keysym)
    special = {
        "left": "<Left>", "right": "<Right>", "up": "<Up>", "down": "<Down>",
        "space": "<space>", "tab": "<Tab>", "escape": "<Escape>",
        "return": "<Return>", "backspace": "<BackSpace>",
        "delete": "<Delete>", "insert": "<Insert>",
        "home": "<Home>", "end": "<End>", "prior": "<Prior>", "next": "<Next>",
        "bracketleft": "[", "bracketright": "]",
        "plus": "<plus>", "minus": "<minus>",
    }
    if s in special:
        return special[s]
    if s.startswith("f") and s[1:].isdigit():
        return f"<{s.upper()}>"
    if len(s) == 1:
        return s
    return f"<KeyPress-{s}>"


# ========== 热键表 ==========
HOTKEY_ACTIONS = [
    ("cell_left", "朝左一格"),
    ("cell_right", "朝右一格"),
    ("cell_up", "朝上一格"),
    ("cell_down", "朝下一格"),
    ("prev_region", "上个区域"),
    ("next_region", "下个区域"),
    ("toggle_mouse", "开/关移鼠"),
    ("prev_layout", "上一界面"),
    ("next_layout", "下一界面"),
    ("cell_home", "回到当前区第1格"),
    ("cell_again", "再移到当前格"),
    ("toggle_overlay", "开/关屏幕辅助线"),
    ("toggle_global_lock", "全局锁定"),
    # 以下仅保留兼容旧 hotkeys.json，不再出现在底栏
    ("region_left", "（旧）区左"),
    ("region_right", "（旧）区右"),
    ("region_up", "（旧）区上"),
    ("region_down", "（旧）区下"),
]

DEFAULT_KEYS = {
    "cell_left": "left",
    "cell_right": "right",
    "cell_up": "up",
    "cell_down": "down",
    "prev_region": "prior",
    "next_region": "next",
    "toggle_mouse": "",
    "prev_layout": "",
    "next_layout": "",
    "cell_home": "",
    "cell_again": "",
    "toggle_overlay": "",
    "toggle_global_lock": "",
    "region_left": "",
    "region_right": "",
    "region_up": "",
    "region_down": "",
}


def load_hotkeys():
    keys = dict(DEFAULT_KEYS)
    try:
        with open(HOTKEY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in data.items():
                if k in keys and isinstance(v, str):
                    keys[k] = norm_keysym(v) if v else ""
    except Exception:
        pass
    # 兼容旧配置：区左/区右 → 上个/下个区域；若格子键为空则承接旧的区方向键
    if not keys.get("prev_region") and keys.get("region_left"):
        keys["prev_region"] = keys["region_left"]
    if not keys.get("next_region") and keys.get("region_right"):
        keys["next_region"] = keys["region_right"]
    for cell_act, region_act, fallback in (
        ("cell_left", "region_left", "left"),
        ("cell_right", "region_right", "right"),
        ("cell_up", "region_up", "up"),
        ("cell_down", "region_down", "down"),
    ):
        if not keys.get(cell_act):
            keys[cell_act] = keys.get(region_act) or fallback
    # 旧区方向键不再用于全局移鼠，避免与格子键重复
    keys["region_left"] = ""
    keys["region_right"] = ""
    keys["region_up"] = ""
    keys["region_down"] = ""
    return keys


def save_hotkeys(keys: dict):
    with open(HOTKEY_FILE, "w", encoding="utf-8") as f:
        json.dump(keys, f, ensure_ascii=False, indent=2)


# ========== 数据模型 ==========
@dataclass
class Region:
    name: str = "区域1"
    rx1: float = 40
    ry1: float = 40
    rx2: float = 440
    ry2: float = 280
    cols: int = 10
    rows: int = 5
    cell_w: str = ""
    cell_h: str = ""
    origin: str = "center"
    locked: bool = False
    # 同 Layout 内区域下标；None 表示无链接
    link_left: Optional[int] = None
    link_right: Optional[int] = None
    link_up: Optional[int] = None
    link_down: Optional[int] = None

    def rect(self):
        return self.rx1, self.ry1, self.rx2, self.ry2

    def set_rect(self, x1, y1, x2, y2):
        self.rx1, self.ry1, self.rx2, self.ry2 = x1, y1, x2, y2

    def center(self):
        return (self.rx1 + self.rx2) / 2.0, (self.ry1 + self.ry2) / 2.0


@dataclass
class Layout:
    name: str = "背包"
    switch_key: str = ""
    res_w: str = "1920"
    res_h: str = "1080"
    regions: list = field(default_factory=lambda: [Region()])
    # 界面特征点：图像像素坐标 + RGB + 容差；非空即开启运行时自动切界面
    feature_points: list = field(default_factory=list)


@dataclass
class GridResult:
    name: str
    region: list
    cols: int
    rows: int
    cell_w: float
    cell_h: float
    step_x: float
    step_y: float
    origin: str
    points: list


def _to_int(val, default):
    try:
        v = int(str(val).strip())
        return v if v > 0 else default
    except Exception:
        return default


def _to_float(val):
    s = str(val).strip() if val is not None else ""
    if not s:
        return None
    try:
        v = float(s)
        return v if v > 0 else None
    except Exception:
        return None


# ========== 计算格子中心点 ==========
def compute_region(reg: Region) -> GridResult:
    cols = _to_int(reg.cols, 1)
    rows = _to_int(reg.rows, 1)
    box_w = float(reg.rx2 - reg.rx1)
    box_h = float(reg.ry2 - reg.ry1)
    cw = _to_float(reg.cell_w)
    ch = _to_float(reg.cell_h)
    if cw is None:
        cw = box_w / cols if cols else box_w
    if ch is None:
        ch = box_h / rows if rows else box_h
    points = []
    for r in range(rows):
        for c in range(cols):
            if reg.origin == "topleft":
                x = reg.rx1 + c * cw
                y = reg.ry1 + r * ch
            else:
                x = reg.rx1 + c * cw + cw / 2
                y = reg.ry1 + r * ch + ch / 2
            points.append([round(x, 2), round(y, 2)])
    return GridResult(
        name=reg.name,
        region=[round(reg.rx1, 2), round(reg.ry1, 2), round(reg.rx2, 2), round(reg.ry2, 2)],
        cols=cols,
        rows=rows,
        cell_w=round(cw, 3),
        cell_h=round(ch, 3),
        step_x=round(cw, 3),
        step_y=round(ch, 3),
        origin=reg.origin,
        points=points,
    )


def _opt_int(val):
    if val is None or val == "":
        return None
    try:
        return int(val)
    except Exception:
        return None


def region_from_dict(d) -> Region:
    return Region(
        name=d.get("name", "区域"),
        rx1=float(d.get("rx1", 40)),
        ry1=float(d.get("ry1", 40)),
        rx2=float(d.get("rx2", 440)),
        ry2=float(d.get("ry2", 280)),
        cols=int(d.get("cols", 10)),
        rows=int(d.get("rows", 5)),
        cell_w=str(d.get("cell_w", "")),
        cell_h=str(d.get("cell_h", "")),
        origin=d.get("origin", "center"),
        locked=bool(d.get("locked", False)),
        link_left=_opt_int(d.get("link_left")),
        link_right=_opt_int(d.get("link_right")),
        link_up=_opt_int(d.get("link_up")),
        link_down=_opt_int(d.get("link_down")),
    )


def _feature_point_from_dict(d) -> dict:
    """规范化单个特征点；非法项丢弃。"""
    if not isinstance(d, dict):
        return None
    try:
        x = float(d.get("x", 0))
        y = float(d.get("y", 0))
        r = int(d.get("r", 0))
        g = int(d.get("g", 0))
        b = int(d.get("b", 0))
        tol = int(d.get("tol", 15))
    except Exception:
        return None
    tol = max(0, min(tol, 255))
    return {
        "x": round(x, 2), "y": round(y, 2),
        "r": max(0, min(r, 255)),
        "g": max(0, min(g, 255)),
        "b": max(0, min(b, 255)),
        "tol": tol,
    }


def feature_points_from_list(items) -> list:
    out = []
    if not items:
        return out
    for it in items:
        fp = _feature_point_from_dict(it)
        if fp is not None:
            out.append(fp)
    return out


def layout_from_dict(d) -> Layout:
    regs = d.get("regions") or []
    # 旧配置可能含 off_x/off_y，忽略；无 feature_points 字段时默认 []
    return Layout(
        name=d.get("name", "界面"),
        switch_key=norm_keysym(d.get("switch_key", "") or ""),
        res_w=str(d.get("res_w", "1920")),
        res_h=str(d.get("res_h", "1080")),
        regions=[region_from_dict(x) for x in regs] or [Region()],
        feature_points=feature_points_from_list(d.get("feature_points")),
    )


def _fmt_cell_num(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


def region_to_dict(r: Region) -> dict:
    return {
        "name": r.name,
        "rx1": r.rx1, "ry1": r.ry1, "rx2": r.rx2, "ry2": r.ry2,
        "cols": r.cols, "rows": r.rows,
        "cell_w": r.cell_w, "cell_h": r.cell_h,
        "origin": r.origin, "locked": r.locked,
        "link_left": r.link_left, "link_right": r.link_right,
        "link_up": r.link_up, "link_down": r.link_down,
    }


def fix_region_links(regions: list, deleted_idx=None):
    """删除区域后修正/清空失效链接下标。"""
    n = len(regions)
    for r in regions:
        for attr in ("link_left", "link_right", "link_up", "link_down"):
            v = getattr(r, attr, None)
            if v is None:
                continue
            if deleted_idx is not None:
                if v == deleted_idx:
                    setattr(r, attr, None)
                    continue
                if v > deleted_idx:
                    v -= 1
                    setattr(r, attr, v)
            if getattr(r, attr) is not None and not (0 <= getattr(r, attr) < n):
                setattr(r, attr, None)


def next_default_switch_key(existing):
    return ""


# ========== 界面特征点取色对话框 ==========
class FeaturePointsDialog(Toplevel):
    """在编辑截图上点选特征点，保存到指定 Layout.feature_points。"""

    DEFAULT_TOL = 15

    def __init__(self, master: "App", layout_idx: int):
        super().__init__(master)
        self.master_app = master
        self.layout_idx = layout_idx
        lay = master.layouts[layout_idx]
        self.title(f"特征点取色 — {lay.name}")
        self.resizable(True, True)
        self.transient(master)
        self.geometry("420x460")
        self.points = [dict(p) for p in (lay.feature_points or [])]
        self.tol_var = StringVar(value=str(self.points[0]["tol"] if self.points else self.DEFAULT_TOL))
        self.pick_on = False
        self._closed = False

        tip = (
            "用法：先打开/粘贴背包截图，点「开始点选」后在右侧编辑画布上点击特征位置。\n"
            "每个点记录图像坐标与 RGB。运行时按该界面分辨率映射到屏幕采样；\n"
            "全部特征点颜色均在容差内时，自动切换到本界面。未设置则功能关闭。"
        )
        Label(self, text=tip, wraplength=390, justify="left", fg="#333").pack(
            fill=X, padx=12, pady=(12, 6)
        )

        tf = Frame(self)
        tf.pack(fill=X, padx=12, pady=2)
        Label(tf, text="颜色容差 ±").pack(side=LEFT)
        Entry(tf, textvariable=self.tol_var, width=6).pack(side=LEFT, padx=4)
        Label(tf, text="（建议 12～18）").pack(side=LEFT)

        lf = Frame(self)
        lf.pack(fill=BOTH, expand=True, padx=12, pady=6)
        self.listbox = Listbox(lf, height=12)
        sb = Scrollbar(lf, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side=LEFT, fill=BOTH, expand=True)
        sb.pack(side=RIGHT, fill=Y)

        bf = Frame(self)
        bf.pack(fill=X, padx=12, pady=4)
        self.btn_pick = Button(bf, text="开始点选", command=self._toggle_pick)
        self.btn_pick.pack(side=LEFT, expand=True, fill=X)
        Button(bf, text="删除选中", command=self._del_sel).pack(side=LEFT, expand=True, fill=X, padx=4)
        Button(bf, text="清空全部", command=self._clear_all).pack(side=LEFT, expand=True, fill=X)

        bf2 = Frame(self)
        bf2.pack(fill=X, padx=12, pady=(4, 12))
        Button(bf2, text="保存并开启", command=self._save, bg="#c8e6c9").pack(side=LEFT, expand=True, fill=X)
        Button(bf2, text="取消", command=self._cancel).pack(side=LEFT, expand=True, fill=X, padx=4)

        self.hint = Label(self, text="", fg="#1565c0", wraplength=390, justify="left")
        self.hint.pack(fill=X, padx=12, pady=(0, 8))

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Destroy>", self._on_destroy)
        self._refresh_list()
        if master.image is None:
            self.hint.config(text="当前无截图，请先「打开截图」或「粘贴截图」再点选。", fg="#c62828")

    def _tol(self) -> int:
        try:
            v = int(str(self.tol_var.get()).strip() or self.DEFAULT_TOL)
        except Exception:
            v = self.DEFAULT_TOL
        return max(0, min(v, 255))

    def _refresh_list(self):
        self.listbox.delete(0, END)
        for i, p in enumerate(self.points):
            self.listbox.insert(
                END,
                f"{i + 1}. ({p['x']:.1f}, {p['y']:.1f})  RGB({p['r']},{p['g']},{p['b']})  ±{p.get('tol', self.DEFAULT_TOL)}",
            )

    def _toggle_pick(self):
        if self.pick_on:
            self._stop_pick()
            return
        if self.master_app.image is None:
            messagebox.showinfo("提示", "请先打开或粘贴一张截图，再点选特征点。", parent=self)
            return
        # 切到目标界面，便于对照
        if self.master_app.layout_i != self.layout_idx:
            self.master_app.switch_layout(self.layout_idx)
        self.pick_on = True
        self.master_app._feature_pick_cb = self._on_canvas_pick
        self.btn_pick.config(text="停止点选", bg="#fff59d")
        self.hint.config(text="点选中：请在右侧编辑画布上点击特征位置（Esc 或再点按钮停止）。", fg="#1565c0")
        self.master_app._draw_feature_markers(self.points)
        try:
            self.master_app.bind("<Escape>", self._esc_pick)
        except Exception:
            pass

    def _esc_pick(self, event=None):
        self._stop_pick()
        return "break"

    def _stop_pick(self):
        self.pick_on = False
        if getattr(self.master_app, "_feature_pick_cb", None) is self._on_canvas_pick:
            self.master_app._feature_pick_cb = None
        try:
            self.btn_pick.config(text="开始点选", bg="#f0f0f0")
        except Exception:
            pass
        self.hint.config(text="已停止点选。", fg="#666")
        try:
            self.master_app.unbind("<Escape>")
        except Exception:
            pass
        self.master_app._draw_feature_markers(self.points)

    def _on_canvas_pick(self, ix, iy):
        app = self.master_app
        if app.image is None:
            return
        iw, ih = app.image.size
        x = max(0, min(ix, iw - 1))
        y = max(0, min(iy, ih - 1))
        try:
            pix = app.image.getpixel((int(round(x)), int(round(y))))
        except Exception:
            return
        if isinstance(pix, int):
            r = g = b = int(pix)
        else:
            r, g, b = int(pix[0]), int(pix[1]), int(pix[2])
        tol = self._tol()
        self.points.append({
            "x": round(float(x), 2), "y": round(float(y), 2),
            "r": r, "g": g, "b": b, "tol": tol,
        })
        # 同步已有点的容差显示为当前容差（新点用当前；旧点保留各自 tol）
        self._refresh_list()
        self.hint.config(
            text=f"已添加点 #{len(self.points)}：({x:.1f},{y:.1f}) RGB({r},{g},{b}) ±{tol}",
            fg="#2e7d32",
        )
        app._draw_feature_markers(self.points)

    def _del_sel(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.points):
            del self.points[idx]
            self._refresh_list()
            self.master_app._draw_feature_markers(self.points)

    def _clear_all(self):
        if not self.points:
            return
        if not messagebox.askyesno("确认", "清空本界面全部特征点？", parent=self):
            return
        self.points = []
        self._refresh_list()
        self.master_app._draw_feature_markers([])

    def _apply_tol_to_all(self):
        tol = self._tol()
        for p in self.points:
            p["tol"] = tol

    def _save(self):
        self._stop_pick()
        self._apply_tol_to_all()
        lay = self.master_app.layouts[self.layout_idx]
        lay.feature_points = feature_points_from_list(self.points)
        self.master_app._feature_pick_cb = None
        self.master_app._feature_preview_points = []
        self.master_app._clear_feature_markers()
        self.master_app._refresh_layout_list()
        self.master_app.save_layouts_file()
        n = len(lay.feature_points)
        if n:
            self.master_app.status_var.set(f"「{lay.name}」已设 {n} 个特征点，自动切界面已开启")
        else:
            self.master_app.status_var.set(f"「{lay.name}」特征点已清空，自动切界面关闭")
        self._closed = True
        self.destroy()

    def _cancel(self):
        self._stop_pick()
        self.master_app._feature_pick_cb = None
        self.master_app._feature_preview_points = []
        self.master_app._clear_feature_markers()
        self._closed = True
        self.destroy()

    def _on_destroy(self, event=None):
        if event and event.widget is not self:
            return
        if not self._closed:
            self.master_app._feature_pick_cb = None
            self.master_app._feature_preview_points = []
            self.master_app._clear_feature_markers()


# ========== 热键对话框 ==========
class HotkeyDialog(Toplevel):
    def __init__(self, master: "App"):
        super().__init__(master)
        self.master_app = master
        self.title("快捷键设置")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.keys = dict(master.hotkeys)
        self.layout_keys = [L.switch_key for L in master.layouts]
        self.labels = {}
        self.layout_labels = []
        self.waiting = None  # ("global", act) or ("layout", idx)

        Label(self, text="点击「更改」后按键。界面切换键按层级单独设置。",
              wraplength=460, justify="left").grid(row=0, column=0, columnspan=3, padx=12, pady=(12, 8), sticky="w")

        groups = [
            ("格子移鼠（开启移鼠后全局）", ["cell_left", "cell_right", "cell_up", "cell_down"]),
            ("切换区域（当前界面的大区）", ["prev_region", "next_region"]),
            ("界面循环 / 其它", [
                "prev_layout", "next_layout", "toggle_mouse", "toggle_overlay",
                "toggle_global_lock", "cell_home", "cell_again",
            ]),
        ]
        row = 1
        titles = {k: t for k, t in HOTKEY_ACTIONS}
        for title, acts in groups:
            Label(self, text=title, font=("Segoe UI", 10, "bold")).grid(
                row=row, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 4)
            )
            row += 1
            for act in acts:
                Label(self, text=titles[act], width=20, anchor="w").grid(row=row, column=0, padx=12, pady=2, sticky="w")
                lb = Label(self, text=pretty_key(self.keys[act]), width=14, relief="groove", bg="#f5f5f5")
                lb.grid(row=row, column=1, padx=4, pady=2)
                self.labels[act] = lb
                Button(self, text="更改", width=8,
                       command=lambda a=act: self._begin(("global", a))).grid(row=row, column=2, padx=12, pady=2)
                row += 1

        Label(self, text="各背包界面专属切换键", font=("Segoe UI", 10, "bold")).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 4)
        )
        row += 1
        for i, lay in enumerate(master.layouts):
            Label(self, text=f"切换到「{lay.name}」", width=20, anchor="w").grid(
                row=row, column=0, padx=12, pady=2, sticky="w"
            )
            lb = Label(self, text=pretty_key(self.layout_keys[i]) or "未设置", width=14, relief="groove", bg="#e3f2fd")
            lb.grid(row=row, column=1, padx=4, pady=2)
            self.layout_labels.append(lb)
            Button(self, text="更改", width=8,
                   command=lambda idx=i: self._begin(("layout", idx))).grid(row=row, column=2, padx=12, pady=2)
            row += 1

        bf = Frame(self)
        bf.grid(row=row, column=0, columnspan=3, pady=12)
        Button(bf, text="恢复默认", command=self._reset).pack(side=LEFT, padx=6)
        Button(bf, text="保存", command=self._save).pack(side=LEFT, padx=6)
        Button(bf, text="取消", command=self.destroy).pack(side=LEFT, padx=6)

        self.bind("<KeyPress>", self._on_key)
        self.hint = Label(self, text="", fg="#c62828")
        self.hint.grid(row=row + 1, column=0, columnspan=3, pady=(0, 10))

    def _begin(self, token):
        self.waiting = token
        kind, ref = token
        if kind == "global":
            self.labels[ref].config(text="请按键…", bg="#fff59d")
        else:
            self.layout_labels[ref].config(text="请按键…", bg="#fff59d")
        self.hint.config(text="正在监听（Esc 取消）")
        self.focus_set()

    def _on_key(self, event):
        if not self.waiting:
            return
        sym = norm_keysym(event.keysym)
        if sym in ("shift_l", "shift_r", "control_l", "control_r", "alt_l", "alt_r"):
            return
        kind, ref = self.waiting
        target = self.labels[ref] if kind == "global" else self.layout_labels[ref]
        old = self.keys[ref] if kind == "global" else self.layout_keys[ref]
        if sym == "escape":
            target.config(text=pretty_key(old), bg="#f5f5f5" if kind == "global" else "#e3f2fd")
            self.waiting = None
            self.hint.config(text="")
            return
        if not vk_of(sym) and len(sym) != 1:
            self.hint.config(text=f"不支持的键：{event.keysym}")
            return
        if kind == "global":
            self.keys[ref] = sym
        else:
            self.layout_keys[ref] = sym
        target.config(text=pretty_key(sym), bg="#c8e6c9")
        self.waiting = None
        self.hint.config(text="")

    def _reset(self):
        self.keys = dict(DEFAULT_KEYS)
        for i in range(len(self.layout_keys)):
            self.layout_keys[i] = f"f{min(i + 1, 12)}"
        for act, lb in self.labels.items():
            lb.config(text=pretty_key(self.keys[act]), bg="#f5f5f5")
        for i, lb in enumerate(self.layout_labels):
            lb.config(text=pretty_key(self.layout_keys[i]), bg="#e3f2fd")

    def _save(self):
        save_hotkeys(self.keys)
        self.master_app.apply_hotkeys(self.keys)
        self.master_app.apply_layout_keys(self.layout_keys)
        try:
            self.master_app.save_layouts_file()
        except Exception:
            pass
        self.destroy()


# ========== 界面切换小窗 ==========
class SwitcherWindow(Toplevel):
    def __init__(self, master: "App"):
        super().__init__(master)
        self.master_app = master
        self.title("界面切换")
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.geometry("+40+80")
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.body = Frame(self, padx=8, pady=8)
        self.body.pack()
        self.refresh()

    def refresh(self):
        for w in self.body.winfo_children():
            w.destroy()
        Label(self.body, text="点击或用专属快捷键切换", fg="#555").pack(anchor="w", pady=(0, 6))
        app = self.master_app
        for i, lay in enumerate(app.layouts):
            on = i == app.layout_i
            key = pretty_key(lay.switch_key)
            txt = f"{lay.name}   [{key}]" if key else lay.name
            bg = "#1565c0" if on else "#37474f"
            Button(
                self.body, text=txt, bg=bg, fg="white", width=28,
                command=lambda idx=i: app.switch_layout(idx),
            ).pack(fill=X, pady=2)


# ========== 全屏辅助线（绿=当前区 白=其它） ==========
class ScreenOverlay:
    """全屏置顶辅助线网格。激活时绿色，未激活时白色；Windows 下点击穿透。"""

    def __init__(self, app: "App"):
        self.app = app
        self.win = None
        self.canvas = None
        self._pin_job = None

    def show(self):
        if self.win and self.win.winfo_exists():
            self.redraw()
            self.pin_top()
            self._schedule_pin()
            return
        sw = self.app.winfo_screenwidth()
        sh = self.app.winfo_screenheight()
        win = Toplevel(self.app)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-transparentcolor", "black")
        except Exception:
            win.attributes("-alpha", 0.35)
        try:
            win.attributes("-disabled", True)
        except Exception:
            pass
        win.geometry(f"{sw}x{sh}+0+0")
        win.configure(bg="black")
        cv = Canvas(win, bg="black", highlightthickness=0, width=sw, height=sh)
        cv.pack(fill=BOTH, expand=True)
        self.win = win
        self.canvas = cv
        win.update_idletasks()
        self._make_clickthrough(win)
        self.redraw()
        self.pin_top()
        self._schedule_pin()

    def hide(self):
        if self._pin_job:
            try:
                self.app.after_cancel(self._pin_job)
            except Exception:
                pass
            self._pin_job = None
        if self.win and self.win.winfo_exists():
            self.win.destroy()
        self.win = None
        self.canvas = None

    def visible(self):
        return bool(self.win and self.win.winfo_exists())

    def _hwnd(self):
        if not IS_WIN or user32 is None or not self.visible():
            return 0
        hwnd = self.win.winfo_id()
        parent = user32.GetParent(hwnd)
        return parent or hwnd

    def _make_clickthrough(self, win):
        if not IS_WIN or user32 is None:
            return
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOPMOST = 0x00000008
        hwnd = win.winfo_id()
        parent = user32.GetParent(hwnd)
        if parent:
            hwnd = parent
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE | WS_EX_TOPMOST,
        )
        self.pin_top()

    def pin_top(self):
        if not IS_WIN or user32 is None or not self.visible():
            return
        try:
            self.win.attributes("-topmost", False)
            self.win.attributes("-topmost", True)
            self.win.lift()
        except Exception:
            pass
        hwnd = self._hwnd()
        if not hwnd:
            return
        HWND_TOPMOST = -1
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010
        SWP_SHOWWINDOW = 0x0040
        user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def _schedule_pin(self):
        if self._pin_job:
            try:
                self.app.after_cancel(self._pin_job)
            except Exception:
                pass
        if self.visible():
            self._pin_job = self.app.after(250, self._keep_top)

    def _keep_top(self):
        self._pin_job = None
        if self.visible():
            self.pin_top()
            self._schedule_pin()

    def redraw(self):
        if not self.visible():
            return
        cv = self.canvas
        cv.delete("all")
        app = self.app
        lay = app.L()
        # 当前激活区域：绿线；其它区域：白线（与编辑画布一致）
        for i, reg in enumerate(lay.regions):
            active = i == app.cur
            color = "#00e676" if active else "#ffffff"
            width = 3 if active else 1
            res = compute_region(reg)
            x1, y1 = app.scale_to_screen(reg.rx1, reg.ry1)
            x2, y2 = app.scale_to_screen(reg.rx2, reg.ry2)
            cv.create_rectangle(x1, y1, x2, y2, outline=color, width=width)
            for c in range(1, res.cols):
                xx, _ = app.scale_to_screen(reg.rx1 + c * res.step_x, reg.ry1)
                cv.create_line(xx, y1, xx, y2, fill=color, width=1)
            for r in range(1, res.rows):
                _, yy = app.scale_to_screen(reg.rx1, reg.ry1 + r * res.step_y)
                cv.create_line(x1, yy, x2, yy, fill=color, width=1)
            tw, th = app.target_resolution()
            cv.create_text(
                x1 + 6, y1 + 6,
                text=f"{lay.name} / {reg.name}  {res.cols}x{res.rows}  {tw}×{th}",
                fill=color, anchor="nw", font=("Segoe UI", 10),
            )


# ========== 主窗口 ==========
class App(Tk):
    COLOR_ACTIVE = "#00e676"
    COLOR_INACTIVE = "#ffffff"

    def __init__(self):
        super().__init__()
        _set_windows_app_id()
        self.title("Controller Bagbuddy")
        self.geometry("1440x920")
        self.minsize(1180, 820)
        self._apply_window_icon()

        self.image = None
        self.tk_image = None
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0

        self.layouts = [Layout(name="背包", switch_key="")]
        self.global_lock = True
        self.global_lock_var = StringVar(value="全局锁定")
        self.global_lock_chk = IntVar(value=1)
        self.layout_i = 0
        self.cur = 0
        self.cell_index = 0
        self.hotkeys = load_hotkeys()
        self._edit_binds = []
        self._layout_tk_binds = []
        self.switcher = None
        self.overlay = None
        self.overlay_on = False
        self.overlay_var = StringVar(value="屏幕辅助线：关闭")

        self.layout_name_var = StringVar(value="背包")
        self.name_var = StringVar(value="区域1")
        self.cols_var = StringVar(value="10")
        self.rows_var = StringVar(value="5")
        self.cell_w_var = StringVar(value="")
        self.cell_h_var = StringVar(value="")
        self.origin_var = StringVar(value="center")
        self.res_w_var = StringVar(value="1920")
        self.res_h_var = StringVar(value="1080")
        self.res_preset_var = StringVar(value="1K / 1080p (1920×1080)")
        self.status_var = StringVar(value="请加载一张背包截图")
        self.info_var = StringVar(value="")
        self.mouse_var = StringVar(value="移鼠：关闭")
        self.hotkey_hint_var = StringVar(value="")
        self.active_var = StringVar(value="●  未激活")
        self.draw_border_var = IntVar(value=1)
        self.cross_region_var = IntVar(value=0)
        self.config_name_var = StringVar(value=DEFAULT_CONFIG_NAME)
        self.config_combo_var = StringVar(value=DEFAULT_CONFIG_NAME)

        self.mouse_on = False
        self.cross_region = False
        self.active_config = DEFAULT_CONFIG_NAME
        self._poll_stop = threading.Event()
        self._feature_pick_cb = None
        self._feature_dialog = None
        self._feature_preview_points = []
        self._feature_cooldown_until = 0.0
        self._list_wheel_target = None
        self._key_was_down = set()
        self._last_toggle_ts = 0.0
        self._last_act_ts = {}
        self._last_action_fire = {}
        self._hook_ok = False
        self._move_lock = threading.Lock()
        self._ui_lock = False
        self._drag_mode = None
        self._drag_start = (0, 0)
        self._drag_rect = (0, 0, 0, 0)

        self._ensure_configs_migrated()
        self._load_active_config()
        self._build_ui()
        self.apply_hotkeys(self.hotkeys)
        self._show_layout()
        self._redraw()
        self._refresh_config_ui()
        self._start_global_poll()
        self._start_feature_detect()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- 当前数据 -----

    def _apply_window_icon(self):
        """固定窗口/任务栏图标。优先外部 ico/png，失败则用内嵌 PNG，避免 Tk 默认羽毛图标。"""
        import tempfile
        import shutil
        import base64
        from io import BytesIO
        ico = _resource_path("backpack_tool.ico")
        png = _resource_path("backpack_tool_icon_256.png")
        self._icon_img = None
        self._icon_tmp = None

        def _load_pil():
            for p in (png, ico):
                if p and os.path.isfile(p):
                    try:
                        return Image.open(p).convert("RGBA")
                    except Exception:
                        pass
            try:
                raw = base64.b64decode(EMBEDDED_ICON_PNG_B64)
                return Image.open(BytesIO(raw)).convert("RGBA")
            except Exception:
                return None

        im = _load_pil()
        if im is None:
            return
        if max(im.size) > 256:
            im = im.resize((256, 256), Image.Resampling.LANCZOS)
        try:
            self._icon_img = ImageTk.PhotoImage(im)
            self.iconphoto(True, self._icon_img)
        except Exception:
            pass
        # iconbitmap 需要 .ico 文件；写到纯英文临时路径
        try:
            tmp_ico = os.path.join(tempfile.gettempdir(), "backpack_grid_tool_app.ico")
            if os.path.isfile(ico):
                shutil.copyfile(ico, tmp_ico)
            else:
                # 单尺寸 ico
                im64 = im.resize((64, 64), Image.Resampling.LANCZOS)
                im64.save(tmp_ico, format="ICO", sizes=[(64, 64)])
            self._icon_tmp = tmp_ico
            try:
                self.iconbitmap(default=tmp_ico)
            except Exception:
                try:
                    self.iconbitmap(tmp_ico)
                except Exception:
                    pass
        except Exception:
            pass

    def L(self) -> Layout:
        if not self.layouts:
            self.layouts = [Layout()]
            self.layout_i = 0
        self.layout_i = max(0, min(self.layout_i, len(self.layouts) - 1))
        return self.layouts[self.layout_i]

    def regions(self):
        return self.L().regions

    def R(self) -> Region:
        regs = self.regions()
        if not regs:
            self.L().regions = [Region()]
            self.cur = 0
            regs = self.L().regions
        self.cur = max(0, min(self.cur, len(regs) - 1))
        return regs[self.cur]

    def _sync_from_ui(self):
        lay = self.L()
        lay.name = self.layout_name_var.get().strip() or lay.name
        lay.res_w = self.res_w_var.get()
        lay.res_h = self.res_h_var.get()
        r = self.R()
        r.name = self.name_var.get().strip() or r.name
        r.cols = _to_int(self.cols_var.get(), 1)
        r.rows = _to_int(self.rows_var.get(), 1)
        r.cell_w = self.cell_w_var.get()
        r.cell_h = self.cell_h_var.get()
        r.origin = self.origin_var.get()
        self.cross_region = bool(self.cross_region_var.get())

    def _show_layout(self):
        self._ui_lock = True
        lay = self.L()
        self.layout_name_var.set(lay.name)
        self.res_w_var.set(lay.res_w or "1920")
        self.res_h_var.set(lay.res_h or "1080")
        try:
            self.res_preset_var.set(preset_name_for(int(float(lay.res_w)), int(float(lay.res_h))))
        except Exception:
            self.res_preset_var.set("自定义")
        self.cur = min(self.cur, len(lay.regions) - 1)
        r = self.R()
        self.name_var.set(r.name)
        self.cols_var.set(str(r.cols))
        self.rows_var.set(str(r.rows))
        self.cell_w_var.set(r.cell_w)
        self.cell_h_var.set(r.cell_h)
        self.origin_var.set(r.origin)
        self.cross_region_var.set(1 if self.cross_region else 0)
        self.cell_index = 0
        self._refresh_layout_list()
        self._refresh_region_list()
        self._ui_lock = False
        self._bind_layout_tk_keys()
        self._update_hotkey_hint()
        if self.switcher and self.switcher.winfo_exists():
            self.switcher.refresh()

    def _auto_fill_cell_size(self, r: Region = None):
        """按区域宽高与行列自动填入格子宽高。"""
        if r is None:
            r = self.R()
        cols = max(_to_int(r.cols, 1), 1)
        rows = max(_to_int(r.rows, 1), 1)
        box_w = float(r.rx2 - r.rx1)
        box_h = float(r.ry2 - r.ry1)
        r.cell_w = _fmt_cell_num(box_w / cols)
        r.cell_h = _fmt_cell_num(box_h / rows)
        if r is self.R() and not self._ui_lock:
            self._ui_lock = True
            self.cell_w_var.set(r.cell_w)
            self.cell_h_var.set(r.cell_h)
            self._ui_lock = False
        elif r is self.R() and self._ui_lock:
            self.cell_w_var.set(r.cell_w)
            self.cell_h_var.set(r.cell_h)

    def _on_field_change(self, *_):
        if self._ui_lock:
            return
        old_cols = self.R().cols
        old_rows = self.R().rows
        self._sync_from_ui()
        r = self.R()
        if r.cols != old_cols or r.rows != old_rows:
            self._auto_fill_cell_size(r)
        self._refresh_layout_list()
        self._refresh_region_list()
        self._redraw()

    def _bind_wheel_deep(self, widget, handler):
        widget.bind("<MouseWheel>", handler)
        widget.bind("<Button-4>", handler)
        widget.bind("<Button-5>", handler)
        for child in widget.winfo_children():
            self._bind_wheel_deep(child, handler)

    def _make_scroll_box(self, parent, height):
        """固定高度列表 + 可见竖向滑条；滚轮仅滚动本列表（return break 防冒泡）。"""
        wrap = Frame(parent)
        wrap.pack(fill=X)
        canvas = Canvas(wrap, height=height, highlightthickness=1, highlightbackground="#ccc", bg="white")
        sb = Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = Frame(canvas, bg="white")
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas._scroll_win_id = win_id
        canvas._scroll_inner = inner
        # 标记：左侧大面板滚轮绑定需跳过这些控件
        for w in (wrap, canvas, inner, sb):
            w._is_list_scroll = True

        def _sync_scrollregion(_event=None, c=canvas):
            bbox = c.bbox("all")
            c.configure(scrollregion=bbox if bbox else (0, 0, 0, 0))

        def _on_canvas_configure(event, c=canvas, wid=win_id):
            # 内层 window 宽度跟随 canvas，避免右侧被裁切
            try:
                c.itemconfigure(wid, width=max(int(event.width), 1))
            except Exception:
                pass
            _sync_scrollregion()

        inner.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        sb.pack(side=RIGHT, fill=Y)

        def _wheel(e, c=canvas):
            delta = int(-e.delta / 120) if getattr(e, "delta", 0) else (1 if getattr(e, "num", 0) == 5 else -1)
            c.yview_scroll(delta, "units")
            return "break"

        def _bind_list_wheel(widget, handler=_wheel):
            widget.bind("<MouseWheel>", handler)
            widget.bind("<Button-4>", handler)
            widget.bind("<Button-5>", handler)
            widget._is_list_scroll = True
            for ch in widget.winfo_children():
                _bind_list_wheel(ch, handler)

        for w in (canvas, inner, wrap, sb):
            w.bind("<MouseWheel>", _wheel)
            w.bind("<Button-4>", _wheel)
            w.bind("<Button-5>", _wheel)

        # 进入列表区域时记录目标，离开后左侧面板可继续滚
        def _enter(_e=None, c=canvas):
            self._list_wheel_target = c

        def _leave(_e=None, c=canvas):
            if getattr(self, "_list_wheel_target", None) is c:
                self._list_wheel_target = None

        for w in (wrap, canvas, inner, sb):
            w.bind("<Enter>", _enter)
            w.bind("<Leave>", _leave)

        canvas._bind_list_wheel = _bind_list_wheel
        canvas._sync_scrollregion = _sync_scrollregion
        return canvas, inner

    def _update_scroll_box(self, canvas):
        """刷新列表后强制同步宽度与 scrollregion；内容变短时复位到顶部。"""
        if canvas is None:
            return
        try:
            self.update_idletasks()
        except Exception:
            pass
        try:
            wid = getattr(canvas, "_scroll_win_id", None)
            if wid is not None:
                canvas.itemconfigure(wid, width=max(int(canvas.winfo_width()), 1))
        except Exception:
            pass
        try:
            bbox = canvas.bbox("all")
            if not bbox:
                canvas.configure(scrollregion=(0, 0, 0, 0))
                canvas.yview_moveto(0)
                return
            canvas.configure(scrollregion=bbox)
            content_h = bbox[3] - bbox[1]
            view_h = max(int(canvas.winfo_height()), 1)
            if content_h <= view_h + 1:
                canvas.yview_moveto(0)
        except Exception:
            pass

    def _refresh_layout_list(self):
        if not hasattr(self, "layout_list_inner"):
            return
        self.layout_key_btns = {}
        self.layout_feature_btns = {}
        for w in self.layout_list_inner.winfo_children():
            w.destroy()
        for i, lay in enumerate(self.layouts):
            row = Frame(self.layout_list_inner, bg="white")
            row.pack(fill=X)
            row._is_list_scroll = True
            on = i == self.layout_i
            bg = "#bbdefb" if on else "white"
            txt = f"{'► ' if on else '   '}{lay.name}"
            lab = Label(row, text=txt, anchor="w", bg=bg)
            lab.pack(side=LEFT, fill=X, expand=True)
            has_fp = bool(getattr(lay, "feature_points", None))
            # 右侧先放「色」再放快捷键（pack RIGHT 先者靠最右）
            fb = Button(
                row, text="色", width=3,
                bg="#c8e6c9" if has_fp else "#e0e0e0",
                fg="#1b5e20" if has_fp else "#666666",
                relief="raised" if has_fp else "flat",
                command=lambda idx=i: self.open_feature_points(idx),
            )
            fb.pack(side=RIGHT, padx=(0, 2))
            fb.bind("<Button-3>", lambda e, idx=i: (self.clear_feature_points(idx) or "break"))
            self.layout_feature_btns[i] = fb
            keytxt = pretty_key(lay.switch_key) or "未设"
            kb = Button(row, text=keytxt, width=6,
                        command=lambda idx=i: self.begin_capture(("layout", idx)))
            kb.pack(side=RIGHT, padx=2)
            self.layout_key_btns[i] = kb
            for w in (row, lab, kb, fb):
                w._is_list_scroll = True
                w.bind("<MouseWheel>", lambda e: self._forward_wheel(e, self.layout_list_canvas))
                w.bind("<Button-4>", lambda e: self._forward_wheel(e, self.layout_list_canvas))
                w.bind("<Button-5>", lambda e: self._forward_wheel(e, self.layout_list_canvas))
            for w in (row, lab):
                w.bind("<Button-1>", lambda e, idx=i: self.switch_layout(idx))
                w.bind("<Button-3>", lambda e, idx=i: self._popup_rename_layout(e, idx))
        self._refresh_lock_button()
        self._update_scroll_box(getattr(self, "layout_list_canvas", None))
        # 不再整树重绑左侧滚轮，以免盖掉列表自己的滚轮


    def _refresh_region_list(self):
        if not hasattr(self, "region_list_inner"):
            return
        for w in self.region_list_inner.winfo_children():
            w.destroy()
        regs = self.regions()
        for i, r in enumerate(regs):
            row = Frame(self.region_list_inner, bg="white")
            row.pack(fill=X)
            row._is_list_scroll = True
            on = i == self.cur
            bg = "#bbdefb" if on else "white"
            hints = []
            if r.link_left is not None and 0 <= r.link_left < len(regs):
                hints.append(f"←{r.link_left + 1}")
            if r.link_right is not None and 0 <= r.link_right < len(regs):
                hints.append(f"→{r.link_right + 1}")
            if r.link_up is not None and 0 <= r.link_up < len(regs):
                hints.append(f"↑{r.link_up + 1}")
            if r.link_down is not None and 0 <= r.link_down < len(regs):
                hints.append(f"↓{r.link_down + 1}")
            link_txt = ("  " + " ".join(hints)) if hints else ""
            txt = f"{'► ' if on else '   '}{r.name}  {r.cols}x{r.rows}{link_txt}"
            lab = Label(row, text=txt, anchor="w", bg=bg)
            lab.pack(side=LEFT, fill=X, expand=True)
            var = IntVar(value=1 if r.locked else 0)
            chk = Checkbutton(
                row, text="锁定", variable=var, bg=bg,
                command=lambda idx=i, v=var: self._set_region_lock(idx, bool(v.get())),
            )
            if self.global_lock:
                chk.config(state="disabled")
            chk.pack(side=RIGHT)
            for w in (row, lab, chk):
                w._is_list_scroll = True
                w.bind("<MouseWheel>", lambda e: self._forward_wheel(e, self.region_list_canvas))
                w.bind("<Button-4>", lambda e: self._forward_wheel(e, self.region_list_canvas))
                w.bind("<Button-5>", lambda e: self._forward_wheel(e, self.region_list_canvas))
            for w in (row, lab):
                w.bind("<Button-1>", lambda e, idx=i: self.select_region(idx))
                w.bind("<Button-3>", lambda e, idx=i: self._popup_rename_region(e, idx))
        self._refresh_lock_button()
        self._update_scroll_box(getattr(self, "region_list_canvas", None))


    def _forward_wheel(self, event, canvas):
        if not canvas:
            return "break"
        delta = int(-event.delta / 120) if getattr(event, "delta", 0) else (1 if getattr(event, "num", 0) == 5 else -1)
        canvas.yview_scroll(delta, "units")
        return "break"

    def _popup_rename_layout(self, event, idx):
        self.switch_layout(idx)
        menu = Menu(self, tearoff=0)
        menu.add_command(label="重命名界面", command=lambda: self.rename_layout(idx))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _popup_region_menu(self, event, idx):
        regs = self.regions()
        if idx < 0 or idx >= len(regs):
            return
        self.select_region(idx)
        menu = Menu(self, tearoff=0)
        menu.add_command(label="重命名区域", command=lambda: self.rename_region(idx))
        menu.add_command(label="链接区域…", command=lambda: self._link_region_dialog(idx))
        menu.add_command(label="清除本区链接", command=lambda: self._clear_region_links(idx))
        menu.add_separator()
        menu.add_command(label="删除区域", command=lambda: self._del_region_at(idx))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _popup_rename_region(self, event, idx):
        self._popup_region_menu(event, idx)

    def _clear_region_links(self, idx):
        regs = self.regions()
        if idx < 0 or idx >= len(regs):
            return
        r = regs[idx]
        r.link_left = r.link_right = r.link_up = r.link_down = None
        # 对方指向本区的反向链接也清掉
        for i, other in enumerate(regs):
            if i == idx:
                continue
            for attr, back in (
                ("link_left", "link_right"), ("link_right", "link_left"),
                ("link_up", "link_down"), ("link_down", "link_up"),
            ):
                if getattr(other, attr) == idx:
                    setattr(other, attr, None)
        self.status_var.set(f"已清除「{r.name}」的链接")
        self._refresh_region_list()
        self._redraw()

    def _link_region_dialog(self, src_idx):
        regs = self.regions()
        if src_idx < 0 or src_idx >= len(regs):
            return
        if len(regs) < 2:
            messagebox.showinfo("提示", "至少需要两个区域才能链接。")
            return
        dlg = Toplevel(self)
        dlg.title("链接区域")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()
        Label(dlg, text=f"将「{regs[src_idx].name}」链接到：", anchor="w").pack(fill=X, padx=12, pady=(10, 4))
        lb = Listbox(dlg, height=min(10, len(regs) - 1), exportselection=False)
        choices = []
        for i, r in enumerate(regs):
            if i == src_idx:
                continue
            choices.append(i)
            lb.insert(END, f"{i + 1}. {r.name}")
        lb.pack(fill=BOTH, expand=True, padx=12, pady=4)
        if choices:
            lb.selection_set(0)

        def _ok():
            sel = lb.curselection()
            if not sel:
                messagebox.showinfo("提示", "请选择目标区域。", parent=dlg)
                return
            dst_idx = choices[sel[0]]
            self._link_regions_auto(src_idx, dst_idx)
            dlg.destroy()

        bf = Frame(dlg)
        bf.pack(fill=X, padx=12, pady=(4, 10))
        Button(bf, text="确定", command=_ok).pack(side=RIGHT, padx=4)
        Button(bf, text="取消", command=dlg.destroy).pack(side=RIGHT)
        dlg.wait_window()

    def _link_regions_auto(self, src_idx, dst_idx):
        regs = self.regions()
        if src_idx == dst_idx or not (0 <= src_idx < len(regs)) or not (0 <= dst_idx < len(regs)):
            return
        a, b = regs[src_idx], regs[dst_idx]
        ax, ay = a.center()
        bx, by = b.center()
        dx, dy = bx - ax, by - ay
        if abs(dx) >= abs(dy):
            # 水平：目标在右 / 左
            if dx >= 0:
                src_attr, dst_attr, dname = "link_right", "link_left", "右"
            else:
                src_attr, dst_attr, dname = "link_left", "link_right", "左"
        else:
            if dy >= 0:
                src_attr, dst_attr, dname = "link_down", "link_up", "下"
            else:
                src_attr, dst_attr, dname = "link_up", "link_down", "上"
        old = getattr(a, src_attr)
        setattr(a, src_attr, dst_idx)
        setattr(b, dst_attr, src_idx)
        tip = f"已双向链接：{a.name} —{dname}→ {b.name}"
        if old is not None and old != dst_idx:
            tip += f"（覆盖原链接→{regs[old].name if 0 <= old < len(regs) else old}）"
        # 链接成功：自动打开跨区总开关并反映到勾选框
        self.cross_region = True
        if hasattr(self, "cross_region_var"):
            self.cross_region_var.set(1)
        tip += f"（{dname}向跨区已开）"
        self.status_var.set(tip)
        self._refresh_region_list()
        self._redraw()

    def rename_region(self, idx=None):
        if idx is None:
            idx = self.cur
        regs = self.regions()
        if idx < 0 or idx >= len(regs):
            return
        self._sync_from_ui()
        old = regs[idx].name
        name = simpledialog.askstring("重命名区域", "新名称：", initialvalue=old, parent=self)
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showinfo("提示", "名称不能为空。")
            return
        regs[idx].name = name
        if idx == self.cur:
            self._ui_lock = True
            self.name_var.set(name)
            self._ui_lock = False
        self._refresh_region_list()
        self._redraw()
        self.save_layouts_file()

    def _set_region_lock(self, idx, locked):
        if self.global_lock:
            self.status_var.set("全局锁定中，请先取消勾选上方全局锁定")
            self._refresh_region_list()
            return
        if 0 <= idx < len(self.regions()):
            self.regions()[idx].locked = locked
        self._redraw()

    def _on_global_lock_check(self):
        self.global_lock = bool(self.global_lock_chk.get())
        self._refresh_lock_button()
        self._refresh_region_list()
        self.status_var.set("全局锁定" if self.global_lock else "全局解锁，可调区域")
        self._redraw()

    def region_is_locked(self, reg=None):
        if self.global_lock:
            return True
        r = reg if reg is not None else self.R()
        return bool(r.locked)

    def toggle_region_lock(self, idx):
        if idx < 0 or idx >= len(self.regions()):
            return
        if self.global_lock:
            self.status_var.set("全局锁定中，请先解除全局锁定")
            return
        r = self.regions()[idx]
        r.locked = not r.locked
        self._refresh_region_list()
        self._redraw()

    def toggle_global_lock(self):
        self.global_lock = not self.global_lock
        self.global_lock_chk.set(1 if self.global_lock else 0)
        self._refresh_lock_button()
        self._refresh_region_list()
        self.status_var.set("全局锁定" if self.global_lock else "全局解锁，可调区域")
        self._redraw()

    def _refresh_lock_button(self):
        extra = self._hk_suffix("toggle_global_lock")
        if self.global_lock:
            self.global_lock_var.set(f"全局锁定{extra}")
        else:
            self.global_lock_var.set(f"全局锁定{extra}")
        self.global_lock_chk.set(1 if self.global_lock else 0)

    def _hk_suffix(self, act):
        k = pretty_key(self.hotkeys.get(act, ""))
        return f"  [{k}]" if k else ""

    # ----- UI -----
    # ----- UI -----
    def _build_ui(self):
        self._capturing = None
        self.hk_labels = {}

        bottom = Frame(self, padx=8, pady=6, bd=1, relief="groove")
        bottom.pack(side="bottom", fill=X)

        body = Frame(self)
        body.pack(side="top", fill=BOTH, expand=True)

        left_wrap = Frame(body, width=310)
        left_wrap.pack(side=LEFT, fill=Y)
        left_wrap.pack_propagate(False)
        self._side_left = left_wrap

        left_canvas = Canvas(left_wrap, highlightthickness=0, width=292)
        left_scroll = Scrollbar(left_wrap, orient="vertical", command=left_canvas.yview)
        left_canvas.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side=RIGHT, fill=Y)
        left_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        left = Frame(left_canvas, padx=8, pady=6)
        self._left_inner = left
        left_win = left_canvas.create_window((0, 0), window=left, anchor="nw")

        def _sync_left_scroll(_event=None):
            left_canvas.configure(scrollregion=left_canvas.bbox("all"))
            # 内容不够高时也不裁切底部按钮
            left_canvas.itemconfigure(left_win, width=max(left_canvas.winfo_width() - 2, 260))

        left.bind("<Configure>", _sync_left_scroll)
        left_canvas.bind("<Configure>", _sync_left_scroll)

        def _wheel_left(event):
            # 鼠标在层级/大区列表上时，不滚左侧大面板
            if getattr(self, "_list_wheel_target", None) is not None:
                return
            w = event.widget
            while w is not None:
                if getattr(w, "_is_list_scroll", False):
                    return
                try:
                    w = w.master
                except Exception:
                    break
            if getattr(event, "delta", 0):
                left_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
            elif getattr(event, "num", None) == 4:
                left_canvas.yview_scroll(-1, "units")
            elif getattr(event, "num", None) == 5:
                left_canvas.yview_scroll(1, "units")

        def _bind_left_wheel(w):
            if getattr(w, "_is_list_scroll", False):
                return
            w.bind("<MouseWheel>", _wheel_left)
            w.bind("<Button-4>", _wheel_left)
            w.bind("<Button-5>", _wheel_left)
            for ch in w.winfo_children():
                _bind_left_wheel(ch)

        self._bind_left_wheel = _bind_left_wheel
        self._left_canvas = left_canvas
        self._list_wheel_target = None

        loadf = Frame(left)
        loadf.pack(fill=X)
        Button(loadf, text="打开截图", command=self.load_image).pack(side=LEFT, expand=True, fill=X)
        Button(loadf, text="粘贴截图", command=self.paste_clipboard).pack(side=LEFT, expand=True, fill=X, padx=(4, 0))

        self.chk_global_lock = Checkbutton(
            left, textvariable=self.global_lock_var, variable=self.global_lock_chk,
            command=self._on_global_lock_check, anchor="w",
        )
        self.chk_global_lock.pack(fill=X, pady=(6, 0))
        self.btn_overlay = Button(left, textvariable=self.overlay_var, command=self.toggle_overlay, bg="#444", fg="white")
        self.btn_overlay.pack(fill=X, pady=2)

        Label(left, text="背包界面 / 层级", anchor="w",
              font=("Segoe UI", 9, "bold")).pack(fill=X, pady=(8, 2))
        self.layout_list_canvas, self.layout_list_inner = self._make_scroll_box(left, 130)
        self.layout_list = None
        lbf = Frame(left)
        lbf.pack(fill=X, pady=2)
        Button(lbf, text="新增界面", command=self.add_layout).pack(side=LEFT, expand=True, fill=X)
        Button(lbf, text="复制", command=self.dup_layout).pack(side=LEFT, expand=True, fill=X, padx=3)
        Button(lbf, text="删除", command=self.del_layout).pack(side=LEFT, expand=True, fill=X)
        lnf = Frame(left)
        lnf.pack(fill=X, pady=(2, 6))
        Label(lnf, text="界面名").pack(side=LEFT)
        Entry(lnf, textvariable=self.layout_name_var).pack(side=LEFT, fill=X, expand=True, padx=6)

        Label(left, text="当前界面的大区", anchor="w",
              font=("Segoe UI", 9, "bold")).pack(fill=X, pady=(4, 2))
        self.region_list_canvas, self.region_list_inner = self._make_scroll_box(left, 130)
        self.listbox = None
        bf = Frame(left)
        bf.pack(fill=X, pady=2)
        Button(bf, text="新增大区", command=self.add_region).pack(side=LEFT, expand=True, fill=X)
        Button(bf, text="复制", command=self.dup_region).pack(side=LEFT, expand=True, fill=X, padx=3)
        Button(bf, text="删除", command=self.del_region).pack(side=LEFT, expand=True, fill=X)
        nf = Frame(left)
        nf.pack(fill=X, pady=(2, 2))
        Label(nf, text="大区名").pack(side=LEFT)
        Entry(nf, textvariable=self.name_var).pack(side=LEFT, fill=X, expand=True, padx=6)
        self.region_label = Label(left, text="", anchor="w", fg="#333")
        self.region_label.pack(fill=X)
        rowf = Frame(left)
        rowf.pack(fill=X, pady=2)
        Label(rowf, text="列").pack(side=LEFT)
        Entry(rowf, textvariable=self.cols_var, width=5).pack(side=LEFT, padx=4)
        Label(rowf, text="行").pack(side=LEFT)
        Entry(rowf, textvariable=self.rows_var, width=5).pack(side=LEFT, padx=4)
        Label(left, text="格子宽高（按区域自动计算）", anchor="w").pack(fill=X)
        cf = Frame(left)
        cf.pack(fill=X)
        Label(cf, text="宽").pack(side=LEFT)
        Entry(cf, textvariable=self.cell_w_var, width=7).pack(side=LEFT, padx=4)
        Label(cf, text="高").pack(side=LEFT)
        Entry(cf, textvariable=self.cell_h_var, width=7).pack(side=LEFT, padx=4)
        of = Frame(left)
        of.pack(fill=X, pady=4)
        Button(of, text="格子中心", command=lambda: self._set_origin("center")).pack(side=LEFT, expand=True, fill=X)
        Button(of, text="左上角", command=lambda: self._set_origin("topleft")).pack(side=LEFT, expand=True, fill=X, padx=(4, 0))
        Button(left, text="按格子尺寸重算大区", command=self.resize_region_from_cells).pack(fill=X, pady=1)
        Checkbutton(
            left, text="跨区域移动（开：有链接则跨区，无链接卡边缘；关：环绕）", variable=self.cross_region_var,
            command=self._on_cross_region_check, anchor="w",
        ).pack(fill=X, pady=(4, 0))

        savef = Frame(left)
        savef.pack(fill=X, pady=(8, 2))
        Button(savef, text="保存", command=self.save_layouts_file).pack(side=LEFT, expand=True, fill=X)
        Button(savef, text="复制JSON", command=self.copy_json).pack(side=LEFT, expand=True, fill=X, padx=3)
        Button(savef, text="导出", command=self.export_json).pack(side=LEFT, expand=True, fill=X)

        Label(left, text="配置文件（多游戏）", anchor="w",
              font=("Segoe UI", 9, "bold")).pack(fill=X, pady=(8, 2))
        cfgf = Frame(left)
        cfgf.pack(fill=X)
        Label(cfgf, text="当前").pack(side=LEFT)
        self.config_combo = ttk.Combobox(
            cfgf, textvariable=self.config_combo_var, state="readonly", width=14,
        )
        self.config_combo.pack(side=LEFT, fill=X, expand=True, padx=4)
        self.config_combo.bind("<<ComboboxSelected>>", lambda e: None)
        cfgb = Frame(left)
        cfgb.pack(fill=X, pady=2)
        Button(cfgb, text="切换配置", command=self.switch_config_prompt).pack(side=LEFT, expand=True, fill=X)
        Button(cfgb, text="新建", command=self.new_config).pack(side=LEFT, expand=True, fill=X, padx=2)
        Button(cfgb, text="另存为", command=self.save_config_as).pack(side=LEFT, expand=True, fill=X, padx=2)
        Button(cfgb, text="删除", command=self.delete_config).pack(side=LEFT, expand=True, fill=X)
        self.btn_mouse = Button(left, textvariable=self.mouse_var, command=self.toggle_mouse, bg="#444", fg="white")
        Label(left, textvariable=self.info_var, justify="left", wraplength=280, fg="#063", anchor="w").pack(fill=X, pady=(4, 0))
        Label(left, textvariable=self.status_var, justify="left", wraplength=280, fg="#666", anchor="w").pack(fill=X)

        try:
            self._bind_left_wheel(left)
        except Exception:
            pass

        # ----- 底部：激活 / 分辨率 / 快捷键 -----
        col1 = Frame(bottom)
        col1.pack(side=LEFT, padx=(0, 16), fill=Y)
        Label(col1, text="激活", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        actf = Frame(col1)
        actf.pack()
        self.btn_active = Button(
            actf, textvariable=self.active_var, command=self.toggle_active,
            font=("Segoe UI", 11, "bold"), fg="white", bg="#616161",
            width=14, height=1, pady=8,
        )
        self.btn_active.pack(side=LEFT)
        self.chk_border = Checkbutton(
            actf, text="同时绘制\n界面边框", variable=self.draw_border_var,
            command=self._on_border_check, justify="left",
        )
        self.chk_border.pack(side=LEFT, padx=8)

        col2 = Frame(bottom)
        col2.pack(side=LEFT, padx=(0, 16), fill=Y)
        Label(col2, text="分辨率", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.res_combo = ttk.Combobox(
            col2, textvariable=self.res_preset_var,
            values=RES_PRESET_NAMES, state="readonly", width=26,
        )
        self.res_combo.pack(anchor="w", pady=2)
        self.res_combo.bind("<<ComboboxSelected>>", self._on_preset_selected)
        rf2 = Frame(col2)
        rf2.pack(anchor="w")
        Label(rf2, text="宽").pack(side=LEFT)
        Entry(rf2, textvariable=self.res_w_var, width=7).pack(side=LEFT, padx=4)
        Label(rf2, text="高").pack(side=LEFT)
        Entry(rf2, textvariable=self.res_h_var, width=7).pack(side=LEFT, padx=4)
        Button(col2, text="用截图尺寸", command=self.use_image_resolution).pack(anchor="w", pady=2)

        col3 = Frame(bottom)
        col3.pack(side=LEFT, fill=BOTH, expand=True)
        Label(col3, text="快捷键（点键名后按键，Esc 或右键取消，Backspace 清空）",
              font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.capture_hint_var = StringVar(value="")
        self.capture_hint = Label(col3, textvariable=self.capture_hint_var, fg="#ef6c00",
                                  font=("Segoe UI", 9, "bold"))
        self.capture_hint.pack(anchor="w")
        self.hk_bar = Frame(col3)
        self.hk_bar.pack(fill=X)
        self._build_hotkey_bar()

        self.canvas = Canvas(body, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(side=RIGHT, fill=BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_down)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_up)
        self.canvas.bind("<Button-3>", self._on_canvas_right_click)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.bind("<KeyPress>", self._on_capture_key)
        self.bind_all("<Button-3>", self._on_capture_right_click)

        for v in (self.layout_name_var, self.name_var, self.cols_var, self.rows_var,
                  self.cell_w_var, self.cell_h_var,
                  self.res_w_var, self.res_h_var):
            v.trace_add("write", self._on_field_change)
        self.res_w_var.trace_add("write", lambda *_: self._sync_preset_label())
        self.res_h_var.trace_add("write", lambda *_: self._sync_preset_label())

        for seq, d in (
            ("<Control-Left>", (0, 0, -1, 0)),
            ("<Control-Right>", (0, 0, 1, 0)),
            ("<Control-Up>", (0, 0, 0, -1)),
            ("<Control-Down>", (0, 0, 0, 1)),
            ("<Control-Shift-Left>", (0, 0, -10, 0)),
            ("<Control-Shift-Right>", (0, 0, 10, 0)),
            ("<Control-Shift-Up>", (0, 0, 0, -10)),
            ("<Control-Shift-Down>", (0, 0, 0, 10)),
        ):
            self.bind(seq, lambda e, d=d: self._edit_nudge(*d))
        self.bind("[", lambda e: self._edit_inflate(-2))
        self.bind("]", lambda e: self._edit_inflate(2))
        self.bind("{", lambda e: self._edit_inflate(-10))
        self.bind("}", lambda e: self._edit_inflate(10))

    def _on_preset_selected(self, event=None):
        name = self.res_preset_var.get()
        for n, w, h in RES_PRESETS:
            if n == name and w > 0 and h > 0:
                self._ui_lock = True
                self.res_w_var.set(str(w))
                self.res_h_var.set(str(h))
                self._ui_lock = False
                self._sync_from_ui()
                self._redraw()
                self.status_var.set(f"分辨率已设为 {w}×{h}")
                return

    def _sync_preset_label(self, *_):
        if self._ui_lock:
            return
        try:
            w = int(float(self.res_w_var.get()))
            h = int(float(self.res_h_var.get()))
        except Exception:
            return
        name = preset_name_for(w, h)
        if self.res_preset_var.get() != name:
            self.res_preset_var.set(name)

    def use_image_resolution(self):
        if self.image is None:
            messagebox.showinfo("提示", "请先加载截图。")
            return
        w, h = self.image.size
        self._ui_lock = True
        self.res_w_var.set(str(w))
        self.res_h_var.set(str(h))
        self.res_preset_var.set(preset_name_for(w, h))
        self._ui_lock = False
        self._sync_from_ui()
        self._redraw()

    def target_resolution(self):
        try:
            w = int(float(str(self.res_w_var.get()).strip() or 1920))
            h = int(float(str(self.res_h_var.get()).strip() or 1080))
        except Exception:
            w, h = 1920, 1080
        return max(w, 1), max(h, 1)

    def scale_to_screen(self, x, y):
        """图像像素 → 目标分辨率坐标。"""
        tw, th = self.target_resolution()
        if self.image is not None:
            sw, sh = self.image.size
        else:
            sw, sh = tw, th
        sx = x * (tw / sw) if sw else x
        sy = y * (th / sh) if sh else y
        return sx, sy

    def _build_hotkey_bar(self):
        if not hasattr(self, "hk_bar"):
            return
        for w in self.hk_bar.winfo_children():
            w.destroy()
        self.hk_labels = {}
        items = [
            ("toggle_mouse", "激活"),
            ("toggle_overlay", "辅助线"),
            ("toggle_global_lock", "全局锁"),
            ("prev_region", "上个区域"),
            ("next_region", "下个区域"),
            ("cell_left", "朝左一格"),
            ("cell_right", "朝右一格"),
            ("cell_up", "朝上一格"),
            ("cell_down", "朝下一格"),
            ("prev_layout", "上一界面"),
            ("next_layout", "下一界面"),
        ]
        # 固定两行：11 项 → 每行最多 6 个
        per_row = 6
        for i, (act, title) in enumerate(items):
            cell = Frame(self.hk_bar)
            cell.grid(row=i // per_row, column=i % per_row, padx=4, pady=2, sticky="w")
            Label(cell, text=title, font=("Segoe UI", 9)).pack(side=LEFT)
            txt = pretty_key(self.hotkeys.get(act, "")) or "未设"
            lb = Button(
                cell, text=txt, width=5, font=("Segoe UI", 9),
                command=lambda a=act: self.begin_capture(("hotkey", a)),
            )
            lb.pack(side=LEFT, padx=2)
            self.hk_labels[act] = lb

    def _capture_btn(self, token):
        if not token:
            return None
        kind, ref = token
        if kind == "hotkey":
            return self.hk_labels.get(ref)
        return getattr(self, "layout_key_btns", {}).get(ref)

    def begin_capture(self, token):
        self._stop_capture_flash()
        self._capturing = token
        kind, ref = token
        name = ref if kind == "layout" else dict(HOTKEY_ACTIONS).get(ref, ref)
        if kind == "layout" and 0 <= ref < len(self.layouts):
            name = self.layouts[ref].name
        self.capture_hint_var.set(f"正在设置「{name}」：请按键    Esc / 右键 取消")
        if hasattr(self, "capture_hint"):
            self.capture_hint.config(fg="#e65100", bg="#fff3e0")
        btn = self._capture_btn(token)
        if btn:
            btn.config(text="按键…", bg="#ff9800", fg="white", activebackground="#fb8c00")
        self._flash_on = True
        self._flash_capture()
        self.status_var.set("请按要绑定的键（Esc 或右键取消，Backspace 清空）")
        self.focus_force()

    def _flash_capture(self):
        if not self._capturing:
            return
        btn = self._capture_btn(self._capturing)
        self._flash_on = not getattr(self, "_flash_on", False)
        if btn:
            btn.config(bg="#ff9800" if self._flash_on else "#ffe0b2",
                       fg="white" if self._flash_on else "#e65100")
        if hasattr(self, "capture_hint"):
            self.capture_hint.config(fg="#e65100" if self._flash_on else "#ef6c00")
        self._flash_job = self.after(280, self._flash_capture)

    def _stop_capture_flash(self):
        if getattr(self, "_flash_job", None):
            try:
                self.after_cancel(self._flash_job)
            except Exception:
                pass
            self._flash_job = None
        if hasattr(self, "capture_hint"):
            self.capture_hint.config(bg=self.cget("bg"))

    def _finish_capture(self, ok, text):
        self._stop_capture_flash()
        self._capturing = None
        if hasattr(self, "capture_hint"):
            self.capture_hint.config(fg="#2e7d32" if ok else "#757575")
        self.capture_hint_var.set(text)
        self.status_var.set(text)
        self.after(1600, lambda: self.capture_hint_var.set("") if not self._capturing else None)

    def _cancel_capture(self):
        if not self._capturing:
            return
        self._finish_capture(False, "已取消设键")
        self._build_hotkey_bar()
        self._refresh_layout_list()

    def _on_capture_right_click(self, event):
        if not self._capturing:
            return
        self._cancel_capture()
        return "break"

    def _on_canvas_right_click(self, event):
        # 捕获取消优先：正在设键时只取消，不弹菜单
        if self._capturing:
            self._cancel_capture()
            return "break"
        ix, iy = self.canvas_to_image(event.x, event.y)
        hit = self._region_at(ix, iy)
        if hit is None:
            return
        self._popup_region_menu(event, hit)
        return "break"

    def _on_cross_region_check(self):
        self.cross_region = bool(self.cross_region_var.get())
        self.status_var.set("跨区域移动：开" if self.cross_region else "跨区域移动：关")

    def _on_capture_key(self, event):
        if not self._capturing:
            return
        sym = norm_keysym(event.keysym)
        if sym in ("shift_l", "shift_r", "control_l", "control_r", "alt_l", "alt_r"):
            return
        kind, ref = self._capturing
        if sym == "escape":
            self._finish_capture(False, "已取消设键")
            self._build_hotkey_bar()
            self._refresh_layout_list()
            return "break"
        if sym in ("backspace", "delete"):
            sym = ""
        if kind == "hotkey":
            self.hotkeys[ref] = sym
            save_hotkeys(self.hotkeys)
            self.apply_hotkeys(self.hotkeys)
            try:
                self.save_layouts_file()
            except Exception:
                pass
            self._build_hotkey_bar()
            btn = self.hk_labels.get(ref)
            if btn:
                btn.config(bg="#c8e6c9", fg="#1b5e20")
        else:
            if 0 <= ref < len(self.layouts):
                self.layouts[ref].switch_key = sym
                self._bind_layout_tk_keys()
                self._refresh_layout_list()
                self.save_layouts_file()
                btn = getattr(self, "layout_key_btns", {}).get(ref)
                if btn:
                    btn.config(bg="#c8e6c9", fg="#1b5e20")
        shown = pretty_key(sym) if sym else "空"
        self._finish_capture(True, f"已设为 {shown}")
        return "break"

    def open_hotkey_settings(self):
        self._sync_from_ui()
        HotkeyDialog(self)

    def open_switcher(self):
        if self.switcher and self.switcher.winfo_exists():
            self.switcher.lift()
            self.switcher.refresh()
            return
        self.switcher = SwitcherWindow(self)

    # ----- 热键绑定（走 _dispatch_action 防抖） -----
    def apply_hotkeys(self, keys: dict):
        self.hotkeys = dict(keys)
        for seq in self._edit_binds:
            try:
                self.unbind(seq)
            except Exception:
                pass
        self._edit_binds = []
        mapping = {
            "region_left": (-1, 0, 0, 0),
            "region_right": (1, 0, 0, 0),
            "region_up": (0, -1, 0, 0),
            "region_down": (0, 1, 0, 0),
        }
        def _bind_if(act, fn):
            raw = self.hotkeys.get(act, "")
            if not raw:
                return
            seq = tk_seq(raw)
            if not seq:
                return
            self.bind(seq, fn)
            self._edit_binds.append(seq)

        for act, delta in mapping.items():
            raw = self.hotkeys.get(act, "")
            if not raw:
                continue
            seq = tk_seq(raw)
            self.bind(seq, lambda e, d=delta: self._region_move(d, 1))
            self._edit_binds.append(seq)
            shift_seq = self._shift_seq(seq)
            if shift_seq:
                self.bind(shift_seq, lambda e, d=delta: self._region_move(d, 10))
                self._edit_binds.append(shift_seq)
        # 走统一分发，避免与全局轮询各触发一次（两界面时会来回切等于没动）
        _bind_if("toggle_mouse", lambda e: self._dispatch_action("toggle_mouse"))
        _bind_if("toggle_overlay", lambda e: self._dispatch_action("toggle_overlay"))
        _bind_if("toggle_global_lock", lambda e: self._dispatch_action("toggle_global_lock"))
        _bind_if("prev_layout", lambda e: self._dispatch_action("prev_layout"))
        _bind_if("next_layout", lambda e: self._dispatch_action("next_layout"))
        _bind_if("prev_region", lambda e: self._dispatch_action("prev_region"))
        _bind_if("next_region", lambda e: self._dispatch_action("next_region"))
        self._bind_layout_tk_keys()
        self._update_hotkey_hint()
        self._refresh_lock_button()
        self._refresh_active_ui()

    def apply_layout_keys(self, keys):
        for i, k in enumerate(keys):
            if i < len(self.layouts):
                self.layouts[i].switch_key = norm_keysym(k)
        self._bind_layout_tk_keys()
        self._refresh_layout_list()
        self._update_hotkey_hint()
        self.save_layouts_file()
        if self.switcher and self.switcher.winfo_exists():
            self.switcher.refresh()

    def _bind_layout_tk_keys(self):
        for seq in self._layout_tk_binds:
            try:
                self.unbind(seq)
            except Exception:
                pass
        self._layout_tk_binds = []
        for i, lay in enumerate(self.layouts):
            if not lay.switch_key:
                continue
            seq = tk_seq(lay.switch_key)
            self.bind(seq, lambda e, idx=i: self.switch_layout(idx))
            self._layout_tk_binds.append(seq)

    def _shift_seq(self, seq: str) -> str:
        if seq.startswith("<") and seq.endswith(">") and "-" not in seq[1:-1]:
            return f"<Shift-{seq[1:-1]}>"
        if len(seq) == 1:
            return f"<Shift-KeyPress-{seq}>"
        return ""

    def _update_hotkey_hint(self):
        k = self.hotkeys
        lines = []
        def add(label, act):
            pk = pretty_key(k.get(act, ""))
            if pk:
                lines.append(f"{label} {pk}")
        add("循环界面↑", "prev_layout")
        add("循环界面↓", "next_layout")
        add("辅助线", "toggle_overlay")
        add("激活", "toggle_mouse")
        add("全局锁", "toggle_global_lock")
        cells = "".join(pretty_key(k.get(a, "")) for a in ("cell_left", "cell_right", "cell_up", "cell_down"))
        if cells:
            lines.append("格子 " + cells)
        for lay in self.layouts:
            pk = pretty_key(lay.switch_key)
            if pk:
                lines.append(f"{lay.name} → {pk}")
        self.hotkey_hint_var.set("\n".join(lines) if lines else "仅方向键调区域，其它快捷键请到设置里配")

    def _region_move(self, delta, mul):
        if self.mouse_on:
            return
        dx, dy, dw, dh = delta
        self._edit_nudge(dx * mul, dy * mul, dw * mul, dh * mul)

    def _set_origin(self, mode):
        self.origin_var.set(mode)
        self._sync_from_ui()
        self._redraw()

    def _edit_nudge(self, dx, dy, dw, dh):
        if self.mouse_on:
            return
        if self.region_is_locked():
            self.status_var.set("区域已锁定，解锁后才能调整大小")
            return
        r = self.R()
        r.rx1 += dx
        r.ry1 += dy
        r.rx2 += dx + dw
        r.ry2 += dy + dh
        self._normalize_rect(r)
        self._redraw()

    def _edit_inflate(self, amount):
        if self.mouse_on:
            return
        if self.region_is_locked():
            self.status_var.set("区域已锁定，解锁后才能调整大小")
            return
        r = self.R()
        r.rx1 -= amount
        r.ry1 -= amount
        r.rx2 += amount
        r.ry2 += amount
        self._normalize_rect(r)
        self._redraw()

    # ----- 界面切换 -----
    # ----- 切换界面 -----
    def open_feature_points(self, idx):
        """左键：打开特征点取色小窗。"""
        if idx < 0 or idx >= len(self.layouts):
            return
        dlg = getattr(self, "_feature_dialog", None)
        if dlg is not None:
            try:
                if dlg.winfo_exists():
                    dlg.lift()
                    dlg.focus_set()
                    return
            except Exception:
                pass
        self._feature_dialog = FeaturePointsDialog(self, idx)

    def clear_feature_points(self, idx):
        """右键色按钮：清空该界面特征点并关闭功能。"""
        if idx < 0 or idx >= len(self.layouts):
            return
        lay = self.layouts[idx]
        if not getattr(lay, "feature_points", None):
            self.status_var.set(f"「{lay.name}」尚未设置特征点")
            return
        lay.feature_points = []
        self._refresh_layout_list()
        self.save_layouts_file()
        self.status_var.set(f"已清空「{lay.name}」特征点，自动切界面已关闭")
        return "break"

    def _draw_feature_markers(self, points):
        """在编辑画布上叠加特征点标记（不影响区域拖拽逻辑）。"""
        self._feature_preview_points = list(points or [])
        self._clear_feature_markers()
        if not points or self.image is None:
            return
        for i, p in enumerate(points):
            try:
                cx, cy = self.image_to_canvas(float(p["x"]), float(p["y"]))
            except Exception:
                continue
            r = 6
            self.canvas.create_oval(
                cx - r, cy - r, cx + r, cy + r,
                outline="#ff1744", width=2, fill="#ff8a80", tags="feature_pt",
            )
            self.canvas.create_text(
                cx + 10, cy - 10, text=str(i + 1),
                fill="#b71c1c", font=("Segoe UI", 9, "bold"), tags="feature_pt",
            )

    def _clear_feature_markers(self):
        try:
            self.canvas.delete("feature_pt")
        except Exception:
            pass

    def _restore_feature_markers_after_redraw(self):
        pts = getattr(self, "_feature_preview_points", None)
        if pts:
            # 直接画，避免再次覆盖 _feature_preview_points
            for i, p in enumerate(pts):
                try:
                    cx, cy = self.image_to_canvas(float(p["x"]), float(p["y"]))
                except Exception:
                    continue
                r = 6
                self.canvas.create_oval(
                    cx - r, cy - r, cx + r, cy + r,
                    outline="#ff1744", width=2, fill="#ff8a80", tags="feature_pt",
                )
                self.canvas.create_text(
                    cx + 10, cy - 10, text=str(i + 1),
                    fill="#b71c1c", font=("Segoe UI", 9, "bold"), tags="feature_pt",
                )

    def switch_layout(self, idx):
        if not self.layouts:
            return
        self._sync_from_ui()
        self.layout_i = idx % len(self.layouts)
        self.cur = 0
        self._show_layout()
        self._redraw()
        self.status_var.set(f"当前界面：{self.L().name}  键 {pretty_key(self.L().switch_key)}")
        self.refresh_overlay()

    def _on_layout_right_click(self, event):
        idx = self.layout_list.nearest(event.y)
        if idx < 0 or idx >= len(self.layouts):
            return
        self.layout_list.selection_clear(0, END)
        self.layout_list.selection_set(idx)
        self.layout_list.activate(idx)
        if idx != self.layout_i:
            self.switch_layout(idx)
        menu = Menu(self, tearoff=0)
        menu.add_command(label="重命名界面", command=lambda: self.rename_layout(idx))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def rename_layout(self, idx=None):
        if idx is None:
            idx = self.layout_i
        if idx < 0 or idx >= len(self.layouts):
            return
        self._sync_from_ui()
        old = self.layouts[idx].name
        name = simpledialog.askstring("重命名界面", "新名称：", initialvalue=old, parent=self)
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showinfo("提示", "名称不能为空。")
            return
        self.layouts[idx].name = name
        if idx == self.layout_i:
            self._ui_lock = True
            self.layout_name_var.set(name)
            self._ui_lock = False
        self._refresh_layout_list()
        self._bind_layout_tk_keys()
        self._update_hotkey_hint()
        if self.switcher and self.switcher.winfo_exists():
            self.switcher.refresh()
        self.save_layouts_file()
        self.status_var.set(f"已重命名为「{name}」")
        self._redraw()

    def _on_layout_list(self, event):
        if self._ui_lock:
            return
        sel = self.layout_list.curselection()
        if not sel:
            return
        self.switch_layout(int(sel[0]))

    def add_layout(self):
        self._sync_from_ui()
        key = next_default_switch_key(L.switch_key for L in self.layouts)
        n = len(self.layouts) + 1
        self.layouts.append(Layout(name=f"界面{n}", switch_key=key, regions=[Region()]))
        self.layout_i = len(self.layouts) - 1
        self.cur = 0
        self._show_layout()
        self._redraw()
        self.save_layouts_file()

    def dup_layout(self):
        self._sync_from_ui()
        src = self.L()
        key = next_default_switch_key(L.switch_key for L in self.layouts)
        regs = [Region(**asdict(r)) for r in src.regions]
        fps = [dict(p) for p in (getattr(src, "feature_points", None) or [])]
        self.layouts.append(Layout(
            name=src.name + " 副本", switch_key=key,
            res_w=src.res_w, res_h=src.res_h, regions=regs,
            feature_points=fps,
        ))
        self.layout_i = len(self.layouts) - 1
        self.cur = 0
        self._show_layout()
        self._redraw()
        self.save_layouts_file()

    def del_layout(self):
        if len(self.layouts) <= 1:
            messagebox.showinfo("提示", "至少保留一个界面。")
            return
        del self.layouts[self.layout_i]
        self.layout_i = min(self.layout_i, len(self.layouts) - 1)
        self.cur = 0
        self._show_layout()
        self._redraw()
        self.save_layouts_file()

    def _on_region_list(self, event=None):
        return

    def add_region(self):
        self._sync_from_ui()
        n = len(self.regions()) + 1
        base = self.R()
        nr = Region(
            name=f"区域{n}",
            rx1=base.rx1 + 30, ry1=base.ry1 + 30,
            rx2=base.rx2 + 30, ry2=base.ry2 + 30,
            cols=base.cols, rows=base.rows,
            cell_w=base.cell_w, cell_h=base.cell_h, origin=base.origin,
        )
        if self.image:
            self._normalize_rect(nr)
        self._auto_fill_cell_size(nr)
        self.L().regions.append(nr)
        self.cur = len(self.regions()) - 1
        self.cell_index = 0
        self._show_layout()
        self._redraw()

    def dup_region(self):
        self._sync_from_ui()
        r = self.R()
        nr = Region(**asdict(r))
        nr.name = r.name + " 副本"
        nr.rx1 += 20
        nr.ry1 += 20
        nr.rx2 += 20
        nr.ry2 += 20
        nr.link_left = nr.link_right = nr.link_up = nr.link_down = None
        self.L().regions.append(nr)
        self.cur = len(self.regions()) - 1
        self._show_layout()
        self._redraw()

    def del_region(self):
        self._del_region_at(self.cur)

    def _del_region_at(self, idx):
        regs = self.regions()
        if len(regs) <= 1:
            messagebox.showinfo("提示", "每个界面至少保留一个大区。")
            return
        if idx < 0 or idx >= len(regs):
            return
        del self.L().regions[idx]
        fix_region_links(self.L().regions, deleted_idx=idx)
        if self.cur > idx:
            self.cur -= 1
        elif self.cur == idx:
            self.cur = min(idx, len(self.regions()) - 1)
        self.cur = min(self.cur, len(self.regions()) - 1)
        self._show_layout()
        self._redraw()

    def select_region(self, idx):
        regs = self.regions()
        if not regs:
            return
        self._sync_from_ui()
        self.cur = idx % len(regs)
        self.cell_index = 0
        self._auto_fill_cell_size(self.R())
        # 移鼠激活时也要重绘：屏幕辅助线/画布靠绿=#00e676、白=其它区区分当前区
        # 切区不频繁，可接受；格子连跳仍走 move_to_current（不整画重绘）
        if self.mouse_on:
            self._refresh_region_list()
            self._redraw()
            return
        self._show_layout()
        self._redraw()

    # ----- 图像 -----
    def load_image(self):
        path = filedialog.askopenfilename(
            title="选择背包截图",
            filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.bmp;*.webp"), ("全部", "*.*")],
        )
        if not path:
            return
        try:
            with Image.open(path) as im:
                img = im.convert("RGB")
        except Exception as e:
            messagebox.showerror("打开截图", f"无法读取图片：\n{e}")
            return
        self._set_image(img)
        self.status_var.set(os.path.basename(path))

    def paste_clipboard(self):
        img = ImageGrab.grabclipboard()
        if img is None:
            messagebox.showinfo("剪贴板", "剪贴板里没有图片。")
            return
        if not isinstance(img, Image.Image):
            try:
                img = Image.open(img).convert("RGB")
            except Exception:
                messagebox.showinfo("剪贴板", "无法读取剪贴板图片。")
                return
        else:
            img = img.convert("RGB")
        self._set_image(img)
        self.status_var.set("来自剪贴板")

    def _set_image(self, img):
        # 必须失效 PhotoImage 缓存：同尺寸二次载入时否则仍显示旧图
        self.image = img
        self.tk_image = None
        self._tk_image_size = None
        try:
            self.canvas.delete("bg_image")
        except Exception:
            pass
        self._redraw()

    def _normalize_rect(self, r: Region):
        if r.rx2 < r.rx1:
            r.rx1, r.rx2 = r.rx2, r.rx1
        if r.ry2 < r.ry1:
            r.ry1, r.ry2 = r.ry2, r.ry1
        if r.rx2 - r.rx1 < 8:
            r.rx2 = r.rx1 + 8
        if r.ry2 - r.ry1 < 8:
            r.ry2 = r.ry1 + 8
        if self.image:
            w, h = self.image.size
            r.rx1 = max(0, min(r.rx1, w - 8))
            r.ry1 = max(0, min(r.ry1, h - 8))
            r.rx2 = max(r.rx1 + 8, min(r.rx2, w))
            r.ry2 = max(r.ry1 + 8, min(r.ry2, h))

    def resize_region_from_cells(self):
        if self.region_is_locked():
            messagebox.showinfo("提示", "区域已锁定，解锁后才能调整大小。")
            return
        self._sync_from_ui()
        r = self.R()
        cw = _to_float(r.cell_w)
        ch = _to_float(r.cell_h)
        if cw is None or ch is None:
            messagebox.showinfo("提示", "请先填写当前大区的格子宽和高。")
            return
        r.rx2 = r.rx1 + cw * r.cols
        r.ry2 = r.ry1 + ch * r.rows
        self._normalize_rect(r)
        self._redraw()

    def _region_at(self, ix, iy):
        for i in range(len(self.regions()) - 1, -1, -1):
            r = self.regions()[i]
            if r.rx1 <= ix <= r.rx2 and r.ry1 <= iy <= r.ry2:
                return i
        return None

    def _hit_handle(self, ix, iy):
        r = self.R()
        handles = {
            "nw": (r.rx1, r.ry1), "ne": (r.rx2, r.ry1),
            "sw": (r.rx1, r.ry2), "se": (r.rx2, r.ry2),
        }
        for name, (hx, hy) in handles.items():
            if abs(ix - hx) <= 8 and abs(iy - hy) <= 8:
                return name
        hit = self._region_at(ix, iy)
        if hit is not None:
            if hit != self.cur:
                self.select_region(hit)
            return "move"
        return "new"

    def on_down(self, event):
        ix, iy = self.canvas_to_image(event.x, event.y)
        cb = getattr(self, "_feature_pick_cb", None)
        if cb is not None:
            try:
                cb(ix, iy)
            except Exception:
                pass
            return
        hit = self._region_at(ix, iy)
        if hit is not None:
            self.select_region(hit)
        if self.region_is_locked():
            self._drag_mode = None
            return
        self._drag_mode = self._hit_handle(ix, iy)
        r = self.R()
        self._drag_start = (ix, iy)
        self._drag_rect = r.rect()
        if self._drag_mode == "new":
            r.rx1 = r.rx2 = ix
            r.ry1 = r.ry2 = iy
        self._redraw()

    def on_drag(self, event):
        if not self._drag_mode or self.region_is_locked():
            return
        ix, iy = self.canvas_to_image(event.x, event.y)
        x0, y0 = self._drag_start
        box = self._drag_rect
        mode = self._drag_mode
        r = self.R()
        if mode == "new":
            r.rx1, r.rx2 = sorted((x0, ix))
            r.ry1, r.ry2 = sorted((y0, iy))
        elif mode == "move":
            dx, dy = ix - x0, iy - y0
            r.set_rect(box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
        elif mode == "nw":
            r.rx1, r.ry1 = ix, iy
        elif mode == "ne":
            r.rx2, r.ry1 = ix, iy
        elif mode == "sw":
            r.rx1, r.ry2 = ix, iy
        elif mode == "se":
            r.rx2, r.ry2 = ix, iy
        self._normalize_rect(r)
        self._redraw()

    def on_up(self, event):
        mode = self._drag_mode
        self._drag_mode = None
        r = self.R()
        confirmed = False
        if mode == "new":
            # 拖拽结束后再确认，避免按住鼠标时弹窗打断操作
            w = abs(r.rx2 - r.rx1)
            h = abs(r.ry2 - r.ry1)
            old = getattr(self, "_drag_rect", None)
            if w < 8 or h < 8:
                if old is not None:
                    r.set_rect(*old)
                self.status_var.set("框格太小，已取消")
            else:
                ok = messagebox.askyesno(
                    "确认框格",
                    f"确认使用拖拽生成的框格吗？\n大小约 {w:.0f}×{h:.0f}",
                    parent=self,
                )
                if not ok:
                    if old is not None:
                        r.set_rect(*old)
                    self.status_var.set("已取消本次拖拽框格")
                else:
                    self.status_var.set(f"已确认框格 {w:.0f}×{h:.0f}")
                    confirmed = True
        self._normalize_rect(r)
        # 框选确认或拖拽改大小结束后按区域重算格子宽高
        if mode in ("new", "nw", "ne", "sw", "se") and (mode != "new" or confirmed):
            self._auto_fill_cell_size(r)
        self._redraw()

    def _fit_transform(self):
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        if self.image is None:
            return 1.0, 0, 0
        iw, ih = self.image.size
        scale = min(cw / iw, ch / ih)
        return scale, (cw - iw * scale) / 2, (ch - ih * scale) / 2

    def image_to_canvas(self, x, y):
        return x * self.scale + self.offset_x, y * self.scale + self.offset_y

    def canvas_to_image(self, x, y):
        if self.scale == 0:
            return 0, 0
        return (x - self.offset_x) / self.scale, (y - self.offset_y) / self.scale

    def image_to_screen(self, x, y):
        sx, sy = self.scale_to_screen(x, y)
        return int(round(sx)), int(round(sy))

    # ----- 移鼠 / 全局热键 -----
    def set_overlay(self, on: bool):
        if on:
            self.overlay_on = True
            if self.overlay is None:
                self.overlay = ScreenOverlay(self)
            self.overlay.show()
            self.overlay_var.set("屏幕辅助线：开启" + self._hk_suffix("toggle_overlay"))
            if hasattr(self, "btn_overlay"):
                self.btn_overlay.config(bg="#1565c0")
        else:
            self.overlay_on = False
            if self.overlay:
                self.overlay.hide()
            self.overlay_var.set("屏幕辅助线：关闭" + self._hk_suffix("toggle_overlay"))
            if hasattr(self, "btn_overlay"):
                self.btn_overlay.config(bg="#444")

    def toggle_overlay(self):
        self.set_overlay(not self.overlay_on)
        self.status_var.set("屏幕辅助线已开启" if self.overlay_on else "屏幕辅助线已关闭")

    def _refresh_active_ui(self):
        on = self.mouse_on
        extra = self._hk_suffix("toggle_mouse")
        if hasattr(self, "btn_active"):
            if on:
                self.active_var.set(f"●  已激活{extra}")
                self.btn_active.config(bg="#2e7d32", activebackground="#1b5e20", fg="white")
            else:
                self.active_var.set(f"●  未激活{extra}")
                self.btn_active.config(bg="#616161", activebackground="#757575", fg="white")

    # ----- 激活移鼠 -----
    def toggle_active(self):
        now = time.time()
        if now - self._last_toggle_ts < 0.35:
            return
        self._last_toggle_ts = now
        self._key_was_down.clear()
        if self.mouse_on:
            self._stop_mouse()
            if self.draw_border_var.get():
                self.set_overlay(False)
            self.status_var.set("已关闭")
        else:
            self.global_lock = True
            self._refresh_lock_button()
            self._refresh_region_list()
            self._start_mouse()
            if self.draw_border_var.get():
                self.set_overlay(True)
                self.status_var.set(f"已激活「{self.L().name}」，并绘制边框")
            else:
                self.status_var.set(f"已激活「{self.L().name}」")
        self._refresh_active_ui()

    def _on_border_check(self):
        if not self.mouse_on:
            return
        if self.draw_border_var.get():
            self.set_overlay(True)
        else:
            self.set_overlay(False)

    def refresh_overlay(self):
        if self.overlay_on:
            if self.overlay is None:
                self.overlay = ScreenOverlay(self)
            self.overlay.show()  # show() 内会按当前激活状态重绘绿/白线

    def toggle_mouse(self):
        if self.mouse_on:
            self._stop_mouse()
        else:
            self._start_mouse()

    def _start_mouse(self):
        if not IS_WIN:
            messagebox.showinfo("提示", "全局移鼠热键仅在 Windows 下可用。")
        self._sync_from_ui()
        self.mouse_on = True
        self.mouse_var.set("移鼠：开启")
        if hasattr(self, "btn_mouse"):
            self.btn_mouse.config(bg="#c62828")
        self.status_var.set("移鼠已开启")
        self._refresh_active_ui()
        self._set_process_priority(True)
        self._redraw()
        if self.overlay:
            self.overlay.pin_top()

    def _stop_mouse(self):
        self.mouse_on = False
        self.mouse_var.set("移鼠：关闭")
        if hasattr(self, "btn_mouse"):
            self.btn_mouse.config(bg="#444")
        self.status_var.set("移鼠已关闭")
        self._refresh_active_ui()
        self._set_process_priority(False)
        self._redraw()

    def _set_process_priority(self, high: bool):
        if not IS_WIN:
            return
        try:
            k32 = ctypes.windll.kernel32
            HIGH = 0x00000080
            NORMAL = 0x00000020
            k32.SetPriorityClass(k32.GetCurrentProcess(), HIGH if high else NORMAL)
        except Exception:
            pass

    def _effective_key(self, act):
        raw = self.hotkeys.get(act, "")
        if raw:
            return raw
        fallback = {
            "cell_left": "left", "cell_right": "right",
            "cell_up": "up", "cell_down": "down",
            "prev_region": "prior", "next_region": "next",
        }
        return fallback.get(act, "")

    # ----- 界面特征点自动切界面 -----
    def _start_feature_detect(self):
        if not IS_WIN:
            return
        threading.Thread(target=self._feature_detect_loop, daemon=True).start()

    def _any_feature_enabled(self) -> bool:
        for lay in getattr(self, "layouts", []) or []:
            if getattr(lay, "feature_points", None):
                return True
        return False

    def _layout_xy_to_screen(self, lay: Layout, x, y):
        """图像坐标 → 该 Layout 分辨率下的屏幕像素（无截图时按 1:1）。"""
        try:
            tw = int(float(str(lay.res_w).strip() or 1920))
            th = int(float(str(lay.res_h).strip() or 1080))
        except Exception:
            tw, th = 1920, 1080
        tw, th = max(tw, 1), max(th, 1)
        if self.image is not None:
            sw, sh = self.image.size
        else:
            sw, sh = tw, th
        sx = x * (tw / sw) if sw else x
        sy = y * (th / sh) if sh else y
        return int(round(sx)), int(round(sy))

    def _sample_screen_rgb(self, sx, sy):
        """取样屏幕像素 RGB；失败返回 None。"""
        if not IS_WIN or user32 is None:
            return None
        try:
            hdc = user32.GetDC(0)
            if not hdc:
                return None
            try:
                color = ctypes.windll.gdi32.GetPixel(hdc, int(sx), int(sy))
            finally:
                user32.ReleaseDC(0, hdc)
            if color is None or int(color) == -1:
                return None
            color = int(color)
            r = color & 0xFF
            g = (color >> 8) & 0xFF
            b = (color >> 16) & 0xFF
            return r, g, b
        except Exception:
            return None

    def _layout_features_match(self, lay: Layout) -> bool:
        pts = getattr(lay, "feature_points", None) or []
        if not pts:
            return False
        for p in pts:
            try:
                sx, sy = self._layout_xy_to_screen(lay, float(p["x"]), float(p["y"]))
                rgb = self._sample_screen_rgb(sx, sy)
                if rgb is None:
                    return False
                tol = int(p.get("tol", 15))
                if (abs(rgb[0] - int(p["r"])) > tol
                        or abs(rgb[1] - int(p["g"])) > tol
                        or abs(rgb[2] - int(p["b"])) > tol):
                    return False
            except Exception:
                return False
        return True

    def _feature_detect_once(self):
        if not self._any_feature_enabled():
            return
        now = time.time()
        if now < getattr(self, "_feature_cooldown_until", 0):
            return
        matches = []
        for i, lay in enumerate(self.layouts):
            if not getattr(lay, "feature_points", None):
                continue
            if self._layout_features_match(lay):
                matches.append(i)
        if not matches:
            return
        # 多匹配：优先当前已选；否则取列表中第一个，避免抖动
        if self.layout_i in matches:
            chosen = self.layout_i
        else:
            chosen = matches[0]
        if chosen == self.layout_i:
            return
        self._feature_cooldown_until = now + 1.0
        self.after(0, lambda idx=chosen: self._feature_switch_layout(idx))

    def _feature_switch_layout(self, idx):
        if idx < 0 or idx >= len(self.layouts):
            return
        if idx == self.layout_i:
            return
        name = self.layouts[idx].name
        self.switch_layout(idx)
        self.status_var.set(f"特征点匹配 → 已切换到「{name}」")

    def _feature_detect_loop(self):
        while not self._poll_stop.is_set():
            try:
                self._feature_detect_once()
            except Exception:
                pass
            # 约 0.4s；无开启特征时睡久一点少占 CPU
            if self._any_feature_enabled():
                time.sleep(0.4)
            else:
                time.sleep(1.0)

    # ----- 全局热键轮询 -----
    def _start_global_poll(self):
        if not IS_WIN:
            return
        self._poll_stop.clear()
        self._hook_ok = False
        threading.Thread(target=self._poll_keys, daemon=True).start()
        # 不再安装底层键盘钩子：64 位下 CallNextHookEx 参数溢出时会吞掉全部按键。
        # 全屏热键改走 GetAsyncKeyState 轮询，不会锁键盘。

    def _ll_hook_loop(self):
        if not IS_WIN:
            return
        last = {}

        hook_box = [None]

        def _proc(nCode, wParam, lParam):
            try:
                if nCode >= 0 and int(wParam) in (0x0100, 0x0104):
                    info = ctypes.cast(lParam, ctypes.POINTER(_KBDLLHOOK)).contents
                    vk = int(info.vkCode)
                    now = time.time()
                    if now - last.get(vk, 0) > 0.05:
                        last[vk] = now
                        self.after(0, lambda v=vk: self._on_global_vk(v))
            except Exception:
                pass
            try:
                return user32.CallNextHookEx(hook_box[0], nCode, wParam, lParam)
            except Exception:
                return 0

        cb = _LLPROC(_proc)
        self._ll_cb = cb
        hmod = kernel32.GetModuleHandleW(None)
        hook = user32.SetWindowsHookExW(13, cb, hmod, 0)
        if not hook:
            hook = user32.SetWindowsHookExW(13, cb, None, 0)
        hook_box[0] = hook
        self._ll_hook = hook
        self._hook_ok = bool(hook)
        msg = wintypes.MSG()
        while not self._poll_stop.is_set():
            r = user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1)
            if r:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.001)
        if hook:
            user32.UnhookWindowsHookEx(hook)

    def _on_global_vk(self, vk):
        if self._capturing:
            return
        now = time.time()
        if now - self._last_act_ts.get(vk, 0) < 0.12:
            return
        self._last_act_ts[vk] = now
        for act in (
            "toggle_mouse", "toggle_overlay", "toggle_global_lock",
            "prev_layout", "next_layout", "prev_region", "next_region",
            "cell_left", "cell_right", "cell_up", "cell_down",
            "cell_home", "cell_again",
            "region_left", "region_right", "region_up", "region_down",
        ):
            if vk_of(self._effective_key(act)) == vk:
                self._dispatch_action(act)
                return
        for i, lay in enumerate(self.layouts):
            if lay.switch_key and vk_of(lay.switch_key) == vk:
                self.switch_layout(i)
                return

    def _poll_keys(self):
        # next_fire: 长按首次连发延迟 + 连发间隔（格子要跟手）
        next_fire = {}
        cell_like = {
            "cell_left", "cell_right", "cell_up", "cell_down",
            "cell_home", "cell_again",
            "prev_region", "next_region",
            "region_left", "region_right", "region_up", "region_down",
        }
        while not self._poll_stop.is_set():
            now = time.time()
            watch = []
            for act in (
                "toggle_mouse", "toggle_overlay", "toggle_global_lock",
                "prev_layout", "next_layout", "prev_region", "next_region",
                "cell_left", "cell_right", "cell_up", "cell_down",
                "cell_home", "cell_again",
                "region_left", "region_right", "region_up", "region_down",
            ):
                watch.append(("act", act, vk_of(self._effective_key(act))))
            for i, lay in enumerate(list(self.layouts)):
                watch.append(("layout", i, vk_of(lay.switch_key)))
            seen_vk = set()
            for kind, ref, vk in watch:
                if not vk or vk in seen_vk:
                    continue
                seen_vk.add(vk)
                token = f"{kind}:{ref}:{vk}"
                down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                was = token in self._key_was_down
                fire = False
                if down and not was:
                    self._key_was_down.add(token)
                    # 格子类：按下后 90ms 开始连发；其它键更保守
                    delay = 0.09 if (kind == "act" and ref in cell_like) else 0.22
                    next_fire[token] = now + delay
                    fire = True
                elif down and was and now >= next_fire.get(token, now + 1):
                    # 格子连发约 22 次/秒；其它约 8 次/秒
                    rate = 0.045 if (kind == "act" and ref in cell_like) else 0.12
                    next_fire[token] = now + rate
                    fire = True
                elif not down and was:
                    self._key_was_down.discard(token)
                    next_fire.pop(token, None)
                if fire:
                    # 钩子已在工作时，轮询不再重复触发按下，只保留长按连跳格子
                    if self._hook_ok and not (self.mouse_on and ref in (
                        "cell_left", "cell_right", "cell_up", "cell_down",
                        "region_left", "region_right", "region_up", "region_down",
                    ) and was):
                        continue
                    if ref == "toggle_mouse" and was:
                        continue
                    if kind == "layout":
                        self.after(0, lambda idx=ref: self.switch_layout(idx))
                    else:
                        self.after(0, lambda a=ref: self._dispatch_action(a))
            time.sleep(0.003 if self.mouse_on else 0.012)

    # ----- 动作分发 -----
    def _dispatch_action(self, act):
        now = time.time()
        # Tk 与轮询双通道防抖：格子动作要短，否则长按连跳会被 0.2s 卡死
        cell_acts = {
            "cell_left", "cell_right", "cell_up", "cell_down",
            "cell_home", "cell_again", "prev_region", "next_region",
        }
        gap = 0.028 if act in cell_acts else 0.18
        if now - self._last_action_fire.get(act, 0) < gap:
            return
        self._last_action_fire[act] = now
        if act == "toggle_mouse":
            self.toggle_active()
            return
        if act == "toggle_overlay":
            self.toggle_overlay()
            return
        if act == "toggle_global_lock":
            self.toggle_global_lock()
            return
        if act == "prev_layout":
            self.switch_layout(self.layout_i - 1)
            return
        if act == "next_layout":
            self.switch_layout(self.layout_i + 1)
            return
        if not self.mouse_on:
            return
        if act == "cell_right":
            self.step_cell(1)
        elif act == "cell_left":
            self.step_cell(-1)
        elif act == "cell_down":
            self.step_row(1)
        elif act == "cell_up":
            self.step_row(-1)
        elif act == "next_region":
            self.select_region(self.cur + 1)
            self.move_to_current()
        elif act == "prev_region":
            self.select_region(self.cur - 1)
            self.move_to_current()
        elif act == "cell_again":
            self.move_to_current()
        elif act == "cell_home":
            self.cell_index = 0
            self.move_to_current()

    def _goto_linked_region(self, link_idx, place):
        """切到链接区并把 cell_index 放到合理邻格。place: 从哪一侧进入目标区。
        不调用 select_region，避免把 cell_index 重置为 0 并打断连跳。"""
        regs = self.regions()
        if link_idx is None or not (0 <= link_idx < len(regs)):
            return False
        self.cur = link_idx
        res = compute_region(self.R())
        if not res.points:
            self._refresh_region_list()
            self._redraw()
            return True
        cols, rows = res.cols, res.rows
        if place == "left":  # 从右缘进左邻 → 同行最左
            row = min(max(getattr(self, "_cross_row", 0), 0), rows - 1)
            self.cell_index = row * cols + 0
        elif place == "right":  # 从左缘进右邻 → 同行最右
            row = min(max(getattr(self, "_cross_row", 0), 0), rows - 1)
            self.cell_index = row * cols + (cols - 1)
        elif place == "top":  # 从下缘进上邻 → 同列最上
            col = min(max(getattr(self, "_cross_col", 0), 0), cols - 1)
            self.cell_index = 0 * cols + col
        elif place == "bottom":  # 从上缘进下邻 → 同列最下
            col = min(max(getattr(self, "_cross_col", 0), 0), cols - 1)
            self.cell_index = (rows - 1) * cols + col
        else:
            self.cell_index = 0
        # 绿/白当前区与列表高亮立刻更新，再移鼠
        self._refresh_region_list()
        self._redraw()
        self.move_to_current()
        return True

    def _linked_neighbor_indices(self, reg):
        """本区所有已链接目标下标（四个方向字段去重）。"""
        regs = self.regions()
        out = []
        seen = set()
        for attr in ("link_left", "link_right", "link_up", "link_down"):
            j = getattr(reg, attr, None)
            if j is None or not (0 <= j < len(regs)) or j in seen:
                continue
            seen.add(j)
            out.append(j)
        return out

    def _direction_by_centers(self, a, b):
        """按两区中心相对位置得到主方向：left/right/up/down。"""
        ax, ay = a.center()
        bx, by = b.center()
        dx, dy = bx - ax, by - ay
        if abs(dx) >= abs(dy):
            return "right" if dx >= 0 else "left"
        return "down" if dy >= 0 else "up"

    def _edge_cross_or_block(self, direction):
        """跨区开：按链接+中心相对位置决定是否跨到邻区；该方向无邻区则卡在边缘。
        返回 True=已处理（跨区或卡住）；False=总开关关，交给调用方环绕。
        """
        if not self.cross_region:
            return False
        r = self.R()
        regs = self.regions()
        place = {
            "left": "right",
            "right": "left",
            "up": "bottom",
            "down": "top",
        }[direction]
        # 在所有已链接邻区里，找中心落在该方向上的目标（可有多个时取中心最近）
        cands = []
        for j in self._linked_neighbor_indices(r):
            if self._direction_by_centers(r, regs[j]) == direction:
                bx, by = regs[j].center()
                ax, ay = r.center()
                dist = (bx - ax) ** 2 + (by - ay) ** 2
                cands.append((dist, j))
        if cands:
            cands.sort()
            if self._goto_linked_region(cands[0][1], place):
                return True
        # 该方向没有中心相符的链接邻区 → 卡在边缘，不环绕
        return True

    def step_cell(self, delta):
        res = compute_region(self.R())
        if not res.points:
            return
        cols = res.cols
        row, col = divmod(self.cell_index, cols)
        ncol = col + delta
        if ncol < 0 or ncol >= cols:
            self._cross_row = row
            self._cross_col = col
            direction = "right" if ncol >= cols else "left"
            if self._edge_cross_or_block(direction):
                # 已跨区，或跨区开着但无链接而卡住
                return
            # 总开关关：保持旧环绕
            col = ncol % cols
        else:
            col = ncol
        self.cell_index = row * cols + col
        self.move_to_current()

    def step_row(self, delta):
        res = compute_region(self.R())
        if not res.points:
            return
        cols = res.cols
        row, col = divmod(self.cell_index, cols)
        nrow = row + delta
        if nrow < 0 or nrow >= res.rows:
            self._cross_row = row
            self._cross_col = col
            direction = "down" if nrow >= res.rows else "up"
            if self._edge_cross_or_block(direction):
                return
            row = nrow % res.rows
        else:
            row = nrow
        self.cell_index = row * cols + col
        self.move_to_current()

    def move_to_current(self):
        res = compute_region(self.R())
        if not res.points:
            return
        self.cell_index %= len(res.points)
        x, y = res.points[self.cell_index]
        sx, sy = self.image_to_screen(x, y)
        # 格子连跳：极快真实滑动；不整画布重绘、不反复 pin 辅助线（这两项最拖慢连续移动）
        send_cursor(sx, sy, fast=True)
        self.status_var.set(
            f"{self.L().name}/{res.name}  #{self.cell_index}  屏({sx},{sy})"
        )
        if not self.mouse_on:
            if self.overlay:
                self.overlay.pin_top()
            self._redraw()

    # ----- 重绘（复用 PhotoImage） -----
    def _redraw(self):
        self.canvas.delete("all")
        self.scale, self.offset_x, self.offset_y = self._fit_transform()
        if self.image is not None:
            disp_w = max(1, int(self.image.width * self.scale))
            disp_h = max(1, int(self.image.height * self.scale))
            img_token = id(self.image)
            need_new = (
                getattr(self, "_tk_image_size", None) != (disp_w, disp_h)
                or self.tk_image is None
                or getattr(self, "_tk_image_src", None) != img_token
            )
            if need_new:
                shown = self.image.resize((disp_w, disp_h), Image.Resampling.BILINEAR)
                old_img = self.tk_image
                self.tk_image = ImageTk.PhotoImage(shown)
                self._tk_image_size = (disp_w, disp_h)
                self._tk_image_src = id(self.image)
                if old_img is not None:
                    try:
                        self.canvas.delete("bg_image")
                    except Exception:
                        pass
                    del old_img
            self.canvas.create_image(
                self.offset_x, self.offset_y, image=self.tk_image, anchor="nw", tags="bg_image",
            )

        for i, reg in enumerate(self.regions()):
            color = self.COLOR_ACTIVE if i == self.cur else self.COLOR_INACTIVE
            self._draw_region(reg, color, i == self.cur, i)
        self._draw_link_arrows()

        r = self.R()
        res = compute_region(r)
        self.region_label.config(
            text=f"({r.rx1:.0f}, {r.ry1:.0f}) - ({r.rx2:.0f}, {r.ry2:.0f})  "
                 f"{r.rx2 - r.rx1:.0f}×{r.ry2 - r.ry1:.0f}"
        )
        extra = f"格#{self.cell_index % len(res.points)}" if res.points else ""
        self.info_var.set(
            f"界面 {self.L().name}  |  {r.name} {res.cols}×{res.rows}\n"
            f"格子 {res.cell_w}×{res.cell_h}  步进 {res.step_x}/{res.step_y}\n"
            f"目标分辨率 {self.target_resolution()[0]}×{self.target_resolution()[1]}  {extra}"
        )
        # 窗口标题固定为「Controller Bagbuddy」，界面名只显示在信息区
        self.title("Controller Bagbuddy")
        self.refresh_overlay()
        self._restore_feature_markers_after_redraw()

    def _draw_region(self, reg: Region, color, active, idx):
        res = compute_region(reg)
        x1, y1 = self.image_to_canvas(reg.rx1, reg.ry1)
        x2, y2 = self.image_to_canvas(reg.rx2, reg.ry2)
        s = max(float(getattr(self, "scale", 1.0) or 1.0), 0.05)
        border_w = max(1, int(round((3 if active else 1) * max(s, 0.35))))
        font_px = max(7, int(round(9 * max(s, 0.5))))
        dash_len = max(2, int(round(3 * max(s, 0.4))))
        r_outer = max(3.0, 6.0 * s)
        r_inner = max(1.5, 3.0 * s)
        handle = max(2.5, 4.0 * s)
        sel_w = max(1, int(round(2 * max(s, 0.5))))

        self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=border_w)
        self.canvas.create_text(
            x1 + 4 * max(s, 0.5), y1 + 4 * max(s, 0.5), text=f"{idx + 1}.{reg.name}", fill=color,
            anchor="nw", font=("Segoe UI", font_px, "bold"),
        )
        if not active:
            return
        for c in range(1, res.cols):
            xx = reg.rx1 + c * res.step_x
            ax, ay = self.image_to_canvas(xx, reg.ry1)
            bx, by = self.image_to_canvas(xx, reg.ry2)
            self.canvas.create_line(ax, ay, bx, by, fill=color, dash=(dash_len, dash_len))
        for row in range(1, res.rows):
            yy = reg.ry1 + row * res.step_y
            ax, ay = self.image_to_canvas(reg.rx1, yy)
            bx, by = self.image_to_canvas(reg.rx2, yy)
            self.canvas.create_line(ax, ay, bx, by, fill=color, dash=(dash_len, dash_len))
        n = max(len(res.points), 1)
        for i, (px, py) in enumerate(res.points):
            cx, cy = self.image_to_canvas(px, py)
            if i == self.cell_index % n:
                self.canvas.create_oval(cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer, outline="#ff1744", width=sel_w)
                self.canvas.create_oval(cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner, fill="#ff1744", outline="")
            else:
                self.canvas.create_oval(cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner, fill="#ffeb3b", outline="")
        for hx, hy in ((reg.rx1, reg.ry1), (reg.rx2, reg.ry1), (reg.rx1, reg.ry2), (reg.rx2, reg.ry2)):
            cx, cy = self.image_to_canvas(hx, hy)
            self.canvas.create_rectangle(cx - handle, cy - handle, cx + handle, cy + handle, fill=color, outline="white")

    def _draw_arrow_head(self, x0, y0, x1, y1, size, color):
        """在 (x1,y1) 画指向该点的三角形箭头（从 (x0,y0) 方向来）。"""
        ang = math.atan2(y1 - y0, x1 - x0)
        a1 = ang + math.pi * 0.82
        a2 = ang - math.pi * 0.82
        p1x = x1 + size * math.cos(a1)
        p1y = y1 + size * math.sin(a1)
        p2x = x1 + size * math.cos(a2)
        p2y = y1 + size * math.sin(a2)
        self.canvas.create_polygon(
            x1, y1, p1x, p1y, p2x, p2y,
            fill=color, outline=color, tags="link_arrow",
        )

    def _draw_link_arrows(self):
        """在编辑画布上画醒目的区域链接箭头（不影响拖拽命中，命中仍按区域矩形）。"""
        regs = self.regions()
        n = len(regs)
        if n < 2:
            return
        # frozenset({i,j}) → [(from, to, label), ...]；双向只画一条双箭头
        edges = {}
        for i, r in enumerate(regs):
            for attr, label in (
                ("link_right", "右"), ("link_left", "左"),
                ("link_down", "下"), ("link_up", "上"),
            ):
                j = getattr(r, attr, None)
                if j is None or not (0 <= j < n) or j == i:
                    continue
                key = frozenset((i, j))
                edges.setdefault(key, []).append((i, j, label))
        if not edges:
            return
        s = max(float(getattr(self, "scale", 1.0) or 1.0), 0.05)
        width = max(3, int(round(3.5 * max(s, 0.4))))
        ah = max(10, int(round(14 * max(s, 0.4))))
        font_px = max(8, int(round(9 * max(s, 0.5))))
        color = "#ffea00"
        for items in edges.values():
            i, j, _ = items[0]
            ax, ay = regs[i].center()
            bx, by = regs[j].center()
            x1, y1 = self.image_to_canvas(ax, ay)
            x2, y2 = self.image_to_canvas(bx, by)
            self.canvas.create_line(
                x1, y1, x2, y2, fill=color, width=width,
                tags="link_arrow",
            )
            # 两端箭头（双向）
            self._draw_arrow_head(x1, y1, x2, y2, ah, color)
            self._draw_arrow_head(x2, y2, x1, y1, ah, color)
            # 旁标方向字
            labels = []
            for fi, tj, lab in items:
                if fi == i:
                    labels.append(lab)
                else:
                    # 反向：用对向字更直观
                    opp = {"左": "右", "右": "左", "上": "下", "下": "上"}.get(lab, lab)
                    if opp not in labels:
                        labels.append(opp)
            mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
            tip = "/".join(labels) if labels else ""
            if tip:
                self.canvas.create_text(
                    mx, my - max(8, int(10 * s)), text=tip, fill=color,
                    font=("Segoe UI", font_px, "bold"), tags="link_arrow",
                )

    def _layouts_payload(self):
        self._sync_from_ui()
        out = []
        for lay in self.layouts:
            out.append({
                "name": lay.name,
                "switch_key": lay.switch_key,
                "res_w": lay.res_w,
                "res_h": lay.res_h,
                "regions": [region_to_dict(r) for r in lay.regions],
                "feature_points": feature_points_from_list(getattr(lay, "feature_points", None)),
            })
        return out

    def _config_payload(self):
        return {
            "layouts": self._layouts_payload(),
            "hotkeys": dict(self.hotkeys),
            "cross_region": bool(self.cross_region_var.get()) if hasattr(self, "cross_region_var") else bool(self.cross_region),
        }

    def _config_path(self, name=None):
        name = name or self.active_config or DEFAULT_CONFIG_NAME
        safe = self._safe_config_name(name)
        return os.path.join(CONFIGS_DIR, f"{safe}.json")

    @staticmethod
    def _safe_config_name(name: str) -> str:
        name = (name or "").strip()
        for ch in '\\/:*?"<>|':
            name = name.replace(ch, "_")
        name = name.strip(". ")
        return name or DEFAULT_CONFIG_NAME

    def _list_config_names(self):
        try:
            os.makedirs(CONFIGS_DIR, exist_ok=True)
        except Exception:
            pass
        names = []
        try:
            for fn in sorted(os.listdir(CONFIGS_DIR)):
                if fn.lower().endswith(".json"):
                    names.append(fn[:-5])
        except Exception:
            pass
        if not names:
            names = [DEFAULT_CONFIG_NAME]
        return names

    def _write_active_config_name(self, name):
        name = self._safe_config_name(name)
        try:
            with open(ACTIVE_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump({"active": name}, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        self.active_config = name
        self.config_name_var.set(name)
        self.config_combo_var.set(name)

    def _read_active_config_name(self):
        try:
            with open(ACTIVE_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return self._safe_config_name(data.get("active") or DEFAULT_CONFIG_NAME)
            if isinstance(data, str):
                return self._safe_config_name(data)
        except Exception:
            pass
        return DEFAULT_CONFIG_NAME

    def _ensure_configs_migrated(self):
        """旧 layouts.json/hotkeys.json 且 configs 为空时迁入 default。"""
        try:
            os.makedirs(CONFIGS_DIR, exist_ok=True)
        except Exception:
            pass
        existing = []
        try:
            existing = [f for f in os.listdir(CONFIGS_DIR) if f.lower().endswith(".json")]
        except Exception:
            pass
        if existing:
            return
        layouts_data = None
        if os.path.isfile(LAYOUT_FILE):
            try:
                with open(LAYOUT_FILE, "r", encoding="utf-8") as f:
                    layouts_data = json.load(f)
            except Exception:
                layouts_data = None
        hotkeys = load_hotkeys()
        if layouts_data is None:
            payload = {
                "layouts": [{
                    "name": "背包", "switch_key": "",
                    "res_w": "1920", "res_h": "1080",
                    "regions": [region_to_dict(Region())],
                    "feature_points": [],
                }],
                "hotkeys": hotkeys,
                "cross_region": False,
            }
        else:
            items = layouts_data.get("layouts") if isinstance(layouts_data, dict) else layouts_data
            if isinstance(layouts_data, dict) and isinstance(layouts_data.get("hotkeys"), dict):
                hk = dict(DEFAULT_KEYS)
                for k, v in layouts_data["hotkeys"].items():
                    if k in hk and isinstance(v, str):
                        hk[k] = norm_keysym(v) if v else ""
                hotkeys = hk
            cross = bool(layouts_data.get("cross_region", False)) if isinstance(layouts_data, dict) else False
            payload = {
                "layouts": items or [],
                "hotkeys": hotkeys,
                "cross_region": cross,
            }
        path = os.path.join(CONFIGS_DIR, f"{DEFAULT_CONFIG_NAME}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        self._write_active_config_name(DEFAULT_CONFIG_NAME)

    def _apply_config_data(self, data: dict):
        items = data.get("layouts") if isinstance(data, dict) else None
        if items:
            self.layouts = [layout_from_dict(x) for x in items]
            self.layout_i = 0
            self.cur = 0
        else:
            self.layouts = [Layout(name="背包", switch_key="")]
            self.layout_i = 0
            self.cur = 0
        if isinstance(data, dict) and isinstance(data.get("hotkeys"), dict):
            keys = dict(DEFAULT_KEYS)
            for k, v in data["hotkeys"].items():
                if k in keys and isinstance(v, str):
                    keys[k] = norm_keysym(v) if v else ""
            self.hotkeys = keys
        else:
            self.hotkeys = load_hotkeys()
        self.cross_region = bool(data.get("cross_region", False)) if isinstance(data, dict) else False
        # 旧配置已有区域链接但未开跨区：自动打开，否则边缘只会 % 环绕
        if not self.cross_region:
            for lay in getattr(self, "layouts", []) or []:
                for reg in getattr(lay, "regions", []) or []:
                    if any(getattr(reg, a, None) is not None for a in ("link_left", "link_right", "link_up", "link_down")):
                        self.cross_region = True
                        break
                if self.cross_region:
                    break
        if hasattr(self, "cross_region_var"):
            self.cross_region_var.set(1 if self.cross_region else 0)

    def _load_active_config(self):
        self._ensure_configs_migrated()
        name = self._read_active_config_name()
        path = self._config_path(name)
        if not os.path.isfile(path):
            # 回退到列表第一个
            names = self._list_config_names()
            name = names[0]
            path = self._config_path(name)
        data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        self.active_config = name
        self.config_name_var.set(name)
        self.config_combo_var.set(name)
        self._apply_config_data(data if isinstance(data, dict) else {})
        self._write_active_config_name(name)

    def _refresh_config_ui(self):
        if not hasattr(self, "config_combo"):
            return
        names = self._list_config_names()
        self.config_combo["values"] = names
        if self.active_config not in names:
            self.active_config = names[0]
        self.config_combo_var.set(self.active_config)
        self.config_name_var.set(self.active_config)

    # ----- 存盘 -----
    def save_layouts_file(self):
        try:
            os.makedirs(CONFIGS_DIR, exist_ok=True)
        except Exception:
            pass
        path = self._config_path()
        payload = self._config_payload()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        # 同步热键到根目录兼容文件（可选）
        try:
            save_hotkeys(self.hotkeys)
        except Exception:
            pass
        self._write_active_config_name(self.active_config)
        self._refresh_config_ui()
        self.status_var.set(f"已保存配置 {self.active_config}.json")

    def _load_layouts_file(self):
        # 兼容旧调用：转调多配置加载
        self._load_active_config()

    def switch_config_prompt(self):
        names = self._list_config_names()
        cur = self.active_config
        target = self.config_combo_var.get().strip() or cur
        if target == cur:
            # 若下拉未变，弹对话框选
            name = simpledialog.askstring(
                "切换配置",
                f"当前：{cur}\n可用：{', '.join(names)}\n输入要切换的配置名：",
                initialvalue=target, parent=self,
            )
            if not name:
                return
            target = self._safe_config_name(name)
        if target not in names:
            messagebox.showinfo("提示", f"配置「{target}」不存在。")
            self.config_combo_var.set(cur)
            return
        if target == cur:
            return
        if not messagebox.askyesno("切换配置", f"切换前是否保存当前配置「{cur}」？\n选「否」则丢弃未保存改动。", parent=self):
            pass
        else:
            try:
                self.save_layouts_file()
            except Exception:
                pass
        self._switch_to_config(target)

    def _switch_to_config(self, name):
        name = self._safe_config_name(name)
        path = self._config_path(name)
        if not os.path.isfile(path):
            messagebox.showinfo("提示", f"找不到配置文件 {name}.json")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("错误", f"读取配置失败：{e}")
            return
        self.active_config = name
        self._apply_config_data(data if isinstance(data, dict) else {})
        self._write_active_config_name(name)
        self.apply_hotkeys(self.hotkeys)
        self._build_hotkey_bar()
        self._show_layout()
        self._redraw()
        self._refresh_config_ui()
        self.status_var.set(f"已切换到配置 {name}")

    def new_config(self):
        name = simpledialog.askstring("新建配置", "新配置名称：", parent=self)
        if name is None:
            return
        name = self._safe_config_name(name)
        path = self._config_path(name)
        if os.path.isfile(path):
            messagebox.showinfo("提示", f"配置「{name}」已存在。")
            return
        if messagebox.askyesno("新建配置", f"是否先保存当前配置「{self.active_config}」？", parent=self):
            try:
                self.save_layouts_file()
            except Exception:
                pass
        # 以空白默认布局新建
        payload = {
            "layouts": [{
                "name": "背包", "switch_key": "",
                "res_w": "1920", "res_h": "1080",
                "regions": [region_to_dict(Region())],
            }],
            "hotkeys": dict(DEFAULT_KEYS),
            "cross_region": False,
        }
        try:
            os.makedirs(CONFIGS_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as e:
            messagebox.showerror("错误", f"创建失败：{e}")
            return
        self._switch_to_config(name)

    def save_config_as(self):
        name = simpledialog.askstring("另存为", "新配置名称：", initialvalue=self.active_config, parent=self)
        if name is None:
            return
        name = self._safe_config_name(name)
        path = self._config_path(name)
        if os.path.isfile(path) and name != self.active_config:
            if not messagebox.askyesno("另存为", f"配置「{name}」已存在，覆盖？", parent=self):
                return
        self.active_config = name
        try:
            self.save_layouts_file()
        except Exception as e:
            messagebox.showerror("错误", f"保存失败：{e}")
            return
        self._refresh_config_ui()
        self.status_var.set(f"已另存为 {name}.json")

    def delete_config(self):
        names = self._list_config_names()
        if len(names) <= 1:
            messagebox.showinfo("提示", "不能删除最后一个配置。")
            return
        name = self.active_config
        if not messagebox.askyesno("删除配置", f"确定删除配置「{name}」？此操作不可恢复。", parent=self):
            return
        path = self._config_path(name)
        try:
            if os.path.isfile(path):
                os.remove(path)
        except Exception as e:
            messagebox.showerror("错误", f"删除失败：{e}")
            return
        remain = self._list_config_names()
        self._switch_to_config(remain[0])

    def copy_json(self):
        payload = self._config_payload()
        self.clipboard_clear()
        self.clipboard_append(json.dumps(payload, ensure_ascii=False, indent=2))
        self.status_var.set("已复制")

    def export_json(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON", "*.json")],
            initialfile=f"{self.active_config}.json",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._config_payload(), f, ensure_ascii=False, indent=2)
        self.status_var.set(f"已保存 {os.path.basename(path)}")

    # ----- 退出 -----
    def _on_close(self):
        try:
            self.save_layouts_file()
        except Exception:
            pass
        self._poll_stop.set()
        if self.overlay:
            self.overlay.hide()
        self.destroy()



if __name__ == "__main__":
    _ensure_admin()
    App().mainloop()
