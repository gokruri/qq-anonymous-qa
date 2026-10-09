#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用只读命令 0xecf_1 (getQuestionDetail) 定出 str_quest_id / uint64_quest_time / uint64_uin 的字段号。
0xecf_1 是「看提问详情」，纯读，可以放心用真实 quest_id。
成功判据：响应体里出现问题正文（你是谁）或 quest_id 本身。
"""
import os
import sys, json, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe import f_varint, f_bytes, decode

UIN = 10001        # ← 改成你自己 bot 的 QQ 号
QID = "U_f724c86aff040600545f3134335f363736343534393032"
QTIME = 1791249050

def call(body, cmd, sub=1):
    env = f_varint(1, cmd) + f_varint(2, sub) + f_bytes(4, body)
    req = urllib.request.Request(
        "http://127.0.0.1:3000/send_packet",
        data=json.dumps({"cmd": "OidbSvc.0x%x_%d" % (cmd, sub), "data": env.hex()}).encode(),
        headers={"content-type": "application/json"})
    r = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
    hx = r.get("data")
    if not hx:
        return None, "", b""
    e = dict((f, v) for f, w, v in decode(bytes.fromhex(hx)))
    return e.get(3), (e.get(5) or b"").decode("utf-8", "replace")[:60], (e.get(4) or b"")

def hit(resp):
    return (b"\xe4\xbd\xa0\xe6\x98\xaf\xe8\xb0\x81" in resp) or (QID.encode() in resp)

print("=== 0xecf_1 扫描：uin 放 varint、quest_id 放 bytes、time 放 varint ===")
print("%-38s %-6s %s" % ("组合", "Code", "响应体(前60字节hex) / 命中"))
found = []
for kq in range(1, 9):          # quest_id 的候选字段号
    for ku in range(1, 6):      # uin 的候选字段号
        if ku == kq:
            continue
        body = f_bytes(kq, QID) + f_varint(ku, UIN) + f_varint(3, QTIME)
        code, info, resp = call(body, 0xECF, 1)
        flag = "★命中★" if hit(resp) else ""
        if resp or code not in (19, 16):
            print("  qid@f%-2d uin@f%-2d Code=%-6s len=%-5d %s %s"
                  % (kq, ku, code, len(resp), resp.hex()[:60], flag))
        if flag:
            found.append((kq, ku))
        time.sleep(0.25)
print()
print("命中组合:", found)
