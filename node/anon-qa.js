// 匿问我答 自动回答（QQ 资料卡「匿问我答」）
//
// 数据流：0xec4_1 getAnswerList 取未答提问 → 模型生成回答 → 0xec7_1 doAnswer 提交。
//
// ─────────────────────────────────────────────────────────────────────────
// ⚠️ 本文件**默认 dryRun=true**：只拉列表 + 打日志，**一条都不提交**。
//    主人要真回答，把 data/config.json 里 anonQa.dryRun 改成 false 再重启。
// ─────────────────────────────────────────────────────────────────────────
//
// 四个踩过的坑（别改回去）：
//   1. **启动包写错了命令**：提交回答不是 `0xec8_1 addComment`（那只是加评论，
//      成功后 JS 做的是 comment_num+1），是 **`0xec7_1` doAnswer**。命令来源是
//      write 页 bundle（`/v2/anonymous/write`），不是 answer 页。
//   2. **send_packet 的 data 是「完整 OIDB 信封」的 hex，不是业务体**：
//      信封 = f1=cmd(varint) f2=sub(varint) f4=bytes(业务体)。业务体要自己编码塞进 f4。
//      启动包给的样例其实只是「查提问总数」，少了一半信封。
//   3. **业务体字段号得靠行为试**：字段号不存在时服务端**静默忽略**（不报错），
//      所以 wire type 探测完全无效；但业务体写成非法 protobuf 会返回 ErrorCode=100001。
//      真正的规律是：**写页 JS 的键序 == protobuf 字段号序**（0xec4 与 0xecf 各实测验证一次）。
//   4. **列表每页最多 10 条**（f2>10 服务端直接返回空响应，不是截断），
//      翻页靠把上一页响应 f4 的 cookie 原样回灌到请求 f4。
//
// 字段号实测（2026-10-09，见 工作区/匿名问答-字段号-实测.md）：
//   OIDB 信封  请求 f1=cmd f2=sub f4=bytes(body)
//              响应 f1=cmd f2=sub f3=ErrorCode f4=bytes(body) f5=str(ErrorInfo)
//   0xec4 请求体 f1=uint64 uin  f2=uint64 quest_num(1..10)  f4=bytes cookie
//   0xec4 响应体 f1=rpt msg_quest  f3=总数  f4=bytes cookie  f5=ErrorCode  f7=昵称
//     单条:     f1=str_id  f2=str_quest  f4=uint64_time  f5=str_ans  f6=回答时间
//   0xec7 请求体 f1=str_quest_id  f2=str_ans  f3=uint64_quest_time
//
// 幂等：state.handled[quest_id] 记「已答/已尝试」。**提交失败绝不重试**（宁可漏别重烧 token）。
import fs from 'node:fs';
import path from 'node:path';
import { DATA_DIR, getConfig } from './config.js';
import { logger } from './logger.js';

const defaultLog = (msg) => {
  try {
    if (typeof logger?.info === 'function') logger.info(msg);
    else if (typeof logger === 'function') logger(msg);
    else console.log(msg);
  } catch { /* 日志失败不影响主流程 */ }
};

const STATE_FILE = path.join(DATA_DIR, 'anon-qa-state.json');
const CMD_GET_LIST = 0xec4;
const CMD_DO_ANSWER = 0xec7;
const SUB = 1;
const MAX_PAGE_SIZE = 10;     // 实测上限：>10 服务端返回空响应

// ─────────────────────────── protobuf 最小手写编解码 ───────────────────────
function uvarint(n) {
  const out = [];
  let v = n;
  for (;;) {
    const b = v & 0x7f;
    v = Math.floor(v / 128);
    if (v) out.push(b | 0x80);
    else { out.push(b); return Buffer.from(out); }
  }
}

const fVarint = (field, val) => Buffer.concat([uvarint((field << 3) | 0), uvarint(val)]);

function fBytes(field, data) {
  const b = Buffer.isBuffer(data) ? data : Buffer.from(String(data), 'utf8');
  return Buffer.concat([uvarint((field << 3) | 2), uvarint(b.length), b]);
}

