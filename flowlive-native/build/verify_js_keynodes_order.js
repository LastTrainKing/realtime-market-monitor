// verify_js_keynodes_order.js —— 「全天关键节点榜」JS 侧排序验证
//
// ★ 为什么需要单独一个 JS 测试：这处修复有**四份实现**，语言不同：
//     Python ×3（skill scripts / flowtool / native_build core）—— 同一份代码的副本
//     JS     ×1（templates/single_file_app.html 的 keyNodes）—— 另一种语言
//   搜索 `timeline_summary` 永远找不到 JS 那份，最容易漏。
//   两边必须给出**相同的排列**，否则"看板里的榜"与"报告里的榜"顺序不同，
//   用户会以为是两个功能。
//
// ★ 关键手法：用**正则从模板里抽出生产函数原文**再 require，
//   不在这里另写一份 timeKey / rowByTime —— 否则验的是"我的副本和我的副本一致"，
//   模板改了这里不会跟着发现（这个坑在 Python 侧已踩过一次，见
//   verify_timeline_order.py 里 tk() 为什么复用生产 _time_key）。
//
// 用法：
//   node verify_js_keynodes_order.js [模板路径]
//   默认路径：~/.workbuddy/skills/astock-intraday-flow-salience/templates/single_file_app.html

const fs = require('fs');
const os = require('os');
const path = require('path');

const defaultTpl = path.join(
  os.homedir(), '.workbuddy', 'skills', 'astock-intraday-flow-salience',
  'templates', 'single_file_app.html');
const target = process.argv[2] || defaultTpl;

if (!fs.existsSync(target)) {
  console.error('[!] 找不到模板：' + target);
  process.exit(2);
}
const src = fs.readFileSync(target, 'utf8');

// ---- 抽出生产函数原文（不另写副本）----
function extract(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n\\}', 'm');
  const m = src.match(re);
  if (!m) throw new Error('未在模板中找到生产函数 ' + name);
  return m[0];
}

const code = [extract('timeKey'), extract('rowByTime'),
              'module.exports = { timeKey, rowByTime };'].join('\n');
const tmp = path.join(os.tmpdir(), '_verify_js_keynodes_' + process.pid + '.js');
fs.writeFileSync(tmp, code, 'utf8');
let timeKey, rowByTime;
try {
  ({ timeKey, rowByTime } = require(tmp));
} finally {
  try { fs.unlinkSync(tmp); } catch (e) { /* 清理失败不影响结论 */ }
}

let pass = 0, fail = 0;
const fails = [];
function ck(name, ok, detail) {
  console.log('[' + (ok ? 'PASS' : 'FAIL') + '] ' + name + (detail ? '  · ' + detail : ''));
  if (ok) pass++; else { fail++; fails.push(name); }
}

console.log('='.repeat(70));
console.log('全天关键节点榜 —— JS 侧排序验证（模板生产函数原文）');
console.log('模板：' + target);
console.log('='.repeat(70));

// ---------------------------------------------------------------- A 解析容错
console.log('\n--- A. timeKey 解析与容错 ---');
const cases = [
  ['09:31', 571, '开盘第一分钟'],
  ['11:30', 690, '上午收盘'],
  ['13:01', 781, '下午开盘（跨午休仍单调）'],
  ['15:00', 900, '全天收盘'],
  ['9:35', 575, '单位数小时（不补零）'],
  [' 10:07 ', 607, '带空格'],
  ['25:00', null, '非法小时'],
  ['10:75', null, '非法分钟'],
  ['abc', null, '非时间串'],
  ['', null, '空串'],
  [null, null, 'null'],
  [1234, null, '非字符串'],
];
for (const [raw, want, desc] of cases) {
  const got = timeKey(raw);
  ck('A·' + desc + '  ' + JSON.stringify(raw) + ' → ' + got, got === want, '期望 ' + want);
}
ck('A·字典序陷阱已避开（9:35 必须小于 10:00）',
   timeKey('9:35') < timeKey('10:00'),
   timeKey('9:35') + ' < ' + timeKey('10:00') + '（字符串比较会得出相反结论）');
ck('A·跨午休单调（11:30 < 13:01）', timeKey('11:30') < timeKey('13:01'));

// -------------------------------------------------------------- B 排序主逻辑
console.log('\n--- B. rowByTime：乱序输入 → 时间升序输出 ---');
// 刻意构造"旧写法（按强度倒序）会给出的顺序"作为输入
const scrambled = [
  { time: '09:42', idx: 5, strength: 83.0, name: 'a' },
  { time: '09:45', idx: 6, strength: 61.5, name: 'b' },
  { time: '10:15', idx: 20, strength: 89.6, name: 'c' },
  { time: '09:51', idx: 8, strength: 61.8, name: 'd' },
  { time: '09:48', idx: 7, strength: 32.0, name: 'e' },
  { time: '11:00', idx: 40, strength: 90.3, name: 'f' },
  { time: '13:30', idx: 80, strength: 55.0, name: 'g' },
  { time: '15:00', idx: 239, strength: 44.0, name: 'h' },
];
const sorted = scrambled.slice().sort(rowByTime);
const times = sorted.map(r => r.time);
const ks = times.map(timeKey);
ck('B1 输出按时间升序', ks.every((k, i) => i === 0 || ks[i - 1] <= k), times.join(' → '));
ck('B2 严格递增（无同刻重复、无回跳）', ks.every((k, i) => i === 0 || ks[i - 1] < k));
ck('B3 首条 = 全天最早、末条 = 全天最晚',
   ks[0] === Math.min.apply(null, ks) && ks[ks.length - 1] === Math.max.apply(null, ks),
   times[0] + ' ~ ' + times[times.length - 1]);
