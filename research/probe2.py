#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""行为探测：找一个字段号，使得响应从基线（resp_body=28924e / code=0）变成别的东西。
只调 0xec4（取列表），只读。"""
import os
import sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe import *

BASE = None

def probe(body, note):
    d = oidb(body)
    key = (d.get("code"), d.get("body", b"").hex(), d.get("info", ""))
    return key, d, note

def baseline():
    global BASE
    k, d, _ = probe(b"", "baseline")
    BASE = k
    print("基线: code=%s body=%s info=%r" % (k[0], k[1], k[2]))
    return k

def diff_scan(fields, modes, pause=0.25):
    hits = []
    for f in fields:
        for mname, val in modes.items():
            if mname == "varint":
                body = f_varint(f, val)
            else:
                body = f_bytes(f, val)
            k, d, note = probe(body, "f%d %s" % (f, mname))
            if k != BASE:
                hits.append((f, mname, k))
                print("  ★ 变化 f%-3d %-10s -> code=%-8s body=%-24s info=%r"
                      % (f, mname, k[0], k[1][:24], k[2][:60]))
            time.sleep(pause)
    if not hits:
        print("  （无任何字段引起变化 —— 单字段不足以触发业务逻辑）")
    return hits

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "flat"
    baseline()
    UIN = UIN_BOT
    if which == "flat":
        print("\n== A. 平坦字段扫描 f1..f64，值 = bot uin(%d) ==" % UIN)
        diff_scan(range(1, 65), {"varint": UIN})
        print("\n== B. 平坦字段扫描 f1..f32，值 = bytes(\"<BOT_UIN>\") ==")
        diff_scan(range(1, 33), {"bytes": b"<BOT_UIN>"})
        print("\n== C. 平坦字段扫描 f1..f32，varint 值 = 0 ==")
        diff_scan(range(1, 33), {"varint": 0})
        print("\n== D. 平坦字段扫描 f1..f32，varint 值 = 1 ==")
        diff_scan(range(1, 33), {"varint": 1})
    elif which == "nested":
        print("\n== E. 嵌套：外层 f1..f16 包一层内层 f1..f16 = uin ==")
        hits = []
        for outer in range(1, 17):
            for inner in range(1, 17):
                inner_msg = f_varint(inner, UIN)
                body = f_bytes(outer, inner_msg)
                k, d, note = probe(body, "outer%d.inner%d" % (outer, inner))
                if k != BASE:
                    hits.append((outer, inner, k))
                    print("  ★ outer f%-2d . inner f%-2d -> code=%-8s body=%s info=%r"
                          % (outer, inner, k[0], k[1][:24], k[2][:60]))
                time.sleep(0.2)
        if not hits:
            print("  （嵌套无命中）")