function readVarint(buf, i) {
  let v = 0; let s = 1; let j = i;
  for (;;) {
    const b = buf[j]; j += 1;
    v += (b & 0x7f) * s;
    if (!(b & 0x80)) return [v, j];
    s *= 128;
  }
}

/** 宽容解码成 { fieldNumber: [ {w, v} ] }（同一字段可重复出现）。 */
function decodeProto(buf) {
  const out = new Map();
  let i = 0;
  while (i < buf.length) {
    let k; [k, i] = readVarint(buf, i);
    const f = Math.floor(k / 8); const w = k & 7;
    let v;
    if (w === 0) { [v, i] = readVarint(buf, i); }
    else if (w === 2) {
      let n; [n, i] = readVarint(buf, i);
      v = buf.subarray(i, i + n); i += n;
    } else if (w === 5) { v = buf.subarray(i, i + 4); i += 4; }
    else if (w === 1) { v = buf.subarray(i, i + 8); i += 8; }
    else throw new Error('protobuf: 非法 wire type ' + w + ' (field ' + f + ')');
    if (!out.has(f)) out.set(f, []);
    out.get(f).push({ w, v });
  }
  return out;
}

const firstVarint = (m, f) => {
  const a = m.get(f);
  return a && a[0] && a[0].w === 0 ? a[0].v : undefined;
};
const firstBytes = (m, f) => {
  const a = m.get(f);
  return a && a[0] && a[0].w === 2 ? a[0].v : undefined;
};
const allBytes = (m, f) => (m.get(f) || []).filter((x) => x.w === 2).map((x) => x.v);

// ─────────────────────────── 协议侧：send_packet ───────────────────────────
function snowlumaHttp() {
  const sl = getConfig().snowluma || {};
  return {
    base: String(sl.httpUrl || 'http://127.0.0.1:3000').replace(/\/+$/, ''),
    token: String(sl.httpAccessToken || sl.accessToken || '')
  };
}

/**
 * 发一个 OIDB 包。`body` 是**业务体**（Buffer），信封在这里拼。
 * 返回 { code, info, body }：code 是 OIDB 信封的 ErrorCode（0 = 正常）。
 */
async function sendPacket(cmd, body, log) {
  const { base, token } = snowlumaHttp();
  const env = Buffer.concat([fVarint(1, cmd), fVarint(2, SUB), fBytes(4, body)]);
  const res = await fetch(`${base}/send_packet`, {
    method: 'POST',
    headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd: `OidbSvc.0x${cmd.toString(16)}_${SUB}`, data: env.toString('hex') })
  });
  const j = await res.json().catch(() => ({}));
  const hex = j?.data;
  if (!hex) throw new Error('send_packet 无数据：' + JSON.stringify(j).slice(0, 160));
  const envd = decodeProto(Buffer.from(hex, 'hex'));
  return {
    code: firstVarint(envd, 3),
    info: firstBytes(envd, 5)?.toString('utf8') ?? '',
    body: firstBytes(envd, 4) ?? Buffer.alloc(0)
  };
}

/** 取机器人自己的 QQ 号（列表只能查自己那张卡）。 */
async function fetchSelfId(log) {
  const { base, token } = snowlumaHttp();
  try {
    const res = await fetch(`${base}/get_login_info`, {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: '{}'
    });
    const j = await res.json().catch(() => ({}));
    const id = String(j?.data?.user_id ?? j?.data?.uin ?? '');
    return id && id !== '0' ? id : '';
  } catch (error) {
    log('[匿问我答] 取登录信息失败：' + String(error?.message ?? error));
    return '';
  }
}

/**
 * 拉取提问列表（带 cookie 翻页）。
 * 返回 { quests, total, errorCode }。整个过程中**只有读**。
 */
