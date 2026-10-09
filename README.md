# QQ 匿问我答 —— 协议实现与自动回答

QQ 资料卡「匿问我答」（`com.tencent.askanonymously`）的协议实现：**读取匿名提问列表**、**提交回答**，以及一份从零推导出来的**字段号实测表**。

> ⚠️ **免责声明（先读这个）**
>
> - 本项目为**逆向工程**产物，基于 QQ 内部 OIDB 接口，**无任何官方文档支持**
> - **仅供学习研究**。请遵守腾讯 QQ 用户协议，**使用风险自负**
> - **请勿**用于骚扰、刷屏、批量操作或任何滥用行为
> - 接口**随时可能失效或被风控**，作者不保证可用性
> - 本项目**不包含**任何腾讯的代码或素材
> - 本项目**不是**法律服务；相关合规问题请自行判断或咨询律师

---

## 这是什么

QQ 的「匿问我答」是资料卡里的一个内嵌小程序：别人可以匿名向你提问，你回答后展示在资料卡上。

它**没有开放接口**，也没有任何 OneBot 实现支持它。本项目把它逆了出来，做成两个可独立运行的工具 + 一份字段号文档。

| 文件 | 作用 |
|---|---|
| `python/anon_read.py` | **只读**：拉取提问列表（含已答/未答） |
| `python/anon_answer.py` | 提交一条回答 |
| `docs/字段号-实测.md` | 字段号全表 + 推导方法 + 踩过的坑 |
| `node/anon-qa.js` | 一个完整参考实现（轮询 → 生成 → 提交，含幂等与限频） |
| `research/` | 探测脚本 —— 字段号是怎么试出来的（可复现） |

---

## 原理

### 通道

QQ 小程序通过 JSAPI 桥调 `window.mqq.data.ssoRequest({cmd:"OidbSvc.<…>", data:<JSON>})`，
底层是 **QQ 的 SSO / OIDB 通道**。

在 OneBot 这层，对应 **`send_packet`** 动作（⚠️ 官方文档里**没有**它，是实测探出来的）：

```http
POST http://127.0.0.1:3000/send_packet
{ "cmd": "OidbSvc.0xec4_1", "data": "<hex>" }
```

**⚠️ `data` 必须是「完整 OIDB 信封」的 protobuf hex，不能是 JSON。** 信封结构：

```
请求  f1=cmd(varint)  f2=sub(varint)  f4=bytes(业务体 protobuf)
响应  f1=cmd  f2=sub  f3=ErrorCode  f4=bytes(业务体)  f5=str(ErrorInfo)
```

### 用到的命令

| 命令 | 作用 | 业务请求体 |
|---|---|---|
| `OidbSvc.0xec4_1` | 拉提问列表 | `f1=uint64 uin` `f2=uint64 条数(1..10)` `f4=bytes cookie` |
| `OidbSvc.0xec7_1` | **提交回答** | `f1=str quest_id` `f2=str 回答正文` `f3=uint64 提问时间` |

> ⚠️ 提交回答**不是** `0xec8_1 addComment`（那只是给回答加评论）。这个坑一开始就踩了 ——
> 详见 `docs/字段号-实测.md`。

### 两个反直觉的点

1. **字段号靠"行为"试，不能靠 wire type 猜。** 字段号不存在时服务端**静默忽略**（不报错），
   所以探测 wire type 完全无效。可行的方法是：把业务体写成非法 protobuf → 服务端回 `ErrorCode=100001`，
   再逐步调整。

2. **写页 JS 的键序 == protobuf 字段号序。** 这是最终找到的规律，在 `0xec4` 和 `0xecf` 上各验证过一次。
   也就是说：拿到小程序的 JS，把对象字面量里键的**书写顺序**抄下来，就是字段号顺序。

---

## 用法

### 前置条件

需要一个提供 **`send_packet`** 的 OneBot 协议端。本项目在 **SnowLuma** 上实测通过。

> ⚠️ **NapCat 也有 `SendPacket`**，但它的参数格式（尤其 `data` 的编码方式）**可能与本项目不同**，
> 直接套用前请先确认。

### 只读：拉提问列表

```bash
python3 python/anon_read.py <QQ号> [每页条数=10] [最大页数=10]
```

输出示例：

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

**⚠️ 这是写操作**，会往目标账号的资料卡上写字。建议先用 `anon_read.py` 拿到一条真实 `quest_id`，
再手动提交一条测试。**本项目默认一切都是手动的，不会自动跑。**

### 自动化参考

`node/anon-qa.js` 是一个完整的自动闭环实现（轮询 → 调模型生成 → 提交），
可以直接读它的设计。它包含三个值得抄的东西：

- **默认 dryRun**：只拉列表 + 打日志，**一条都不提交**（连模型都不调 → 零 token）
- **幂等**：用 `quest_id` 记账，答过的永不重答 —— **没有这个就会每轮重答，疯狂烧 token**
- **限频**：每轮最多 1 条、每日上限 —— 防止长期积压的旧提问一次性炸出几十次调用

> 它依赖一个具体机器人的模块（`config` / `logger` / 模型调用），
> **不能直接跑**，请当作设计参考。

---

## 字段号

完整表格、推导过程、错误码表、遗留疑点都在 **[`docs/字段号-实测.md`](docs/字段号-实测.md)**。

---

## License

[MIT](LICENSE)

## 致谢

- 思路参考了 [NapCatQQ](https://github.com/NapNeko/NapCatQQ)、[Lagrange.Core](https://github.com/LagrangeDev/Lagrange.Core)
  等成熟的 QQ 协议实现 —— 它们证明了这条路是可行的
- 字段号推导用到的「键序 == 字段号序」规律，是在实测 `0xec4` / `0xecf` 的过程中试出来的
