#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""0xec7_1 doAnswer 字段号消融实验。
⚠️ 全程使用**伪造的 quest_id**（指向不存在的 uin 999999999），任何请求都写不进真实提问。
"""
import os
import sys, json, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe import f_varint, f_bytes, decode

BOGUS = "U_deadbeefdeadbeef00545f3134335f393939393939393939"
REALTIME = 1791249050

def call(body, cmd=0xEC7, sub=1):
    env = f_varint(1, cmd) + f_varint(2, sub) + f_bytes(4, body)
    req = urllib.request.Request(
        "http://127.0.0.1:3000/send_packet",
        data=json.dumps({"cmd": "OidbSvc.0x%x_%d" % (cmd, sub), "data": env.hex()}).encode(),
        headers={"content-type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
    hx = r.get("data")
    if not hx:
        return ("调用失败", str(r)[:80], "")
    e = dict((f, v) for f, w, v in decode(bytes.fromhex(hx)))
    return (e.get(3), (e.get(5) or b"").decode("utf-8", "replace")[:60], (e.get(4) or b"").hex()[:60])

F = [("f1", lambda v: f_bytes(1, v)),
     ("f2", lambda v: f_bytes(2, v)),
     ("f3", lambda v: f_varint(3, REALTIME))]

def build(use):
    b = b""
    for name, enc in F:
        if name in use:
            b += enc(BOGUS if name != "f3" else REALTIME)
    return b

CASES = [[], ["f1"], ["f2"], ["f3"], ["f1", "f2"], ["f1", "f3"], ["f2", "f3"],
         ["f1", "f2", "f3"]]
print("命令 0xec7_1，quest_id 恒为伪造值，答案文本恒为 'PROBE'")
print("%-16s %-6s %-30s %s" % ("用了哪些字段", "Code", "ErrorInfo", "响应体"))
for use in CASES:
    body = build(use)
    code, info, rb = call(body)
    print("%-16s %-6s %-30s %s" % ("+".join(use) or "(空)", code, info or "-", rb or "-"))
    time.sleep(0.4)

print()
print("变体：字段号错位（同一组数据换字段号）——应给出不同的错误码")
variants = [
    ("全自然 f1+f2+f3", f_bytes(1, BOGUS) + f_bytes(2, "PROBE") + f_varint(3, REALTIME)),
    ("f1=ans, f2=id, f3=time", f_bytes(1, "PROBE") + f_bytes(2, BOGUS) + f_varint(3, REALTIME)),
    ("f1=id, f2=time(错类型), f3=ans", f_bytes(1, BOGUS) + f_varint(2, REALTIME) + f_bytes(3, "PROBE")),
    ("f1=id, f2=ans, f4=time", f_bytes(1, BOGUS) + f_bytes(2, "PROBE") + f_varint(4, REALTIME)),
    ("f1=id, f2=ans (无 time)", f_bytes(1, BOGUS) + f_bytes(2, "PROBE")),
    ("f1=id, f2=ans, f3=0", f_bytes(1, BOGUS) + f_bytes(2, "PROBE") + f_varint(3, 0)),
]
for lab, body in variants:
    code, info, rb = call(body)
    print("  %-30s Code=%-6s %s" % (lab, code, info or ""))
    time.sleep(0.4)