async function fetchQuests(uin, log, pageSize = MAX_PAGE_SIZE, maxPages = 5) {
  const quests = [];
  const seen = new Set();
  let cookie = null;
  let total = null;
  let errorCode = null;
  for (let pg = 0; pg < maxPages; pg += 1) {
    let body = Buffer.concat([fVarint(1, Number(uin)), fVarint(2, pageSize)]);
    if (cookie) body = Buffer.concat([body, fBytes(4, cookie)]);
    const r = await sendPacket(CMD_GET_LIST, body, log);
    if (r.code !== 0) throw new Error(`getAnswerList OIDB ErrorCode=${r.code} ${r.info}`);
    const m = decodeProto(r.body);
    errorCode = firstVarint(m, 5);
    if (errorCode) break;                       // 10001 = 该 uin 不存在，10002 = 参数错
    if (total === null) total = firstVarint(m, 3);
    for (const qb of allBytes(m, 1)) {
      const qm = decodeProto(qb);
      const id = firstBytes(qm, 1)?.toString('utf8') ?? '';
      if (!id || seen.has(id)) continue;
      seen.add(id);
      quests.push({
        questId: id,
        question: firstBytes(qm, 2)?.toString('utf8') ?? '',
        askTime: firstVarint(qm, 4) ?? 0,
        answer: firstBytes(qm, 5)?.toString('utf8') ?? '',
        answerTime: firstVarint(qm, 6) ?? 0
      });
    }
    cookie = firstBytes(m, 4) ?? null;
    if (!cookie) break;
    if (cookie.toString('utf8').includes('hasFinish=1')) break;
    await new Promise((r2) => setTimeout(r2, 600));   // 页间停顿，别撞限频
  }
  return { quests, total, errorCode };
}

/**
 * 提交一条回答（**写操作**）。字段号：f1=str_quest_id f2=str_ans f3=uint64_quest_time。
 * 返回 { code, info }；code=0 表示成功。
 */
async function submitAnswer(quest, text, log) {
  const body = Buffer.concat([
    fBytes(1, quest.questId),
    fBytes(2, text),
    fVarint(3, quest.askTime)
  ]);
  const r = await sendPacket(CMD_DO_ANSWER, body, log);
  return { code: r.code, info: r.info };
}

// ─────────────────────────── 状态（幂等）───────────────────────────────────
function loadState() {
  try {
    const raw = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    return {
      handled: raw.handled || {},          // quest_id → {at, kind}  已答 或 已尝试(失败不重试)
      counters: raw.counters || {},        // 'YYYY-MM-DD' → {total}
      lastScanAt: raw.lastScanAt || 0,
      selfId: raw.selfId || ''
    };
  } catch {
    return { handled: {}, counters: {}, lastScanAt: 0, selfId: '' };
  }
}

function saveState(state) {
  try {
    const ids = Object.keys(state.handled);
    if (ids.length > 2000) {
      ids.sort((a, b) => (state.handled[a]?.at || 0) - (state.handled[b]?.at || 0));
      for (const id of ids.slice(0, ids.length - 2000)) delete state.handled[id];
    }
    const days = Object.keys(state.counters).sort();
    for (const d of days.slice(0, Math.max(0, days.length - 14))) delete state.counters[d];
    fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 1), 'utf8');
  } catch (error) {
    defaultLog('[匿问我答] 状态写盘失败：' + String(error?.message ?? error));
  }
}

