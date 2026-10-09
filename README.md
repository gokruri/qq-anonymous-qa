# QQ 匿问我答 · 协议实现

> 💬 把 QQ 资料卡里的「匿问我答」逆了出来 —— **读提问、写回答**，附一份从零推出来的**字段号实测表**。
> 无需 Root，不需要抓包，走 OneBot 的 `send_packet`。

<p align="center">
  <img src="https://img.shields.io/badge/python-3.8%2B-blue" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="license">
  <img src="https://img.shields.io/badge/status-实测可用-success" alt="status">
</p>

---

## ⚠️ 免责声明（务必先读）

- 本项目是**逆向工程**产物，基于 QQ **内部** OIDB 接口，**没有任何官方文档**
- **仅供学习研究**。请遵守腾讯 QQ 用户协议，**使用风险自负**
- **禁止**用于骚扰、刷屏、批量操作或任何滥用行为
- 接口**随时可能失效或被风控**，作者**不保证可用性**
- 本项目**不包含**任何腾讯的代码或素材
- 这不是法律服务，合规问题请自行判断

---

## ✨ 它能干什么

QQ 的「匿问我答」是资料卡里的一个内嵌小程序：别人匿名向你提问，你回答后展示在资料卡上。

**它没有开放接口，也没有任何 OneBot 实现支持它。** 这个项目把它逆了出来：

| 文件 | 作用 |
|---|---|
| `python/anon_read.py` | **只读** —— 拉取提问列表（含已答 / 未答 / 翻页） |
| `python/anon_answer.py` | 提交一条回答 |
| `docs/字段号-实测.md` | **字段号全表** + 推导方法 + 踩过的坑 |
| `node/anon-qa.js` | 完整参考实现：轮询 → 生成 → 提交（含幂等 + 限频） |
| `research/` | 探测脚本 —— 字段号**是怎么试出来的**，可复现 |

---

## 🔧 原理

### 通道

QQ 小程序是通过 JSAPI 桥调的：

```js
window.mqq.data.ssoRequest({ cmd: "OidbSvc.<…>", data: <JSON> })
```

底层就是 **QQ 的 SSO / OIDB 通道**。在 OneBot 这一层，对应 **`send_packet`**：

> ⚠️ `send_packet` **官方文档里没有**，是实测探出来的。

```http
POST http://127.0.0.1:3000/send_packet
{ "cmd": "OidbSvc.0xec4_1", "data": "<hex>" }
```

**`data` 必须是「完整 OIDB 信封」的 protobuf hex，不能传 JSON。**

```
请求  f1=cmd(varint)  f2=sub(varint)  f4=bytes(业务体 protobuf)
响应  f1=cmd  f2=sub  f3=ErrorCode  f4=bytes(业务体)  f5=str(ErrorInfo)
```

### 用到的命令

| 命令 | 作用 | 业务请求体 |
|---|---|---|
| `OidbSvc.0xec4_1` | 拉提问列表 | `f1=uin` `f2=条数(1..10)` `f4=cookie` |
| `OidbSvc.0xec7_1` | **提交回答** | `f1=quest_id` `f2=回答正文` `f3=提问时间` |

> ⚠️ 提交回答**不是** `0xec8_1`（那只是给回答加评论）。这个坑一开始就踩了，详见文档。

### 两个反直觉的点 🤔

**1. 字段号不能靠 wire type 猜。**
字段号不存在时，服务端**静默忽略**、不报错 —— 所以探测 wire type 完全无效。
可行办法：把业务体写成非法 protobuf → 服务端回 `ErrorCode=100001`，再逐步收敛。

**2. 写页 JS 的键序 == protobuf 字段号序。** 👈 这是最终找到的规律
拿到小程序 JS，把对象字面量里键的**书写顺序**抄下来，就是字段号顺序。
在 `0xec4` 和 `0xecf` 上各验证过一次。

---

## 🚀 用法

### 前置条件

