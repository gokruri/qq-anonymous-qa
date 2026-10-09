#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
匿问我答 OidbSvc.0xec4_1 字段号探测器 —— 纯只读。

只做一件事：搞清楚 OIDB 请求信封 + getAnswerList 业务体的 protobuf 字段号。
不提交任何回答、不删提问、不点赞。唯一调用的业务命令是 0xec4（取提问列表）。

已确认（本次实测）：
  send_packet 的 data = **完整 OIDB 信封的 hex**，不是业务体。
  信封请求：f1=uint32 cmd(3780=0xec4)  f2=uint32 subcmd(1)  f4=bytes body
  信封响应：f1=cmd  f2=subcmd  f3=ErrorCode  f4=bytes body  f5=string ErrorInfo
  未带 body：08c41d1001 -> ErrorCode=0, body=28924e
  信封里 cmd 写错/解密失败 -> ErrorCode=145 "[oidb] invalid pkg and decode req failed"
  cmd 对但 subcmd 错 -> ErrorCode=150 "[oidb] no privilege"

判定：
  某字段号用错 wire type -> 服务端 protobuf 解析失败 -> ErrorCode=145 invalid pkg
  字段号不存在/未知       -> 被静默忽略 -> ErrorCode 不为 145
"""
import json, time, sys, urllib.request, urllib.error

HTTP = "http://127.0.0.1:3000"
CMD_EC4 = 3780          # 0xec4
SUB_EC4 = 1
UIN_BOT = 10001      # ← 改成你自己 bot 的 QQ 号
UIN_OWNER = 10002    # ← 对照用的另一个号（可选）

ERR_INVALID_PKG = 145
ERR_NO_PRIV = 150

# ─────────────────────────── protobuf 手写编解码 ───────────────────────────
def uvarint(n):
    if n < 0:
        raise ValueError("neg")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)

def tag(field, wire):
    return uvarint((field << 3) | wire)

def f_varint(field, val):
    return tag(field, 0) + uvarint(val)

def f_bytes(field, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return tag(field, 2) + uvarint(len(data)) + data

def read_varint(buf, i):
    v = 0; s = 0
    while True:
        if i >= len(buf):
            raise ValueError("eof varint")
        b = buf[i]; i += 1
        v |= (b & 0x7F) << s
        s += 7
        if not (b & 0x80):
            return v, i

def decode(buf, depth=0):
    """宽容解码：返回 [(field, wire, value)]；value 对 wire2 是 bytes，其余是 int。"""
    out = []
    i = 0
    while i < len(buf):
        k, i = read_varint(buf, i)
        f, w = k >> 3, k & 7
        if w == 0:
            v, i = read_varint(buf, i)
            out.append((f, w, v))
        elif w == 2:
            n, i = read_varint(buf, i)
            out.append((f, w, buf[i:i + n]))
            i += n
        elif w == 5:
            out.append((f, w, buf[i:i + 4])); i += 4
        elif w == 1:
            out.append((f, w, buf[i:i + 8])); i += 8
        else:
            raise ValueError("bad wire %d at field %d" % (w, f))
    return out

def show(items, indent="    "):
    lines = []
    for f, w, v in items:
        if w == 2:
            try:
                s = v.decode("utf-8")
                printable = all(32 <= ord(c) < 127 or c in "\n\t" for c in s) and s
            except Exception:
                printable = None
            if printable:
                lines.append("%sf%d [bytes %d] %r" % (indent, f, len(v), s[:300]))
            else:
                lines.append("%sf%d [bytes %d] %s" % (indent, f, len(v), v.hex()))
                try:
                    sub = decode(v)
                    if sub:
                        lines.append("%s  └─ 可再解一层:" % indent)
                        lines.extend(show(sub, indent + "     "))
                except Exception:
                    pass
        else:
            lines.append("%sf%d [varint] %d" % (indent, f, v))
    return lines

# ─────────────────────────── OIDB 调用 ───────────────────────────
def send_packet(cmd, data_hex, timeout=20):
    req = urllib.request.Request(
        HTTP + "/send_packet",
        data=json.dumps({"cmd": cmd, "data": data_hex}).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def oidb(body: bytes, cmd=CMD_EC4, sub=SUB_EC4, label=""):
    env = f_varint(1, cmd) + f_varint(2, sub) + f_bytes(4, body)
    r = send_packet("OidbSvc.0x%x_%d" % (cmd, sub), env.hex())
    hx = r.get("data")
    if not hx:
        return {"raw": r, "ok": False}
    env_items = decode(bytes.fromhex(hx))
    d = {"ok": True, "label": label, "req_body": body.hex(), "env": {}}
    for f, w, v in env_items:
        d["env"][f] = v
    d["code"] = d["env"].get(3)
    d["info"] = (d["env"].get(5) or b"").decode("utf-8", "replace")
    d["body"] = d["env"].get(4) or b""
    return d

def verdict(d):
    if not d.get("ok"):
        return "调用失败 %s" % d.get("raw")
    if d["code"] == ERR_INVALID_PKG:
        return "❌ 字段号/类型错 (145 invalid pkg)"
    if d["code"] == ERR_NO_PRIV:
        return "⚠️ 无权限 (150)"
    return "✅ 通过 (ErrorCode=%s)" % d["code"]

# ─────────────────────────── 阶段 1：字段号/类型扫描 ───────────────────────────
def phase_scan(cmd=CMD_EC4, sub=SUB_EC4, max_field=12, pause=0.35):
    print("=" * 78)
    print("阶段1：字段号 × wire type 扫描（cmd=0x%x sub=%d）" % (cmd, sub))
    print("  varint(值=1)  与  bytes(内容=1字节 0x01)  各试一遍")
    print("  145 = 该字段号存在但类型不匹配 ／ 其它 = 字段号不存在(被忽略) 或 类型正确")
    print("=" * 78)
    table = {}
    for f in range(1, max_field + 1):
        row = {}
        for wname, body in (("varint", f_varint(f, 1)), ("bytes", f_bytes(f, b"\x01"))):
            d = oidb(body, cmd, sub)
            row[wname] = d
            time.sleep(pause)
        v_ok = row["varint"].get("code")
        b_ok = row["bytes"].get("code")
        guess = "?"
        if v_ok == ERR_INVALID_PKG and b_ok != ERR_INVALID_PKG:
            guess = "bytes/string 类（用 varint 发会报错）"
        elif b_ok == ERR_INVALID_PKG and v_ok != ERR_INVALID_PKG:
            guess = "varint 数字类（用 bytes 发会报错）"
        elif v_ok == ERR_INVALID_PKG and b_ok == ERR_INVALID_PKG:
            guess = "两种都报错 —— 该号不存在？(实为嵌套消息/需外层前置字段)"
        table[f] = (v_ok, b_ok, guess)
        print("  f%-2d  varint->%-4s  bytes->%-4s   %s" % (f, v_ok, b_ok, guess))
    return table

# ─────────────────────────── 阶段 2：按假设发真实请求 ───────────────────────────
def summarize(d, tag):
    print("-" * 78)
    print("【%s】req_body=%s" % (tag, d.get("req_body", "")))
    if not d.get("ok"):
        print("   ", d)
        return
    print("    ErrorCode=%s  ErrorInfo=%r  %s" % (d["code"], d["info"], verdict(d)))
    if d["body"]:
        print("    响应业务体 %d 字节: %s" % (len(d["body"]), d["body"].hex()))
        for ln in show(decode(d["body"])):
            print("  " + ln)
    else:
        print("    响应业务体: 空")

def phase_request(uin, guesses, extra_name=""):
    print()
    print("=" * 78)
    print("阶段2：用假定的字段号发 getAnswerList（uin=%d）%s" % (uin, extra_name))
    print("=" * 78)
    for name, body in guesses:
        d = oidb(body)
        summarize(d, name)
        time.sleep(0.4)

# ─────────────────────────── main ───────────────────────────
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if mode == "scan":
        phase_scan()
    elif mode == "smoke":
        # 冒烟：确认信封理解正确
        for lab, body in (("空 body", b""), ("未知字段 f1 varint=1", f_varint(1, 1)),
                          ("f1 bytes", f_bytes(1, b"\x01"))):
            summarize(oidb(body), lab)
            time.sleep(0.3)
