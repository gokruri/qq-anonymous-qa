#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段3：确认 f1=uin，然后双字段扫描，找出让响应出现「提问列表」的组合。只读。"""
import os
import sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe import *

def call(body):
    d = oidb(body)
    return d

def line(body, d):
    return "  body=%-30s code=%-7s resp=%-34s %s" % (
        body.hex()[:30], d.get("code"), d.get("body", b"").hex()[:34], d.get("info", "")[:40])

def part_a():
    print("=" * 78)
    print("A. f1 是不是 uint64_uin（取列表的目标）")
    print("=" * 78)
    for uin, who in ((0, "0"), (1, "1"), (10001, "10001"),
                     (10001, "bot"), (10002, "对照号"),
                     (10003, "测试号"), (999999999, "不存在?/大号")):
        d = call(f_varint(1, uin))
        print("%-14s uin=%-12d %s" % (who, uin, line(f_varint(1, uin), d)))
        time.sleep(0.3)

def part_b(uin=UIN_BOT):
    print()
    print("=" * 78)
    print("B. 双字段：f1=uin(%d) + f_k，看响应是否变化/变长（找 fetch_type/cookie）" % uin)
    print("=" * 78)
    base_body = f_varint(1, uin)
    base = call(base_body)
    basek = (base.get("code"), base.get("body", b"").hex())
    print("基准 f1 only: code=%s resp=%s" % (basek[0], basek[1]))
    for k in range(2, 49):
        for mname, val, enc in (("varint", 0, "v0"), ("varint", 1, "v1"),
                                ("varint", 2, "v2"), ("varint", 3, "v3"),
                                ("bytes", b"\x00", "b00"), ("bytes", b"", "bempty")):
            body = base_body + (f_varint(k, val) if mname == "varint" else f_bytes(k, val))
            d = call(body)
            k2 = (d.get("code"), d.get("body", b"").hex())
            if k2 != basek:
                print("  ★ f1+%-2d %-6s %s" % (k, enc, line(body, d)))
            time.sleep(0.18)
    print("  （以上为全部与基准不同的组合）")

def part_c():
    print()
    print("=" * 78)
    print("C. 遍历 fetch_type 常见值 + 组合（f1=uin, f2/f3/f4 填默认）")
    print("=" * 78)
    uin = UIN_BOT
    combos = []
    for ft in range(0, 5):
        for fnum in (2, 3, 4, 5, 6):
            combos.append((fnum, ft))
    seen = set()
    for fnum, ft in combos:
        body = f_varint(1, uin) + f_varint(fnum, ft)
        d = call(body)
        k = (d.get("code"), d.get("body", b"").hex())
        if k in seen:
            continue
        seen.add(k)
        print("  f1+f%-2d=%d  %s" % (fnum, ft, line(body, d)))
        time.sleep(0.25)

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "a"):
        part_a()
    if which in ("all", "b"):
        part_b()
    if which in ("all", "c"):
        part_c()
