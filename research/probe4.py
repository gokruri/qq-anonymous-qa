#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段4：补全请求字段号（cookie/quest_num/comment_num）+ fetch_type 行为扫描。只读。"""
import os
import sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe import *
from readlist import get_list, dump

def brief(d):
    b = d.get("body", b"")
    if not b:
        return "code=%s 空体" % d.get("code")
    try:
        items = decode(b)
    except Exception:
        return "code=%s 无法解析" % d.get("code")
    nquest = sum(1 for f, w, v in items if f == 1 and w == 2)
    others = [(f, w, (v.decode("utf-8", "replace")[:70] if w == 2 else v)) for f, w, v in items if f != 1]
    return "code=%s bytes=%d 条数=%d 其他=%s" % (d.get("code"), len(b), nquest, others)

print("=" * 78)
print("A. fetch_type 扫描 0..6（bot 与 主人 各一遍）")
print("=" * 78)
for uin, who in ((UIN_BOT, "bot"), (UIN_OWNER, "主人")):
    for ft in range(0, 7):
        d = oidb(f_varint(1, uin) + f_varint(2, ft))
        print("  %-5s ft=%d  %s" % (who, ft, brief(d)))
        time.sleep(0.25)
    print()

print("=" * 78)
print("B. 请求还有没有 f3..f8？（在 f1+f2 基础上加字段，看是否有变化）")
print("=" * 78)
base = f_varint(1, UIN_BOT) + f_varint(2, 1)
db = oidb(base)
bk = (db.get("code"), db.get("body", b"").hex())
print("  基准: %s" % brief(db))
for k in range(3, 9):
    for enc, lab in ((f_varint(k, 0), "v0"), (f_varint(k, 1), "v1"),
                     (f_varint(k, 10), "v10"), (f_bytes(k, b"\x00"), "b\\x00"),
                     (f_bytes(k, b"x"), "b'x'")):
        d = oidb(base + enc)
        kk = (d.get("code"), d.get("body", b"").hex())
        if kk != bk:
            print("  ★ f%-2d %-6s -> %s" % (k, lab, brief(d)))
        time.sleep(0.2)
print("  （以上为与基准不同的）")

print()
print("=" * 78)
print("C. cookie 回灌：把响应 f4 的 cookie 作为请求字段发回去")
print("=" * 78)
cookie = None
for f, w, v in decode(db.get("body", b"")):
    if f == 4 and w == 2:
        cookie = v
print("  响应 f4 cookie = %r" % (cookie.decode("utf-8", "replace") if cookie else None))
if cookie:
    for k in range(3, 9):
        d = oidb(base + f_bytes(k, cookie))
        kk = (d.get("code"), d.get("body", b"").hex())
        if kk != bk:
            print("  ★ cookie 作为请求 f%d -> %s" % (k, brief(d)))
        time.sleep(0.25)
    print("  （以上为与基准不同的）")