需要一个提供 **`send_packet`** 的 OneBot 协议端。本项目在 **[SnowLuma](https://github.com/motricseven7/snowluma)** 上实测通过。

> ⚠️ **NapCat 也有 `SendPacket`**，但参数格式（尤其 `data` 的编码方式）**可能与本项目不同**，
> 直接套用前请先确认。

### 只读：拉提问列表

```bash
python3 python/anon_read.py <QQ号> [每页条数=10] [最大页数=10]
```

输出：

```
匿问我答 只读列表  uin=10001  page_size=10
  第1页: 本页 1 条 / 新增 1 / 累计 1 / 总数 1 / hasFinish=True

共 1 条提问（已答 1 / 未答 0）
 1. 你是ai吗  [2026-10-06 09:10]
    答: 诶 被你发现了
    quest_id=U_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### 提交回答

```bash
python3 python/anon_answer.py --help
```

> ⚠️ **这是写操作**，会往目标账号的资料卡上写字。建议先用 `anon_read.py` 拿一条真实
> `quest_id`，手动提交一条测试。**本项目默认一切手动，不会自动跑。**

### 自动化参考

`node/anon-qa.js` 是一个完整的自动闭环（轮询 → 调模型 → 提交）。它有三个值得抄的设计：

- 🛡️ **默认 `dryRun`** —— 只拉列表 + 打日志，**一条都不提交**（连模型都不调 → 零 token）
- 🔁 **幂等** —— 用 `quest_id` 记账，答过的永不重答。**没有这个就会每轮重答，疯狂烧 token**
- ⏱️ **限频** —— 每轮最多 1 条、每日上限，防止积压的旧提问一次性炸出几十次调用

> 它依赖某个具体机器人的模块（config / logger / 模型调用），**不能直接跑**，当设计参考看。

---

## 📋 字段号

完整表格、推导过程、错误码表、遗留疑点 → **[`docs/字段号-实测.md`](docs/字段号-实测.md)**

---

## ❓ FAQ

**Q：会不会封号？**
A：不知道。这是内部接口，有一定风险。建议**小号先试**、别高频、别批量。

**Q：为什么 `data` 不能直接传 JSON？**
A：JSAPI 桥那层是原生做的 JSON→protobuf 转换，到了 `send_packet` 这层只有原始 SSO，
必须自己给 protobuf 二进制。

**Q：能自动回答吗？**
A：能，见 `node/anon-qa.js`。但**记得先开 `dryRun` 跑一轮**，确认幂等没问题再放开。

**Q：NapCat 能用吗？**
A：它也有 `SendPacket`，但参数格式可能不同，**未实测**，欢迎 PR 补充。

---

## 🙏 致谢

- 思路参考了 [NapCatQQ](https://github.com/NapNeko/NapCatQQ)、[Lagrange.Core](https://github.com/LagrangeDev/Lagrange.Core)
  等成熟的 QQ 协议实现 —— 它们证明这条路是可行的
- 「键序 == 字段号序」这个规律，是在实测 `0xec4` / `0xecf` 的过程中试出来的

## 📄 License

[MIT](LICENSE)

---

<details>
<summary><b>🇬🇧 English</b></summary>

### QQ Anonymous Q&A — Protocol Implementation

Reverse-engineered implementation of QQ's **Anonymous Q&A** (匿问我答) feature inside the profile card:
**read questions, submit answers**, plus a hand-derived **field-number table**.

> ⚠️ **Disclaimer** — This is a reverse-engineering project based on QQ's internal OIDB
> interfaces, with no official documentation. It is **for research and learning only**.
> Comply with Tencent's ToS and **use at your own risk**. Do **not** use it for harassment,
> spam, or any abusive behaviour. The interfaces may break or get rate-limited at any time.
> No Tencent code or assets are included. This is not legal advice.

#### How it works

QQ mini-programs talk to QQ through the JSAPI bridge:
`window.mqq.data.ssoRequest({cmd: "OidbSvc.<…>", data: <JSON>})`, which sits on top of QQ's
**SSO / OIDB channel**. On the OneBot side this maps to the **`send_packet`** action
(undocumented — discovered experimentally):

```http
POST http://127.0.0.1:3000/send_packet
{ "cmd": "OidbSvc.0xec4_1", "data": "<hex>" }
```

`data` must be the **protobuf hex of the full OIDB envelope**, not JSON:

```
request   f1=cmd(varint)  f2=sub(varint)  f4=bytes(body)
response  f1=cmd  f2=sub  f3=ErrorCode  f4=bytes(body)  f5=str(ErrorInfo)
```

| Command | Purpose | Body |
|---|---|---|
| `OidbSvc.0xec4_1` | list questions | `f1=uin` `f2=count(1..10)` `f4=cookie` |
| `OidbSvc.0xec7_1` | **submit answer** | `f1=quest_id` `f2=answer` `f3=ask_time` |

#### Two counter-intuitive findings

1. **Field numbers can't be probed via wire types.** Non-existent field numbers are
   *silently ignored* (no error), so wire-type probing is useless. Instead, send a malformed
   protobuf body — the server replies `ErrorCode=100001` — and converge from there.
2. **Key order in the write-page JS == protobuf field-number order.** Copy the key order of
   the object literal in the mini-program JS and you have the field order.
   Verified independently on `0xec4` and `0xecf`.

#### Usage

```bash
python3 python/anon_read.py <uin> [page_size=10] [max_pages=10]   # read-only
python3 python/anon_answer.py --help                              # write (be careful!)
```

Requires an OneBot implementation exposing `send_packet` (tested on SnowLuma).
`node/anon-qa.js` is a full reference implementation (polling → LLM → submit) with
`dryRun` on by default, idempotency, and rate limiting.

See [`docs/字段号-实测.md`](docs/字段号-实测.md) for the complete field table.

#### License

[MIT](LICENSE)

</details>
