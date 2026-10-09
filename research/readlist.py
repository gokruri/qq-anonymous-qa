#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
匿问我答 —— 只读读取器（OidbSvc.0xec4_1 getAnswerList）
不提交任何回答。仅拉取 + 打印。

已确认字段（本会话实测）：
  OIDB 信封  请求: f1=cmd(3780=0xec4) f2=sub(1) f4=bytes(body)
             响应: f1=cmd f2=sub f3=ErrorCode f4=bytes(body) f5=str(ErrorInfo)
  业务请求体 : f1=uint64 uin   f2=uint32 fetch_type(>=1 才返回列表)
  业务响应体 : f1=rpt msg_quest   f4=bytes cookie   f7=bytes 目标昵称
"""
import os
import sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe import *   # oidb/f_varint/decode/show/uvarint/tag

def is_utf8(b):
    try:
        s = b.decode("utf-8")
    except Exception:
        return None
    if not s:
        return None
    if any(ord(c) < 9 or (14 <= ord(c) < 32) for c in s):
        return None
    return s

def dump(buf, indent=0, maxdepth=6, name=""):
    pad = "  " * indent
    try:
        items = decode(buf)
    except Exception as e:
        print("%s<解析失败: %s> %s" % (pad, e, buf.hex()[:80]))
        return
    for f, w, v in items:
        if w == 2:
            s = is_utf8(v)
            if s is not None and len(s) <= 400:
                print("%s%s f%-3d str(%d) = %r" % (pad, name, f, len(v), s))
            elif indent >= maxdepth:
                print("%s%s f%-3d bytes(%d) = %s" % (pad, name, f, len(v), v.hex()[:120]))
            else:
                # 可能是嵌套消息：先试解，成功就递归
                print("%s%s f%-3d msg(%d) {" % (pad, name, f, len(v)))
                dump(v, indent + 1, maxdepth, name)
                print("%s}" % pad)
        else:
            print("%s%s f%-3d varint = %d" % (pad, name, f, v))

def get_list(uin, fetch_type=1, sub=1, cmd=CMD_EC4, verbose=True):
    body = f_varint(1, uin) + f_varint(2, fetch_type)
    d = oidb(body, cmd=cmd, sub=sub)
    if verbose:
        print("请求 body = %s   (uin=%d, fetch_type=%d)" % (body.hex(), uin, fetch_type))
        print("信封 ErrorCode = %s   ErrorInfo = %r" % (d.get("code"), d.get("info")))
        print("响应业务体 %d 字节 = %s" % (len(d.get("body", b"")), d.get("body", b"").hex()))
        print("-" * 78)
        if d.get("body"):
            dump(d["body"])
    return d

if __name__ == "__main__":
    uin = int(sys.argv[1]) if len(sys.argv) > 1 else UIN_BOT
    ft = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    print("=" * 78)
    print("匿问我答 只读拉取  uin=%d fetch_type=%d" % (uin, ft))
    print("=" * 78)
    get_list(uin, ft)
