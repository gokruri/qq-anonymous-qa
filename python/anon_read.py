#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
匿问我答 —— 只读列表读取器（自包含，无依赖）
命令：OidbSvc.0xec4_1  getAnswerList

⚠️ 本工具**只读**：只调 0xec4 拉列表，绝不调 0xec8(addComment) 等写接口。
   零 token、零风险、不往资料卡写任何东西。

用法：
  python3 anon-read.py <uin> [每页条数=10] [最大页数=10]

字段号（2026-10 实测，见 匿名问答-字段号-实测.md）：
  OIDB 信封  请求 f1=cmd(3780) f2=sub(1) f4=bytes(body)
             响应 f1=cmd f2=sub f3=ErrorCode f4=bytes(body) f5=str(ErrorInfo)
  业务请求体 f1=uint64 uin          （必填，缺了回 ErrorCode 10002 参数错）
             f2=uint32 要拉几条      （实测=返回条数上限，1..10；≥11 服务端直接返空）
             f4=bytes  cookie        （翻页游标，把上一页响应的 f4 原样回灌）
  业务响应体 f1=rpt msg_quest   f3=提问总数   f4=bytes cookie(下一页)
             f5=ErrorCode(0 正常/10001 用户不存在/10002 参数错)
             f7=str 目标昵称
  单条 msg_quest（在响应 f1 里）：
             f1=quest_id  f2=问题正文  f4=提问时间(unix)  f5=回答正文
             f6=回答时间  f10=评论id  f14=?  f16=?  f18=32位hex令牌  f20=?
"""
import json
import sys
import time
import urllib.request

HTTP = "http://127.0.0.1:3000"
CMD, SUB = 3780, 1          # OidbSvc.0xec4_1
MAX_PAGE_SIZE = 10          # 实测上限：>10 服务端返回空
PAUSE = 0.6                 # 页间停顿，别撞限频

# ───────────────────────── protobuf 手写编解码 ─────────────────────────
def uvarint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)

def f_varint(field, val):
    return uvarint((field << 3) | 0) + uvarint(val)

def f_bytes(field, data):
    if isinstance(data, str):
        data = data.encode()
    return uvarint((field << 3) | 2) + uvarint(len(data)) + data

def _rv(buf, i):
    v = s = 0
    while True:
        b = buf[i]; i += 1
        v |= (b & 0x7F) << s; s += 7
        if not b & 0x80:
            return v, i

def decode(buf):
    out, i = [], 0
    while i < len(buf):
        k, i = _rv(buf, i)
        f, w = k >> 3, k & 7
        if w == 0:
            v, i = _rv(buf, i); out.append((f, w, v))
        elif w == 2:
            n, i = _rv(buf, i); out.append((f, w, buf[i:i + n])); i += n
        elif w == 5:
            out.append((f, w, buf[i:i + 4])); i += 4
        elif w == 1:
            out.append((f, w, buf[i:i + 8])); i += 8
        else:
            raise ValueError("bad wire type %d (field %d)" % (w, f))
    return out

def s(v):
    return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else v

# ───────────────────────── OIDB 调用 ─────────────────────────
def oidb_ec4(body):
    env = f_varint(1, CMD) + f_varint(2, SUB) + f_bytes(4, body)
    req = urllib.request.Request(
        HTTP + "/send_packet",
        data=json.dumps({"cmd": "OidbSvc.0x%x_%d" % (CMD, SUB),
                         "data": env.hex()}).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read().decode())
    hx = resp.get("data")
    if not hx:
        raise RuntimeError("send_packet 失败: %s" % resp)
    envd = dict((f, v) for f, w, v in decode(bytes.fromhex(hx)))
    return envd.get(3), s(envd.get(5, b"")), envd.get(4, b"")

def fetch_page(uin, count=MAX_PAGE_SIZE, cookie=None):
    body = f_varint(1, uin) + f_varint(2, count)
    if cookie:
        body += f_bytes(4, cookie)
    code, info, resp = oidb_ec4(body)
    if code:
        raise RuntimeError("OIDB ErrorCode=%s %s" % (code, info))
    items = decode(resp) if resp else []
    quests = [v for f, w, v in items if f == 1 and w == 2]
    outer = dict((f, v) for f, w, v in items if f != 1)
    return quests, outer

def parse_quest(q):
    var = dict((f, v) for f, w, v in decode(q) if w == 0)
    byt = dict((f, v) for f, w, v in decode(q) if w == 2)
    return {
        "quest_id": s(byt.get(1, b"")),
        "question": s(byt.get(2, b"")),
        "answer": s(byt.get(5, b"")),
        "ask_time": var.get(4),
        "answer_time": var.get(6),
        "comment_id": s(byt.get(10, b"")),
        "token": s(byt.get(18, b"")),
    }

def fetch_all(uin, page_size=MAX_PAGE_SIZE, max_pages=10, verbose=True):
    seen, out, cookie = set(), [], None
    for pg in range(1, max_pages + 1):
        quests, outer = fetch_page(uin, page_size, cookie)
        err = outer.get(5, 0)
        total = outer.get(3)
        ck = outer.get(4)
        new = 0
        for q in quests:
            item = parse_quest(q)
            if item["quest_id"] in seen:
                continue
            seen.add(item["quest_id"]); out.append(item); new += 1
        if verbose:
            print("  第%d页: 本页 %d 条 / 新增 %d / 累计 %d / 总数 %s / hasFinish=%s"
                  % (pg, len(quests), new, len(out), total,
                     "hasFinish=1" in s(ck) if ck else "?"))
        if err:
            if verbose:
                print("  业务 ErrorCode=%s（10001=该 uin 不存在，10002=参数错）" % err)
            break
        if not ck or "hasFinish=1" in s(ck):
            break
        cookie = ck
        time.sleep(PAUSE)
    return out

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    uin = int(sys.argv[1])
    page_size = int(sys.argv[2]) if len(sys.argv) > 2 else MAX_PAGE_SIZE
    max_pages = int(sys.argv[3]) if len(sys.argv) > 3 else 10
    print("=" * 78)
    print("匿问我答 只读列表  uin=%d  page_size=%d" % (uin, page_size))
    print("=" * 78)
    quests = fetch_all(uin, page_size, max_pages)
    print("-" * 78)
    print("共 %d 条提问（已答 %d / 未答 %d）"
          % (len(quests), sum(1 for q in quests if q["answer"]),
             sum(1 for q in quests if not q["answer"])))
    print("-" * 78)
    for i, q in enumerate(quests, 1):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(q["ask_time"])) if q["ask_time"] else "?"
        print("%2d. %s  [%s]" % (i, q["question"], when))
        print("    答: %s" % (q["answer"] if q["answer"] else "（未回答）"))
        print("    quest_id=%s" % q["quest_id"])
    return 0

if __name__ == "__main__":
    sys.exit(main())