let back = 0;
for (let i = 1; i < ks.length; i++) if (ks[i - 1] > ks[i]) back++;
ck('B4 时间回跳 = 0', back === 0, '实测回跳 ' + back + ' 次');

// ------------------------------------------------------------------ C 边界
console.log('\n--- C. 边界 ---');
const withBad = [{ time: '10:00', idx: 0, strength: 1 },
                 { time: 'bad', idx: 1, strength: 99 },
                 { time: '10:03', idx: 2, strength: 1 }].sort(rowByTime);
ck('C1 不可解析时刻排到队尾（不当成 0 挤到最前）',
   withBad[withBad.length - 1].time === 'bad', withBad.map(r => r.time).join(' → '));

const dup = [{ time: '10:00', idx: 5, strength: 10 },
             { time: '10:00', idx: 3, strength: 20 },
             { time: '10:00', idx: 4, strength: 30 }].sort(rowByTime);
ck('C2 同刻按面板行序兜底（稳定）',
   dup.map(r => r.idx).join(',') === '3,4,5', dup.map(r => r.idx).join(' → '));

const sameIdx = [{ time: '10:00', idx: 7, strength: 10 },
                 { time: '10:00', idx: 7, strength: 30 }].sort(rowByTime);
ck('C3 同刻同行时按强度降序兜底', sameIdx[0].strength === 30,
   '强度 ' + sameIdx.map(r => r.strength).join(' → '));

// -------------------------------------------------------------- D 不变式
console.log('\n--- D. 幂等 / 不变式 ---');
const once = scrambled.slice().sort(rowByTime).map(r => r.time).join();
const twice = scrambled.slice().sort(rowByTime).sort(rowByTime).map(r => r.time).join();
ck('D1 连续排两次结果一致（幂等、无状态）', once === twice);
ck('D2 排序前后条数不变（不丢行）', sorted.length === scrambled.length,
   scrambled.length + ' → ' + sorted.length);
ck('D3 排序前后元素集合不变（不漏不增）',
   JSON.stringify(sorted.map(r => r.name).sort()) ===
   JSON.stringify(scrambled.map(r => r.name).sort()));

// ------------------------------------------------- E 与 Python 侧口径一致性
console.log('\n--- E. 与 Python 侧口径一致性（同构检查）---');
// Python 的 _key() 是 (0, tk, idx, -strength) / (1, 0, idx, -strength)，
// JS 的 rowByTime 必须给出完全相同的偏序关系才谈得上"两边一致"
const pyLike = [{ time: '11:30', idx: 100, strength: 9 },
                { time: '13:01', idx: 101, strength: 99 }].sort(rowByTime);
ck('E1 跨午休 11:30 仍排在 13:01 之前（与 Python 同构）',
   pyLike[0].time === '11:30', pyLike.map(r => r.time).join(' → '));

const strengthNoWin = [{ time: '09:40', idx: 9, strength: 1 },
                       { time: '09:37', idx: 6, strength: 999 }].sort(rowByTime);
ck('E2 强度高不能置前（时间优先，与 Python 修复同构）',
   strengthNoWin[0].time === '09:37', strengthNoWin.map(r => r.time).join(' → '));

// ------------------------------------------------- F 源码级守卫（防回退）
console.log('\n--- F. 源码守卫（防止旧写法回归）---');
// ★ 与 Python 侧同理：判据必须是**可执行代码**而不是注释/文档。
//   JS 没有 ast，这里用「函数体切片 + 去掉 // 行注释与 /* */ 块注释」近似，
//   并明确只检查 keyNodes 与 rowByTime 的函数体。
function stripComments(s) {
  return s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}
const keyNodesBody = stripComments(extract('keyNodes'));
ck('F1 keyNodes 最终一步是 rowByTime 排序（不是按强度）',
   /\.sort\(\s*rowByTime\s*\)/.test(keyNodesBody),
   '找到 .sort(rowByTime)');
ck('F2 旧写法 .sort((a,b)=>b.strength-a.strength) 只出现在"先选"那步',
   (keyNodesBody.match(/b\.strength\s*-\s*a\.strength/g) || []).length === 1,
   '出现 1 次（选取阶段），排列阶段已改用 rowByTime');
ck('F3 归档路径也重排（旧归档按强度存的）',
   /histRec\.nodes[\s\S]{0,80}\.sort\(\s*rowByTime\s*\)/.test(keyNodesBody),
   '历史回看读归档后重新按时间摆');

console.log('\n' + '='.repeat(70));
console.log('结果：' + pass + '/' + (pass + fail) + ' PASS');
if (fails.length) {
  console.log('未通过：');
  for (const f of fails) console.log('  · ' + f);
} else {
  console.log('全部通过 ✅');
}
console.log('='.repeat(70));
process.exit(fails.length ? 1 : 0);
