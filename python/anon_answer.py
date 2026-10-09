#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
匿问我答 —— 提交回答（OidbSvc.0xec7_1 doAnswer）
⚠️ 这是**写**操作。默认 dry-run（只打印不发），必须显式 --commit 才真发。

命令来源：write 页 bundle 的 doAnswer
  doAnswer: { str_quest_id: r.qid, str_ans: t.str_ans, uint64_quest_time: r.qtime }
             → "OidbSvc.0xec7_1"

字段号（写页 JS 键序 = 字段号序，已由 0xec4 / 0xecf_1 两次实测确认）：
  请求业务体 f1=str_quest_id  f2=str_ans  f3=uint64_quest_time
  （与 0xec4 的 {uin,quest_num,comment_num,cookie,fetch_type} → f1..f5 同规律；
    0xecf_1 实测 uin@f1 quest_id@f4，也与键序 {uin,comment_num,quest_time,quest_id} 吻合）

⚠️ 启动包说的「0xec8 addComment 提交回答」是**错的**：0xec8 只是加评论，
   成功后 JS 做的是 comment_num+1。真正的回答是 0xec7_1。

用法：
  python3 anon-answer.py <uin> <quest_id> "<回答文本>" [--commit]
  python3 anon-answer.py <uin> --from "<提问正文片段>" "<回答文本>" [--commit]
"""
import os
import sys, json, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe import f_varint, f_bytes, decode
from anon_read import fetch_all

CMD, SUB = 0xEC7, 1


def send(body, cmd=CMD, sub=SUB):
    env = f_varint(1, cmd) + f_varint(2, sub) + f_bytes(4, body)
    req = urllib.request.Request(
        "http://127.0.0.1:3000/send_packet",
        data=json.dumps({"cmd": "OidbSvc.0x%x_%d" % (cmd, sub), "data": env.hex()}).encode(),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        resp = json.loads(r.read().decode())
    hx = resp.get("data")
    if not hx:
        raise RuntimeError("send_packet 失败: %s" % resp)
    e = dict((f, v) for f, w, v in decode(bytes.fromhex(hx)))
    return e.get(3), (e.get(5) or b"").decode("utf-8", "replace"), (e.get(4) or b"")


def main(argv):
    commit = "--commit" in argv
    argv = [a for a in argv if a != "--commit"]
    if len(argv) < 4:
        print(__doc__)
        return 1
    uin = int(argv[1])
    if argv[2] == "--from":
        need, ans = argv[3], argv[4]
        quests = fetch_all(uin, verbose=False)
        cand = [q for q in quests if need in q["question"] and not q["answer"]]
        if not cand:
            print("没找到匹配且未回答的提问: %r" % need)
            return 2
        q = cand[0]
    else:
        qid, ans = argv[2], argv[3]
        quests = fetch_all(uin, verbose=False)
        q = next((x for x in quests if x["quest_id"] == qid), None)
        if q is None:
            print("列表里没有这个 quest_id: %s" % qid)
            return 2

    print("=" * 78)
    print("目标提问 : %s" % q["question"])
    print("quest_id : %s" % q["quest_id"])
    print("提问时间 : %s" % q["ask_time"])
    print("当前回答 : %r" % q["answer"])
    print("拟提交   : %r  (%d 字)" % (ans, len(ans)))
    body = f_bytes(1, q["quest_id"]) + f_bytes(2, ans) + f_varint(3, q["ask_time"])
    print("请求体   : %s" % body.hex())
    print("命令     : OidbSvc.0x%x_%d (doAnswer)" % (CMD, SUB))
    print("=" * 78)
    if not commit:
        print("*** DRY-RUN：没有发送。要真发请加 --commit ***")
        return 0
    if q["answer"]:
        print("!! 该提问已有回答，跳过（不覆盖）")
        return 3
    code, info, rb = send(body)
    print("已发送 → 信封 ErrorCode=%s ErrorInfo=%r 响应体=%s" % (code, info, rb.hex()))
    if code:
        print("失败，未提交成功。")
        return 4
    time.sleep(1.5)
    after = fetch_all(uin, verbose=False)
    got = next((x for x in after if x["quest_id"] == q["quest_id"]), None)
    print("回读验证：回答 = %r" % (got["answer"] if got else None))
    print("✅ 提交成功并已回读确认" if got and got["answer"] == ans else "⚠️ 回读不一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