function todayKey(now = Date.now()) {
  const d = new Date(now);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// ─────────────────────────── 配置（内置默认；主人也可写进 config.json）──────
function eff() {
  const c = getConfig().anonQa || {};
  return {
    enabled: c.enabled !== false,                 // 默认开
    dryRun: c.dryRun !== false,                   // ⚠️ 默认 true = 只拉列表、不提交
    intervalMinutes: Number(c.intervalMinutes) > 0 ? Number(c.intervalMinutes) : 10,
    activeIntervalMinutes: Number(c.activeIntervalMinutes) > 0 ? Number(c.activeIntervalMinutes) : 2,
    activeHoldMinutes: Number(c.activeHoldMinutes) > 0 ? Number(c.activeHoldMinutes) : 5,
    pageSize: Math.min(MAX_PAGE_SIZE, Math.max(1, Number(c.pageSize) || MAX_PAGE_SIZE)),
    maxAnswersPerScan: Number(c.maxAnswersPerScan) > 0 ? Number(c.maxAnswersPerScan) : 3,
    maxAnswersPerDay: Number(c.maxAnswersPerDay) || 0,      // 0 = 不限
    maxAnswerChars: Number(c.maxAnswerChars) > 0 ? Number(c.maxAnswerChars) : 150,
    // 积压保护：只自动回答「最近 N 天内」提的问题。默认 3 天。
    // 为什么需要：资料卡上可能躺着几十条陈年未答提问（实测测试号 29 条里 14 条是
    // 2024 年的），一旦 dryRun 关掉、又没有这道闸，会一次性炸出几十次模型调用。
    // 设 0 = 不限（连积压一起答）。
    // 注意：这里**只过滤、不写状态**，所以哪天想补答积压，把值改 0 即可，不会永久丢弃。
    backlogDays: c.backlogDays === undefined ? 3 : Number(c.backlogDays) || 0,
    silentEnabled: c.silentEnabled === true,
    silentRanges: String(c.silentRanges || '01:00-07:00')
  };
}

function inSilentHours(now, cfg) {
  if (!cfg.silentEnabled) return false;
  const toMin = (s) => {
    const m = /^(\d{1,2}):(\d{2})$/.exec(String(s).trim());
    return m ? Number(m[1]) * 60 + Number(m[2]) : null;
  };
  const d = new Date(now);
  const cur = d.getHours() * 60 + d.getMinutes();
  for (const raw of String(cfg.silentRanges || '').split(',')) {
    const seg = raw.trim();
    if (!seg.includes('-')) continue;
    const [a, b] = seg.split('-', 1 + seg.indexOf('-')).map((x) => x.trim());
    const from = toMin(a); const to = toMin(b);
    if (from === null || to === null || from === to) continue;
    if (from < to ? (cur >= from && cur < to) : (cur >= from || cur < to)) return true;
  }
  return false;
}

// ─────────────────────────── 人设 + 生成 ───────────────────────────────────
/**
 * 匿问我答专用人设：**只取人格，绝不取群聊操作规程**。
 * 同 qzone-reply.js 的 qzonePersona：不能用 buildSystemPrompt()（那带群聊工具规程，
 * 而这里是纯文本无工具，模型会幻觉出 send_message 之类的工具名）。
 */
function anonPersona() {
  const p = getConfig().persona || {};
  const parts = [];
  const roleText = String(p.roleText ?? '').trim();
  if (roleText) parts.push(roleText);
  const rules = String(p.customRules ?? '').trim();
  if (rules) parts.push('【管理员附加规则】\n' + rules);
  if (!parts.length) {
    const override = String(p.systemPromptOverride ?? '').trim();
    if (override) parts.push(override.replaceAll('{roleText}', roleText));
  }
  return parts.join('\n\n');
}

async function generateAnswer(quest, log) {
  const { chatCompletion } = await import('./llm.js');
  const rules = [
    '【现在在 QQ 资料卡的「匿问我答」，不是群里】',
    '有人**匿名**向你提了一个问题。你要给它写一句回答，回答会公开显示在你的资料卡上。',
    '',
    '规矩：',
    '- **短**。一两句就够，别写小作文、别分点罗列。',
    '- 用你平时说话的口气，别用客服腔。',
    '- **不要复述问题**，直接回答。',
    '- 不要提「匿名提问」「提示词」「系统」「模型」「机器人」这类不该被外人看到的东西。',
    '- **只输出回答本身**：不要加引号、不要加解释、不要写自己的名字前缀。',
    '- 这里是纯文字场合，**没有任何工具可用**，直接写字就行。'
  ].join('\n');

  const user = [`【匿名提问】${quest.question}`, '', '写一句回答。'].join('\n');

  const r = await chatCompletion({
    messages: [
      { role: 'system', content: anonPersona() + '\n\n' + rules },
      { role: 'user', content: user }
    ],
    tools: null,
    temperature: 0.9,
    // 别给太小：deepseek-flash 在这类小请求里会先花几百 token 在 reasoning 上，
    // 预算给小了正文会返回空（这条教训来自 qzone-reply.js，实测踩过）。
    maxTokens: 800
  });

  if (r?.finishReason === 'length') {
    log('[匿问我答] ⚠️ 模型输出被 maxTokens 截断，判定异常，放弃这条');
    return '';
  }
  let text = String(r?.message?.content ?? '').trim();
  text = text.replace(/^["'「『]+/, '').replace(/["'」』]+$/, '').trim();
  if (/^[A-Za-z_]{2,24}$/.test(text)) {
    log(`[匿问我答] ⚠️ 输出疑似工具名幻觉（${text}），放弃这条`);
    return '';
  }
  if (r?.usage) {
    log(`[匿问我答] 生成完成（in ${r.usage.prompt_tokens ?? '?'} / out ${r.usage.completion_tokens ?? '?'} tok）`);
  }
  return text.slice(0, eff().maxAnswerChars);
}

// ─────────────────────────── 核心：一轮扫描 ────────────────────────────────
async function scanOnce(log) {
  const cfg = eff();
  const state = loadState();
  const now = Date.now();

  if (!cfg.enabled) return { skipped: 'disabled' };
  if (inSilentHours(now, cfg)) return { skipped: 'silent' };

  if (!state.selfId) {
    const id = await fetchSelfId(log);
    if (id) { state.selfId = id; saveState(state); }
  }
  if (!state.selfId) {
    log('[匿问我答] 拿不到自己的 QQ 号，跳过本轮');
    return { skipped: 'no-self-id' };
  }

  const { quests, total, errorCode } = await fetchQuests(state.selfId, log, cfg.pageSize);
  if (errorCode) {
    log(`[匿问我答] 列表业务 ErrorCode=${errorCode}（10001=该 uin 不存在 / 10002=参数错）`);
    state.lastScanAt = now; saveState(state);
    return { scanned: true, total: 0, pending: 0 };
  }

  // 待答：没回答过、且状态里没记录过（记录过 = 已答或已尝试失败 → 永不重试）
  const candidate = quests.filter((q) => !q.answer && !state.handled[q.questId]);

  // 积压闸：只答「最近 backlogDays 天内」的新提问（见 eff() 里的说明）。
  // 只过滤不写状态 —— 想补答积压把 backlogDays 改成 0 即可。
  const cutoff = cfg.backlogDays > 0 ? now - cfg.backlogDays * 24 * 3600 * 1000 : 0;
  const pending = candidate.filter((q) => !cutoff || q.askTime * 1000 >= cutoff);
  const backlog = candidate.length - pending.length;

  // 已在资料卡上有回答的，直接记进状态，省得以后重复判断
  for (const q of quests) {
    if (q.answer && !state.handled[q.questId]) state.handled[q.questId] = { at: now, kind: 'already-answered' };
  }

  if (backlog > 0) {
    log(`[匿问我答] 跳过 ${backlog} 条积压提问（早于 ${cfg.backlogDays} 天前；`
      + '要一起答就把 anonQa.backlogDays 设成 0）');
  }

  if (!pending.length) {
    log(`[匿问我答] 本轮无事（共 ${total ?? quests.length} 条提问，全部已答）`);
    state.lastScanAt = now; saveState(state);
    return { scanned: true, total: quests.length, pending: 0 };
  }

  log(`[匿问我答] ⚠️ 发现 ${pending.length} 条未答提问${cfg.dryRun ? '（**只读模式**，不提交）' : ''}：`);
  for (const q of pending) {
    log(`[匿问我答]   · 「${q.question}」 (${new Date(q.askTime * 1000).toLocaleString('zh-CN')})`);
  }

  const day = todayKey(now);
  const c = state.counters[day] || (state.counters[day] = { total: 0 });
  let done = 0;

  for (const q of pending) {
    if (done >= cfg.maxAnswersPerScan) {
      log(`[匿问我答] ⏸ 本轮已达 maxAnswersPerScan=${cfg.maxAnswersPerScan}，剩下的下轮再说`);
      break;
    }
    if (cfg.maxAnswersPerDay && c.total >= cfg.maxAnswersPerDay) {
      log(`[匿问我答] ⏸ 今日已达 maxAnswersPerDay=${cfg.maxAnswersPerDay}`);
      break;
    }

    if (cfg.dryRun) {
      // 只读模式**连模型都不调** —— 零 token，主人都能先看日志确认触发判断准不准
      log(`[匿问我答]   → [只读模式] 本该回答：「${q.question}」`);
      continue;
    }

    let text = '';
    try {
      text = await generateAnswer(q, log);
    } catch (error) {
      log('[匿问我答] 生成失败：' + String(error?.message ?? error));
      continue;
    }
    if (!text) { log('[匿问我答] 模型没给出内容，本条跳过（不记状态，下轮可重试）'); continue; }

    let r;
    try {
      r = await submitAnswer(q, text, log);
    } catch (error) {
      // ⚠️ 提交异常也当成"已尝试"记下来：**宁可漏，别重复烧 token**（主人定的规矩）
      state.handled[q.questId] = { at: Date.now(), kind: 'submit-error', err: String(error?.message ?? error).slice(0, 120) };
      saveState(state);
      log('[匿问我答] ❌ 提交异常（不再重试）：' + String(error?.message ?? error));
      continue;
    }

    if (r.code !== 0) {
      state.handled[q.questId] = { at: Date.now(), kind: 'submit-fail', code: r.code };
      saveState(state);
      log(`[匿问我答] ❌ 提交失败 ErrorCode=${r.code} ${r.info}（不再重试）`);
      continue;
    }

    state.handled[q.questId] = { at: Date.now(), kind: 'answered' };
    c.total += 1;
    done += 1;
    saveState(state);
    log(`[匿问我答] ✅ 已回答：「${q.question}」→「${text}」`);
  }

  state.lastScanAt = now;
  saveState(state);
  return { scanned: true, total: quests.length, pending: pending.length, answered: done };
}

// ─────────────────────────── 对外入口：app.js 里 startAnonQa(log) ──────────
export function startAnonQa(log = defaultLog) {
  const cfg = eff();
  if (!cfg.enabled) {
    log('[匿问我答] 已禁用（anonQa.enabled=false）');
    return null;
  }

  let running = false;
  let backoffUntil = 0;
  let activeUntil = 0;
  let stopped = false;
  let timer = null;

  const schedule = (ms) => {
    if (stopped) return;
    timer = setTimeout(tick, Math.max(1000, ms));
    timer.unref?.();
  };

  const tick = async () => {
    if (stopped) return;
    if (running) { schedule(30 * 1000); return; }
    if (Date.now() < backoffUntil) { schedule(backoffUntil - Date.now()); return; }

    running = true;
    let pending = 0;
    try {
      const r = await scanOnce(log);
      pending = Number(r?.pending) || 0;
    } catch (error) {
      backoffUntil = Date.now() + 5 * 60 * 1000;
      log('[匿问我答] 扫描异常（' + String(error?.message ?? error) + '），5 分钟后再试');
    } finally {
      running = false;
    }

    // 自适应节奏：同 qzone-reply —— 空闲慢慢拉，有未答提问就切成密集盯梢
    if (pending > 0) {
      activeUntil = Date.now() + cfg.activeHoldMinutes * 60 * 1000;
      log(`[匿问我答] 有未答提问 → 切到 ${cfg.activeIntervalMinutes} 分钟密切盯梢 ${cfg.activeHoldMinutes} 分钟`);
    }
    const active = Date.now() < activeUntil;
    schedule((active ? cfg.activeIntervalMinutes : cfg.intervalMinutes) * 60 * 1000);
  };

  log(`[匿问我答] 已启动：空闲 ${cfg.intervalMinutes} 分钟 / 有未答时 ${cfg.activeIntervalMinutes} 分钟`
    + `（${cfg.dryRun ? '只读模式，不提交任何回答' : '可自动回答'}；`
    + `每轮最多 ${cfg.maxAnswersPerScan} 条，每日${cfg.maxAnswersPerDay ? cfg.maxAnswersPerDay + ' 条' : '不限'}）`);
  schedule(40 * 1000);   // 启动 40 秒后先跑一轮，方便重启后立刻看到日志

  return {
    stop() { stopped = true; if (timer) clearTimeout(timer); },
    unref() { timer?.unref?.(); }
  };
}

export { scanOnce, eff, inSilentHours, fetchQuests, generateAnswer, submitAnswer };
