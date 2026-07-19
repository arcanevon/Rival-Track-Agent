// ══════════════════════════════════════════════════════════════════
// 常量
// ══════════════════════════════════════════════════════════════════
const WS_PORT = Number(window.RIVALTRACK_CONFIG?.wsPort || 8765);
const WS_URL = `ws://${location.hostname}:${WS_PORT}`;
const DEBOUNCE_MS = 300;
const MAX_ANALYSIS_COMPETITORS = 8;
const LS_KEY = 'rivaltrack-agent-outputs';
const HISTORY_LS_KEY = 'rivaltrack-history';
let confirmedScope = null;
let scopeDraft = null;
let trackConfirmed = false;
let activeAnalysisId = '';
const HISTORY_CUTOFF_TS = new Date('2026-05-24T11:20:00+08:00').getTime();
const THREAT_LABELS = { high: '高威胁', medium: '中威胁', low: '低威胁' };
const FIELD_LABELS = {
  user_substitution: '用户替代威胁', capability_catch_up: '能力追赶威胁',
  distribution: '分发渠道威胁', strategic_expansion: '战略扩张威胁', overall: '综合威胁',
  evidence_strength: '证据强度', source_tier: '来源层级', actual_source_type: '实际来源类型',
  quality_score: '综合质量分', score_delta: '质量分变化', matrix_completeness: '矩阵完整度',
  qa_confidence: '质检置信度', evidence_gap_count: '证据缺口数', disagreement_count: '分歧数',
  actionable_disagreement_count: '显著分歧数', structural_error_count: '结构错误数',
  relevance_precision_at_5: '前五条证据相关率', claim_answer_rate: '问题可回答率',
  bad_domain_leakage: '低质域名泄漏率', official_capability: '官方能力证据',
  pricing_or_packaging: '价格与套餐证据', community_pain: '社区痛点证据',
  third_party_benchmark: '第三方评测证据', distribution_signal: '渠道信号证据',
  github_release_velocity: '版本活跃度证据', strategic_expansion_signal: '战略扩张信号证据',
  competitor: '竞品', criterion: '分析准则', finding: '方法判断', evidence_refs: '证据引用',
  reasoning: '推导过程', uncertainty: '不确定性', mapped_dimensions: '影响维度',
  method_trace_coverage: '方法推导覆盖率',
};
const VALUE_LABELS = {
  high: '高', medium: '中', low: '低', unknown: '未知',
  strong: '充分', moderate: '一般', weak: '薄弱', insufficient: '不足',
  official: '官方来源', benchmark: '第三方评测', community: '社区口碑', leading: '前瞻信号', web: '普通网页',
  collect: '返回采集', analyze: '返回双分析', write: '进入写作',
  product: '产品', growth: '增长', strategy: '战略', monitoring: '监控', monitor: '监控',
  direct_substitute: '直接替代', capability_chaser: '能力追赶', distribution_power: '渠道强势',
  candidate_lead: '候选线索', verifiable_metadata: '可验证元数据', citable_content: '可引用正文证据',
  accepted: '已认可', rejected: '已驳回', edited: '已人工编辑', unreviewed: '未审核',
  fast: '快速', standard: '标准', deep: '深度',
  'O/B/C/L': '分层证据采集', 'SWOT Analysis': 'SWOT 分析', VRIO: 'VRIO 分析',
  'Evidence-Dialectic': '证据辩证调和', 'Decision-Synthesis': '决策综合',
};

// ══════════════════════════════════════════════════════════════════
// 安全辅助函数
// ══════════════════════════════════════════════════════════════════
function escapeHTML(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}
function renderMarkdownText(value) {
  return escapeHTML(zhText(value || ''))
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
}
function renderDebateMarkdown(value, splitClauses = false) {
  let text = zhText(value || '').trim();
  if (!text) return '<p>...</p>';
  if (splitClauses && !text.includes('\n')) {
    text = text.replace(/；\s*/g, '；\n\n');
  }
  return text.split(/\n{2,}/).map(block => {
    const safeBlock = escapeHTML(block.trim())
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\n/g, '<br>');
    const className = /^\s*\*\*/.test(block) ? ' class="debate-competitor"' : '';
    return `<p${className}>${safeBlock}</p>`;
  }).join('');
}
function safeURL(url) {
  if (!url) return '#';
  const u = String(url);
  return /^https?:\/\//i.test(u) ? escapeHTML(u) : '#';
}
function isSearchEntryURL(url) {
  const u = String(url || '').toLowerCase();
  return u.includes('/search') || u.includes('bing.com/search') || u.includes('s.weibo.com') ||
    u.includes('s.taobao.com') || u.includes('tianyancha.com/search') || u.includes('qcc.com/web/search');
}
function evidenceLinkLabel(url) {
  return isSearchEntryURL(url) ? '打开检索入口' : '查看具体来源';
}

const ROLE_LABELS = {
  collector: '采集 Agent',
  'analyst-a': '能力持久性分析 Agent · VRIO',
  'analyst-b': '市场动态与用户替代分析 Agent · SWOT',
  qa: '质检 Agent',
  writer: '撰写 Agent',
};
const STATUS_LABELS = {
  pending: '等待中',
  running: '处理中',
  completed: '已完成',
  error: '出错',
};
const TEXT_REPLACEMENTS = [
  [/user_substitution/g, FIELD_LABELS.user_substitution],
  [/capability_catch_up/g, FIELD_LABELS.capability_catch_up],
  [/strategic_expansion/g, FIELD_LABELS.strategic_expansion],
  [/evidence_strength/g, FIELD_LABELS.evidence_strength],
  [/quality_score/g, FIELD_LABELS.quality_score],
  [/claim_answer_rate/g, FIELD_LABELS.claim_answer_rate],
  [/bad_domain_leakage/g, FIELD_LABELS.bad_domain_leakage],
  [/official_capability/g, FIELD_LABELS.official_capability],
  [/pricing_or_packaging/g, FIELD_LABELS.pricing_or_packaging],
  [/community_pain/g, FIELD_LABELS.community_pain],
  [/third_party_benchmark/g, FIELD_LABELS.third_party_benchmark],
  [/distribution_signal/g, FIELD_LABELS.distribution_signal],
  [/github_release_velocity/g, FIELD_LABELS.github_release_velocity],
  [/strategic_expansion_signal/g, FIELD_LABELS.strategic_expansion_signal],
  [/method_findings/g, '方法推导记录'],
  [/evidence_refs/g, FIELD_LABELS.evidence_refs],
  [/mapped_dimensions/g, FIELD_LABELS.mapped_dimensions],
  [/乐观派/g, 'VRIO'],
  [/悲观派/g, 'SWOT'],
  [/Agent A \(乐观\)/g, 'Agent A (VRIO)'],
  [/Agent B \(悲观\)/g, 'Agent B (SWOT)'],
  [/Porter 五力/g, 'VRIO'],
  [/Porter Five Forces/g, 'VRIO'],
  [/Community signal gap/gi, '社区信号缺口'],
  [/community signal gap/gi, '社区信号缺口'],
  [/No community signal/gi, '缺少社区信号'],
  [/official source/gi, '官方来源'],
  [/community source/gi, '社区来源'],
  [/threat level/gi, '威胁等级'],
  [/expansion likelihood/gi, '扩张可能性'],
  [/confidence/gi, '置信度'],
  [/completed/gi, '已完成'],
  [/running/gi, '处理中'],
  [/error/gi, '出错'],
  [/^high$/gi, '高'],
  [/^medium$/gi, '中'],
  [/^low$/gi, '低'],
  [/^unknown$/gi, '未知'],
];
function zhText(value) {
  let text = String(value || '');
  TEXT_REPLACEMENTS.forEach(([pattern, replacement]) => {
    text = text.replace(pattern, replacement);
  });
  return text;
}
function fieldLabel(key) {
  return FIELD_LABELS[String(key || '')] || '其他指标';
}
function valueLabel(value) {
  const raw = String(value ?? '');
  return VALUE_LABELS[raw] || zhText(raw);
}
function roleLabel(output) {
  const role = output?.role || output?.node_id || '';
  return zhText(output?.label || ROLE_LABELS[role] || role || 'Agent');
}
function statusLabel(status) {
  return STATUS_LABELS[status] || zhText(status || '');
}
function formatLikelihood(value) {
  const n = Number(value || 0);
  if (!n) return '';
  return (n <= 1 ? n * 100 : n).toFixed(0) + '%';
}

// ══════════════════════════════════════════════════════════════════
// 视图导航
// ══════════════════════════════════════════════════════════════════
let currentView = 'dashboard';
function switchView(viewId) {
  if (viewId === currentView) return;
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('button[data-view]').forEach(b => b.classList.remove('active'));
  const target = document.getElementById('view-' + viewId);
  if (!target) return;
  target.classList.add('active');
  document.querySelectorAll('button[data-view="' + viewId + '"]').forEach(b => b.classList.add('active'));
  currentView = viewId;
  if (viewId === 'compare') refreshCompareView();
  if (viewId === 'report') renderFullReport(agentOutputs);
  if (viewId === 'history') refreshHistoryView();
  if (viewId === 'dashboard') { cy.resize(); cy.fit(undefined, 30); }
}
document.addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-view]');
  if (!btn) return;
  switchView(btn.dataset.view);
});

// ══════════════════════════════════════════════════════════════════
// 状态
// ══════════════════════════════════════════════════════════════════
let agentOutputs = [];
let connected = false;
let pipelineComplete = false;
let lastClickTime = 0;
let elapsedInterval = null;
let startTime = Date.now();
let pausedElapsedSec = 0;
let paused = false;
let activeCompetitorNames = [];
let analysisStartNotified = false;
let analysisCompleteNotified = false;
let discoveredSourceItems = [];
let manualEvidenceCount = 0;

// ══════════════════════════════════════════════════════════════════
// Cytoscape.js——竞争格局图
// ══════════════════════════════════════════════════════════════════
const cy = cytoscape({
  container: document.getElementById('cy'),
  style: [
    // 赛道中心节点
    { selector: 'node.track-center', style: {
      'background-color': '#1a1a2e', 'border-width': 3, 'border-color': '#4f46e5',
      'color': '#ffffff', 'padding': 20, 'shape': 'round-rectangle',
      'font-size': 16, 'font-weight': 'bold', 'text-valign': 'center', 'text-halign': 'center',
      'text-wrap': 'wrap', 'text-max-width': 160,
      'font-family': '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
    }},
    // 竞品节点基础样式
    { selector: 'node.competitor', style: {
      'shape': 'round-rectangle', 'background-color': '#ffffff', 'border-width': 2.5,
      'border-style': 'solid', 'color': '#1a1a2e', 'padding': 14,
      'font-size': 14, 'font-weight': 700, 'text-valign': 'center', 'text-halign': 'center',
      'text-wrap': 'wrap', 'text-max-width': 210,
      'font-family': '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
    }},
    { selector: 'node.semantic', style: {
      'shape': 'round-rectangle', 'background-color': '#f9f7f3',
      'border-width': 1.5, 'border-color': '#e4e0d8',
      'color': '#5c5852', 'padding': 8,
      'font-size': 13, 'font-weight': 600, 'text-valign': 'center', 'text-halign': 'center',
      'text-wrap': 'wrap', 'text-max-width': 120,
      'font-family': '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif',
    }},
    { selector: 'node.evidence-node', style: { 'background-color': '#eef2ff', 'border-color': '#4f46e5', 'color': '#3730a3' }},
    { selector: 'node.risk-node', style: { 'background-color': '#fff7ed', 'border-color': '#d97706', 'color': '#92400e' }},
    { selector: 'node.debate-node', style: { 'background-color': '#fef2f2', 'border-color': '#dc2626', 'color': '#991b1b' }},
    { selector: 'node.method-node', style: { 'background-color': '#ffffff', 'border-width': 2, 'font-size': 12, 'text-max-width': 90 }},
    { selector: 'node.method-a', style: { 'border-color': '#d97706', 'color': '#92400e' }},
    { selector: 'node.method-b', style: { 'border-color': '#7c3aed', 'color': '#5b21b6' }},
    // 威胁等级颜色
    { selector: 'node.threat-high', style: { 'border-color': '#dc2626', 'background-color': '#fef2f2' }},
    { selector: 'node.threat-medium', style: { 'border-color': '#d97706', 'background-color': '#fffbeb' }},
    { selector: 'node.threat-low', style: { 'border-color': '#16a34a', 'background-color': '#f0fdf4' }},
    { selector: 'node.competitor.threat-high', style: { 'border-color': '#dc2626', 'background-color': '#ffffff' }},
    { selector: 'node.competitor.threat-medium', style: { 'border-color': '#d97706', 'background-color': '#ffffff' }},
    { selector: 'node.competitor.threat-low', style: { 'border-color': '#16a34a', 'background-color': '#ffffff' }},
    // 分析师存在分歧时使用双边框
    { selector: 'node.disputed', style: { 'border-style': 'double', 'border-width': 5 }},
    // 悬停高亮
    { selector: 'node.competitor:active', style: { 'border-opacity': 1, 'border-width': 3.5 }},
    // 连线
    { selector: 'edge', style: {
      'line-color': '#c4bfb8', 'width': 1.5, 'curve-style': 'bezier',
      'line-style': 'solid', 'target-arrow-shape': 'none',
      'label': 'data(label)', 'font-size': 11, 'color': '#9b9690',
      'text-background-color': '#ffffff', 'text-background-opacity': 0.85,
      'text-background-padding': 2,
    }},
    { selector: 'edge.threat-high', style: { 'line-color': '#f87171', 'width': 3 }},
    { selector: 'edge.threat-medium', style: { 'line-color': '#fbbf24', 'width': 2 }},
    { selector: 'edge.threat-low', style: { 'line-color': '#4ade80', 'width': 1.5 }},
    { selector: 'edge.conflict-edge', style: {
      'line-color': '#ef4444', 'line-style': 'dashed', 'width': 2.8,
      'color': '#dc2626', 'font-weight': 700, 'text-background-color': '#fff7f7',
      'text-background-opacity': 0.95,
    }},
  ],
  layout: { name: 'preset' },
  maxZoom: 2, minZoom: 0.3,
});


function applyStaggerAnimation() {
  const nodes = cy.nodes().filter('.competitor');
  if (!nodes.length) return;
  nodes.addClass('stagger-start');
  cy.style().selector('node.stagger-start').style({ opacity: 0.3 }).update();
  const totalDuration = 400 + nodes.length * 200 + 100;
  nodes.forEach((node, i) => {
    node.delay(i * 200).animate({ style: { opacity: 1 }, duration: 400, easing: 'ease-out' });
  });
  setTimeout(() => { nodes.removeClass('stagger-start'); }, totalDuration);
}

// ══════════════════════════════════════════════════════════════════
// 竞争格局图
// ══════════════════════════════════════════════════════════════════
let landscapeBuilt = false;

const KNOWN_COMPETITORS = [
  { key: 'openai codex', display: 'OpenAI Codex' },
  { key: 'codex', display: 'OpenAI Codex' },
  { key: 'cursor', display: 'Cursor' },
  { key: 'copilot', display: 'GitHub Copilot' },
  { key: 'claude', display: 'Claude Code' },
  { key: 'qodo', display: 'Qodo' },
  { key: 'roo code', display: 'Roo Code' },
  { key: 'windsurf', display: 'Windsurf' },
  { key: 'faros', display: 'Faros AI' },
  { key: 'tongyi', display: '通义灵码' },
  { key: 'lingma', display: '通义灵码' },
  { key: 'tabnine', display: 'Tabnine' },
  { key: 'augment', display: 'Augment' },
  { key: 'codium', display: 'CodiumAI' },
  { key: 'codeium', display: 'CodiumAI' },
];

const NOISE_TERMS = /^(product\s*hunt|reddit|hacker\s*news|trustpilot|github|zhihu|juejin|twitter|linkedin|medium|dev\.to|stack\s*overflow|discord|slack|youtube|google|microsoft|community\s*signal\s*gap|signal\s*gap|社区信号缺口|社区信号|证据缺口|信息缺口|用户评价|用户反馈|用户评论|企业用户|企业版|企业客户|社区讨论|社区来源|官方来源|官方数据|缓存数据|产品评价|社区反馈|用户数据|客户评价|行业报告|数据源|检索入口|社媒来源|权威来源|手动官方|手动社区|用户输入|补充背景|证据源|来源标签|关键信息|竞品名称|赛道名称|产品名称|目标用户|核心能力|竞争担忧|产品定位|补充信息|未命名|未知来源|unspecified|unknown source)$/i;

const NON_COMPETITOR_KEYWORDS = /(用户评价|用户反馈|用户评论|企业用户|企业版|企业客户|社区讨论|社区来源|社区反馈|官方来源|官方数据|缓存数据|产品评价|检索入口|社媒来源|权威来源|数据源|证据|source|review|feedback|comment|enterprise\s*edition|cache\s*data|community\s*source|official\s*source|signal\s*gap|unknown)/i;

const SOURCE_PAGE_KEYWORDS = /(help\s*center|docs?|documentation|guide|blog|homepage|product\s*page|github|reddit|announcement|review|official\s*site|官网|主页|产品主页|帮助中心|使用指南|文档|博客|公告|技术评测|社区|来源|数据源|证据)/i;

function matchCompetitor(label) {
  const raw = String(label || '').trim();
  const lower = raw.toLowerCase();
  // 忽略大小写匹配用户输入的竞品名称
  for (const name of activeCompetitorNames) {
    if (name && lower.includes(name.toLowerCase())) return name;
  }
  for (const kc of KNOWN_COMPETITORS) {
    if (lower.includes(kc.key)) return kc.display;
  }
  if (activeCompetitorNames.length > 0) return null;
  // 降级策略：解析标签的第一段
  let name = raw.split(/\s*[-:|\\(（]\s*/)[0].trim();
  const m = name.match(/^([A-Za-z一-鿿]{2,}(?:\s+[A-Za-z][a-z]+)?(?:\s+[A-Za-z一-鿿]+)?)/);
  if (m) name = m[1];
  if (!name || name.length < 2 || name.length > 30) return null;
  if (SOURCE_PAGE_KEYWORDS.test(name)) return null;
  // 精确过滤已知的非竞品来源名称
  if (NOISE_TERMS.test(name)) return null;
  // 过滤包含通用非竞品关键词的名称
  if (NON_COMPETITOR_KEYWORDS.test(name)) return null;
  return name;
}

function matchCompetitorFromNames(label, names) {
  const raw = String(label || '').trim();
  const lower = raw.toLowerCase();
  // 按常见分隔符切词
  const labelTokens = lower.split(/[\s·\-|,，、。（）()\[\]【】\/\\]+/).filter(t => t.length > 1);
  for (const name of (names || [])) {
    const candidate = String(name || '').trim();
    if (!candidate) continue;
    const candidateLower = candidate.toLowerCase();
    // 精确子串匹配，用于简单名称
    if (lower.includes(candidateLower)) return candidate;
    // 分词匹配：名称词与标签词互相包含即可
    const nameTokens = candidateLower.split(/[\s·\-|,，、。（）()\[\]【】\/\\]+/).filter(t => t.length > 1);
    if (nameTokens.some(nt =>
      labelTokens.some(lt => lt.includes(nt) || nt.includes(lt))
    )) return candidate;
  }
  return null;
}

function normThreat(t) {
  if (!t) return null;
  if (typeof t === 'object') {
    if (typeof t.score === 'number') return scoreLevel(t.score);
    if (t.level) return normThreat(t.level);
    return null;
  }
  const v = String(t).trim();
  if (/高|high/i.test(v)) return 'high';
  if (/中|medium|mid/i.test(v)) return 'medium';
  if (/低|低|low/i.test(v)) return 'low';
  return v.toLowerCase();
}

function threatAssessmentText(value) {
  if (!value) return '';
  if (typeof value !== 'object') return zhText(value);
  return Object.entries(value).map(([name, item]) => {
    const label = fieldLabel(name);
    if (!item || typeof item !== 'object') return label + ': ' + zhText(item);
    const level = valueLabel(item.level || '');
    const score = item.score ?? '';
    const evidence = item.evidence_strength ? ' · ' + valueLabel(item.evidence_strength) : '';
    return label + ': ' + level + ' ' + score + evidence;
  }).join('\n');
}

function extractCompetitors(outputs) {
  const compMap = new Map();
  const names = viewCompetitorNames(outputs || []);
  names.forEach(name => {
    compMap.set(name, { name, evidence: [], threats: [], analysts: new Set() });
  });

  (outputs || []).forEach(o => {
    (o.evidence || []).forEach(e => {
      const label = (e.source_label || '').trim();
      const name = matchCompetitorFromNames(label, names) || matchCompetitor(label);
      if (!name) return;
      if (names.length && !names.some(n => n.toLowerCase() === String(name).toLowerCase())) return;
      if (!compMap.has(name)) compMap.set(name, { name, evidence: [], threats: [], analysts: new Set() });
      const entry = compMap.get(name);
      entry.evidence.push(e);
      entry.analysts.add(o.role || o.node_id);
    });

    const scores = o.threat_scores;
    if (scores && typeof scores === 'object') {
      compMap.forEach((entry, name) => {
        const scoreObj = scores[name] || scores[Object.keys(scores).find(k => k.toLowerCase() === name.toLowerCase())];
        const norm = normalizeThreatScores(scoreObj);
        if (norm && norm.overall !== null) entry.threats.push(scoreLevel(norm.overall));
      });
    }

    if (o.threat_assessment && typeof o.threat_assessment === 'object') {
      compMap.forEach((entry, name) => {
        const key = Object.keys(o.threat_assessment).find(k => k.toLowerCase() === name.toLowerCase());
        const item = o.threat_assessment[name] || (key ? o.threat_assessment[key] : null);
        const t = normThreat(item);
        if (t) entry.threats.push(t);
      });
    } else if (o.threat_assessment) {
      const t = normThreat(o.threat_assessment);
      if (t) compMap.forEach(entry => { if (entry.threats.length === 0) entry.threats.push(t); });
    }
  });

  // 标记没有证据的高威胁项，供界面提示
  compMap.forEach((entry) => {
    const hasHighThreat = entry.threats.some(t => t === 'high');
    entry.noEvidence = hasHighThreat && entry.evidence.length === 0;
  });

  return compMap;
}


function assessThreatLevel(entry) {
  if (entry.threats.length > 0) {
    const high = entry.threats.filter(t => t === 'high').length;
    const med = entry.threats.filter(t => t === 'medium').length;
    if (high > med) return 'high';
    if (med > 0) return 'medium';
    return 'low';
  }
  let score = 0;
  entry.evidence.forEach(e => {
    const text = (e.quote || '') + (e.relevance || '') + (e.source_label || '');
    if (/威胁|threat|领先|dominate|碾压|替代|replace|超越|surpass|垄断/i.test(text)) score += 2;
    if (/竞争|compete|对手|rival|挑战|challenge/i.test(text)) score += 1;
    if (/合作|partner|集成|integrate|补充|complement/i.test(text)) score -= 1;
  });
  const avg = entry.evidence.length > 0 ? score / entry.evidence.length : 0;
  return avg > 1.2 ? 'high' : avg > 0.3 ? 'medium' : 'low';
}

function getOutputByRole(outputs, role) {
  return (outputs || []).find(o => o.role === role || o.node_id === role);
}

function findDisagreementsForCompetitor(name, outputs) {
  const qa = getOutputByRole(outputs, 'qa');
  const allD = Array.isArray(qa?.disagreements) ? qa.disagreements : [];
  const target = String(name || '').toLowerCase();
  return allD.filter(d => {
    const competitor = String(d.competitor || '').toLowerCase();
    return competitor ? competitor === target : Number(d.delta || 0) >= 0.25;
  });
}

function formatDisagreement(d) {
  const dim = d.dimension ? fieldLabel(d.dimension) : '分歧维度';
  const a = d.a_value ?? '?';
  const b = d.b_value ?? '?';
  const q = d.recommended_score ?? '?';
  const reason = d.qa_reason ? '；' + zhText(d.qa_reason) : '';
  return `${dim}: VRIO ${a} / SWOT ${b} → QA ${q}${reason}`;
}

function methodScoreLabel(name, outputs, role, fallback) {
  const output = getOutputByRole(outputs, role);
  const scores = output ? findThreatScoresFor(name, [output]) : null;
  const value = scores?.overall ?? fallback;
  return value == null ? '' : `\n${value}`;
}

function hasAnalystDisagreement(entry, outputs) {
  return findDisagreementsForCompetitor(entry.name, outputs).length > 0;
}

function buildLandscapeMap(outputs) {
  cy.elements().remove();
  landscapeBuilt = false;

  // 合并用户输入名称与证据中提取的名称
  const competitors = extractCompetitors(outputs);
  const userNames = uniqueNames(activeCompetitorNames);

  // 确保每个用户输入的竞品都有条目，即使尚无证据
  userNames.forEach(name => {
    if (!competitors.has(name)) {
      competitors.set(name, { name, evidence: [], threats: [], analysts: new Set() });
    }
  });

  if (competitors.size === 0) return;

  const trackName = (document.getElementById('track-badge')?.textContent || '赛道中心').trim();
  const canvasW = document.getElementById('cy').clientWidth || 700;
  const canvasH = document.getElementById('cy').clientHeight || 560;
  const cx = canvasW / 2, cy_ = canvasH / 2;

  cy.add({
    group: 'nodes',
    data: { id: 'track-center', label: '赛道：' + trackName },
    classes: 'track-center',
    position: { x: cx, y: cy_ },
  });

  const sorted = [...competitors.entries()];
  const anyDisputed = sorted.some(([, entry]) => hasAnalystDisagreement(entry, outputs));

  sorted.forEach(([name, entry], i) => {
    const threat = assessThreatLevel(entry);
    const isDisputed = hasAnalystDisagreement(entry, outputs);
    const nodeId = 'comp-' + String(name).replace(/[^a-zA-Z0-9一-鿿]/g, '-');
    let classes = 'competitor threat-' + threat;
    if (isDisputed) classes += ' disputed';

    const angle = (i / Math.max(sorted.length, 1)) * 2 * Math.PI - Math.PI / 2;
    const radius = Math.min(canvasW, canvasH) * 0.34;
    const x = cx + Math.cos(angle) * radius;
    const y = cy_ + Math.sin(angle) * radius;

    cy.add({
      group: 'nodes',
      data: {
        id: nodeId,
        label: '竞品：' + name + '\n' + (THREAT_LABELS[threat] || threat) + ' · ' + (entry.evidence.length || 0) + ' 条证据',
        compName: name,
        threat,
        disputed: isDisputed,
        evidenceCount: entry.evidence.length,
        noEvidence: entry.noEvidence || false,
      },
      classes,
      position: { x, y },
    });

    cy.add({
      group: 'edges',
      data: { id: 'track->' + nodeId, source: 'track-center', target: nodeId, label: '对我方威胁' },
      classes: 'threat-' + threat,
    });

    const semanticNodes = [
      { id: nodeId + '-evidence', label: '证据\n' + (entry.evidence.length || 0) + ' 条', classes: 'semantic evidence-node', offsetX: -95, offsetY: 82, edgeLabel: '证据支持' },
      { id: nodeId + '-risk', label: '判断\n' + (THREAT_LABELS[threat] || threat), classes: 'semantic risk-node', offsetX: 95, offsetY: 82, edgeLabel: '风险判断' },
    ];
    const disputes = findDisagreementsForCompetitor(name, outputs);
    if (isDisputed) {
      semanticNodes.push(
        { id: nodeId + '-vrio', label: 'VRIO' + methodScoreLabel(name, outputs, 'analyst-a', null), classes: 'semantic method-node method-a', offsetX: -112, offsetY: -95, edgeLabel: 'A 论据' },
        { id: nodeId + '-swot', label: 'SWOT' + methodScoreLabel(name, outputs, 'analyst-b', null), classes: 'semantic method-node method-b', offsetX: 112, offsetY: -95, edgeLabel: 'B 论据' },
        { id: nodeId + '-debate', label: 'QA\n调和', classes: 'semantic debate-node', offsetX: 0, offsetY: -150, edgeLabel: '分歧复核', noDefaultEdge: true }
      );
    }

    semanticNodes.forEach(sn => {
      cy.add({ group: 'nodes', data: { id: sn.id, label: sn.label, compName: name, threat, disputed: isDisputed, disputes }, classes: sn.classes, position: { x: x + sn.offsetX, y: y + sn.offsetY } });
      if (!sn.noDefaultEdge) cy.add({ group: 'edges', data: { id: nodeId + '->' + sn.id, source: nodeId, target: sn.id, label: sn.edgeLabel } });
    });
    if (isDisputed && disputes.length) {
      const summary = disputes.map(formatDisagreement).join('\n');
      cy.add({
        group: 'edges',
        data: { id: nodeId + '-vrio->debate', source: nodeId + '-vrio', target: nodeId + '-debate', label: '论据冲突', compName: name, summary },
        classes: 'conflict-edge',
      });
      cy.add({
        group: 'edges',
        data: { id: nodeId + '-swot->debate', source: nodeId + '-swot', target: nodeId + '-debate', label: '论据冲突', compName: name, summary },
        classes: 'conflict-edge',
      });
    }
  });

  landscapeBuilt = true;
  const initOverlay = document.getElementById('pipeline-init');
  if (initOverlay) initOverlay.classList.remove('visible');

  if (anyDisputed) {
    const aA = getOutputByRole(outputs, 'analyst-a');
    const aB = getOutputByRole(outputs, 'analyst-b');
    const qa = getOutputByRole(outputs, 'qa');
    if (aA && aB) showDebateCallout(aA, aB, qa);
  }

  cy.layout({
    name: 'cose', animate: true, animationDuration: 650, fit: true, padding: 48,
    randomize: false, componentSpacing: 70, nodeOverlap: 20, idealEdgeLength: 170, gravity: 0.9, numIter: 800, initialTemp: 180,
  }).run();
}


// ══════════════════════════════════════════════════════════════════
// 时间线面板——可展开的编辑卡片
// ══════════════════════════════════════════════════════════════════
function buildExpandableDetail(output) {
  let html = '';
  // 方法框架与输入摘要
  if (output.framework || output.input_summary) {
    html += '<div class="tl-detail-section">';
    if (output.framework) html += '<span class="framework-tag">' + escapeHTML(valueLabel(output.framework)) + '</span>';
    if (output.input_summary) html += '<p>' + escapeHTML(zhText(output.input_summary)) + '</p>';
    html += '</div>';
  }
  // 展示分析师从方法准则到威胁维度的可审计推导链。
  if (Array.isArray(output.method_findings) && output.method_findings.length > 0) {
    html += '<div class="tl-detail-section"><h4>方法推导记录</h4>';
    output.method_findings.forEach(item => {
      const dimensions = Array.isArray(item.mapped_dimensions)
        ? item.mapped_dimensions.map(fieldLabel).join('、')
        : '';
      const refs = Array.isArray(item.evidence_refs) ? item.evidence_refs.join('、') : '';
      html += '<div class="ev-step"><strong>' + escapeHTML(zhText(item.competitor || '')) +
        ' · ' + escapeHTML(zhText(item.criterion || '分析准则')) + '</strong>：' +
        escapeHTML(zhText(item.finding || '')) +
        '<div class="ev-relevance">推导：' + escapeHTML(zhText(item.reasoning || '')) + '</div>' +
        '<div class="ev-relevance">不确定性：' + escapeHTML(zhText(item.uncertainty || '无')) +
        (refs ? ' · 证据：' + escapeHTML(refs) : '') +
        (dimensions ? ' · 影响：' + escapeHTML(dimensions) : '') + '</div></div>';
    });
    html += '</div>';
  }
  // 分歧记录
  if (output.disagreements && output.disagreements.length > 0) {
    html += '<div class="tl-detail-section"><h4>分歧记录</h4>';
    output.disagreements.forEach(d => {
      html += '<p>' + escapeHTML(d.dimension ? fieldLabel(d.dimension) : '分歧维度') + ': 差异 ' + escapeHTML(String(d.delta || '')) + '</p>';
    });
    html += '</div>';
  }
  // 威胁评估与扩张可能性
  if (output.threat_assessment || output.expansion_likelihood) {
    html += '<div class="tl-detail-section"><h4>评估指标</h4>';
    if (output.threat_assessment) html += '<p>威胁等级: <strong>' + escapeHTML(threatAssessmentText(output.threat_assessment)).replace(/\n/g, '<br>') + '</strong></p>';
    if (output.expansion_likelihood) html += '<p>跨界扩张概率: <strong>' + formatLikelihood(output.expansion_likelihood) + '</strong></p>';
    html += '</div>';
  }
  // Quality Gate 复审指标
  const qm = output.quality_metrics;
  if (qm && Object.keys(qm).length > 0) {
    const acquisitionTrace = Array.isArray(qm.acquisition_trace) ? qm.acquisition_trace : [];
    if (acquisitionTrace.length > 0) {
      html += '<div class="tl-detail-section"><h4>采集追踪</h4>';
      acquisitionTrace.slice(-12).forEach(event => {
        const stage = {
          search_complete: '搜索完成', competitor_complete: '竞品采集完成',
          tool_merge: '返工证据写回', batch_complete: '采集批次完成',
          competitor_failed: '竞品采集降级', batch_timeout: '采集预算超时'
        }[event.stage] || zhText(event.stage || '采集动作');
        html += '<p><strong>' + escapeHTML(event.competitor || '全局') + '</strong> · ' +
          escapeHTML(stage) + ' · ' + escapeHTML(String(event.latency_ms || 0)) + 'ms · ' +
          escapeHTML(event.outcome === 'accepted' ? '已取得证据' : event.outcome === 'partial' ? '部分完成' : zhText(event.outcome || '完成')) + '</p>';
      });
      html += '</div>';
    }
    if (typeof qm.quality_score === 'number') {
    const delta = typeof qm.score_delta === 'number'
      ? (qm.score_delta >= 0 ? '+' : '') + qm.score_delta.toFixed(1)
      : '首次评估';
    const completeness = typeof qm.matrix_completeness === 'number'
      ? (qm.matrix_completeness * 100).toFixed(0) + '%'
      : '—';
    html += '<div class="tl-detail-section"><h4>Quality Gate 复审指标</h4>';
    html += '<p>综合质量分: <strong>' + escapeHTML(String(qm.quality_score ?? '—')) + '</strong>（变化 ' + escapeHTML(delta) + '）</p>';
    html += '<p>矩阵完整度: <strong>' + escapeHTML(completeness) + '</strong> · 证据缺口: <strong>' + escapeHTML(String(qm.evidence_gap_count ?? 0)) + '</strong></p>';
    html += '<p>显著分歧: <strong>' + escapeHTML(String(qm.actionable_disagreement_count ?? 0)) + '</strong> · 结构错误: <strong>' + escapeHTML(String(qm.structural_error_count ?? 0)) + '</strong></p>';
    if (typeof qm.method_trace_coverage === 'number') {
      html += '<p>方法推导覆盖率: <strong>' + escapeHTML((qm.method_trace_coverage * 100).toFixed(0) + '%') + '</strong></p>';
    }
    if (Number(qm.evaluated_sources || 0) > 0) {
      const relevance = (Number(qm.relevance_precision_at_5 || 0) * 100).toFixed(0) + '%';
      const answerRate = (Number(qm.claim_answer_rate || 0) * 100).toFixed(0) + '%';
      const leakage = (Number(qm.bad_domain_leakage || 0) * 100).toFixed(0) + '%';
      html += '<p>前五条证据相关率: <strong>' + escapeHTML(relevance) + '</strong> · 问题可回答率: <strong>' + escapeHTML(answerRate) + '</strong> · 低质域名泄漏率: <strong>' + escapeHTML(leakage) + '</strong></p>';
    }
    const history = Array.isArray(output.rework_history) ? output.rework_history : [];
    if (history.length > 0) {
      history.forEach((review, index) => {
        const metrics = review.metrics || {};
        html += '<p>第 ' + (index + 1) + ' 次评估：' +
          escapeHTML(valueLabel(review.route || '—')) +
          ' · ' + escapeHTML(String(metrics.quality_score ?? '—')) + ' 分</p>';
      });
    }
    html += '</div>';
    }
  }
  // 证据链
  if (output.evidence && output.evidence.length > 0) {
    html += '<div class="tl-detail-section"><h4>证据链</h4><div class="tl-evidence">';
    output.evidence.forEach(e => {
      html += '<div class="ev-step">' + escapeHTML(zhText(e.source_label || '')) + ': ' +
        (e.quote ? '"' + escapeHTML(zhText(e.quote).slice(0, 160)) + '"' : '') +
        '<div class="ev-relevance">' + escapeHTML(zhText(e.relevance || '')) + '</div></div>';
    });
    html += '</div></div>';
  }
  return html;
}

function addTimelineEntry(output) {
  const container = document.getElementById('timeline');
  const div = document.createElement('div');
  const hasDetail = !!(output.framework || output.input_summary || (output.evidence && output.evidence.length) || (output.method_findings && output.method_findings.length) || output.threat_assessment || output.quality_metrics);
  div.className = 'tl-card ' + (output.role || '') + (hasDetail ? ' expandable' : '');
  div.dataset.nodeId = output.node_id;

  const elapsed = output.timestamp ? ((new Date(output.timestamp) - startTime) / 1000).toFixed(0) : '--';
  const statusIcon = output.status === 'running' ? '◉' : output.status === 'completed' ? '●' : output.status === 'error' ? '✕' : '○';

  const detailHTML = hasDetail ? '<div class="tl-detail">' + buildExpandableDetail(output) + '</div>' : '';
  const summaryText = renderMarkdownText(output.output_summary || output.input_summary || '—');
  div.innerHTML =
    '<span class="tl-time">T+' + elapsed + 's ' + statusIcon + ' ' + statusLabel(output.status) + '</span>' +
    '<div class="tl-agent ' + escapeHTML(output.role || '') + '">' + escapeHTML(roleLabel(output)) + '</div>' +
    '<div class="tl-summary">' + (output.status === 'running' ? '<span class="typing">处理中</span>' : summaryText) + '</div>' +
    '<div class="tl-meta">' +
      (output.confidence ? '<span>置信度: ' + (output.confidence * 100).toFixed(0) + '%</span>' : '') +
      (output.framework ? '<span>框架: ' + escapeHTML(valueLabel(output.framework)) + '</span>' : '') +
    '</div>' +
    '<div class="tl-expand-hint"></div>' +
    detailHTML;

  // 单击展开或收起详情
  div.addEventListener('click', (e) => {
    if (Date.now() - lastClickTime < DEBOUNCE_MS) return;
    lastClickTime = Date.now();
    if (hasDetail) div.classList.toggle('expanded');
  });

  // 双击打开完整证据弹窗
  div.addEventListener('dblclick', () => { showEvidenceModal(output); });

  container.appendChild(div);
  container.scrollTop = container.scrollHeight;
}

function updateTimelineEntry(output) {
  const existing = document.querySelector(`.tl-card[data-node-id="${output.node_id}"]`);
  if (existing) { existing.remove(); }
  addTimelineEntry(output);
}

// 流程完成后自动展开所有卡片
function autoExpandAllCards() {
  const autoExpand = document.getElementById('setting-auto-expand');
  if (autoExpand && autoExpand.value === 'on') {
    document.querySelectorAll('.tl-card.expandable').forEach(c => c.classList.add('expanded'));
  }
}

// ══════════════════════════════════════════════════════════════════
// 证据弹窗
// ══════════════════════════════════════════════════════════════════
function showEvidenceModal(output) {
  const overlay = document.getElementById('modal-overlay');
  const title = document.getElementById('modal-title');
  const body = document.getElementById('modal-body');
  title.textContent = roleLabel(output) + ' — 证据链';
  let html = '<p style="font-size:15px;color:var(--text-secondary);margin-bottom:20px;line-height:1.7;"><strong style="color:var(--text-primary);font-family:var(--font-serif);">结论:</strong> ' +
             renderMarkdownText(output.output_summary || '—') + '</p>';
  if (output.evidence && output.evidence.length > 0) {
    output.evidence.forEach((e, i) => {
      html += '<div class="ev-item"><strong>证据 ' + (i + 1) + ': </strong>' + escapeHTML(zhText(e.source_label || '')) + '<br>' +
        '<a href="' + safeURL(e.source_url) + '" target="_blank" rel="noopener noreferrer">' + escapeHTML(evidenceLinkLabel(e.source_url)) + ': ' + escapeHTML(e.source_url || '') + '</a>' +
        '<div class="quote">' + renderMarkdownText(e.quote || '') + '</div>' +
        '<div style="font-size:14px;color:var(--text-muted);margin-top:6px;">' + renderMarkdownText(e.relevance || '') + '</div></div>';
    });
  } else {
    html += '<p style="font-size:15px;color:var(--text-muted);">无可用证据</p>';
  }
  body.innerHTML = html;
  overlay.classList.add('show');
}
function closeModal() {
  document.getElementById('modal-overlay').classList.remove('show');
}
document.getElementById('modal-close').addEventListener('click', closeModal);
document.getElementById('modal-overlay').addEventListener('click', (e) => { if (e.target === e.currentTarget) closeModal(); });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeModal();
    closeAnalysisPage();
    closeNoticeModal();
  }
});

function showNotice(title, body) {
  document.getElementById('notice-title').textContent = title;
  document.getElementById('notice-body').innerHTML = body;
  document.getElementById('notice-modal-overlay').classList.add('show');
}
function closeNoticeModal() {
  document.getElementById('notice-modal-overlay').classList.remove('show');
}
document.getElementById('notice-modal-close').addEventListener('click', closeNoticeModal);
document.getElementById('notice-ok').addEventListener('click', closeNoticeModal);
document.getElementById('notice-modal-overlay').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) closeNoticeModal();
});

function setExecAnalyzing(message) {
  const titleEl = document.getElementById('exec-title');
  const bodyEl = document.getElementById('exec-body');
  const metaEl = document.getElementById('exec-meta');
  if (!titleEl || !bodyEl) return;
  titleEl.textContent = '分析执行中…';
  bodyEl.classList.remove('is-empty');
  bodyEl.classList.add('is-running');
  bodyEl.innerHTML = '<span class="typing">' + escapeHTML(message || 'Agent 正在分析竞品证据') + '</span>';
  if (metaEl) metaEl.textContent = '';
}

// ══════════════════════════════════════════════════════════════════
// 新建分析页面
// ══════════════════════════════════════════════════════════════════
function closeAnalysisPage() {
  switchView('dashboard');
}
function openAnalysisPage() {
  resetAnalysisForm();
  switchView('analysis');
}
document.getElementById('btn-new-analysis').addEventListener('click', openAnalysisPage);
document.getElementById('sidebar-new-analysis').addEventListener('click', openAnalysisPage);
document.getElementById('analysis-page-close').addEventListener('click', closeAnalysisPage);

// 读取选择题答案，并把它们转换成后端现有字段所需的中文描述。
function selectedChoiceValues(name) {
  return [...document.querySelectorAll('input[name="' + name + '"]:checked')]
    .map(input => input.value)
    .filter(Boolean);
}

function selectedChoiceText(name, fallback = '') {
  const values = selectedChoiceValues(name);
  return values.length ? values.join('、') : fallback;
}

document.getElementById('analysis-form-scroll').addEventListener('change', (event) => {
  const input = event.target;
  if (!(input instanceof HTMLInputElement)) return;

  // “暂不限定”与具体用户类型互斥，避免生成矛盾答案。
  if (input.type === 'checkbox' && input.checked) {
    const peers = document.querySelectorAll('input[name="' + input.name + '"]');
    if (input.hasAttribute('data-exclusive')) {
      peers.forEach(peer => { if (peer !== input) peer.checked = false; });
    } else {
      peers.forEach(peer => { if (peer.hasAttribute('data-exclusive')) peer.checked = false; });
    }
  }
});

function resetAnalysisForm() {
  document.getElementById('ana-status').textContent = '';
  document.getElementById('ana-status').className = 'form-status';
  document.getElementById('ana-target-name').value = '';
  document.querySelectorAll('#analysis-form-scroll input[type="radio"], #analysis-form-scroll input[type="checkbox"]')
    .forEach(input => { input.checked = input.hasAttribute('data-default'); });
  document.getElementById('ana-context').value = '';
  discoveredSourceItems = [];
  manualEvidenceCount = 0;
  const discoveryBox = document.getElementById('source-discovery-results');
  if (discoveryBox) {
    discoveryBox.classList.remove('show');
    discoveryBox.innerHTML = '';
  }
  const manualList = document.getElementById('manual-evidence-list');
  if (manualList) manualList.innerHTML = '';
  confirmedScope = null;
  scopeDraft = null;
  trackConfirmed = false;
  document.getElementById('btn-freeze-scope').hidden = true;
  document.getElementById('btn-confirm-track').hidden = true;
  document.getElementById('btn-add-scope-competitor').hidden = true;
  document.getElementById('btn-discover-track').textContent = '1. 自动识别赛道';
  document.getElementById('analysis-scope-panel').innerHTML =
    '<div class="source-discovery-header">尚未识别赛道</div>' +
    '<div class="sidebar-hint">填写我方产品名称后，先自动识别赛道。</div>';
}

function getScopeCompetitorNames() {
  const scope = confirmedScope || scopeDraft;
  if (!scope || !Array.isArray(scope.competitors)) return [];
  return scope.competitors
    .filter((item, index) => {
      if (confirmedScope) return true;
      const checkbox = document.querySelector('.scope-competitor-check[data-index="' + index + '"]');
      return !checkbox || checkbox.checked;
    })
    .map(item => String(item.name || '').trim())
    .filter(Boolean);
}

function renderDiscoveredSources(items) {
  const box = document.getElementById('source-discovery-results');
  discoveredSourceItems = Array.isArray(items) ? items : [];
  if (discoveredSourceItems.length === 0) {
    box.classList.add('show');
    box.innerHTML = '<div class="source-discovery-header">暂未生成证据来源，请检查竞品名称。</div>';
    return;
  }

  let html = '<div class="source-discovery-header"><span>证据来源已生成。可直接采集的具体页面默认勾选；候选入口默认不勾选；前瞻风向标用于识别招聘、技术足迹、招投标等早期信号。</span>' +
    '<div class="source-bulk-actions"><button class="source-bulk-btn" type="button" data-source-action="all-select">全部勾选</button>' +
    '<button class="source-bulk-btn" type="button" data-source-action="all-clear">全部取消</button></div></div>';
  const sectionMeta = {
    direct: { title: '可直接采集', hint: '具体页面，会在分析前尝试抓取正文并校验质量。' },
    candidate: { title: '候选入口', hint: '搜索页/聚合页，只作为找证据入口，默认不进入强证据。' },
    leading: { title: '前瞻风向标', hint: '招聘、GitHub、招投标、专利等领先指标，用于补充趋势判断。' },
  };
  discoveredSourceItems.forEach((item, groupIndex) => {
    html += '<div class="source-group">';
    html += '<div class="source-group-title"><span>' + escapeHTML(item.name || ('竞品 ' + (groupIndex + 1))) + '</span>' +
      '<div class="source-bulk-actions"><button class="source-bulk-btn" type="button" data-source-action="group-select" data-group="' + groupIndex + '">本竞品全选</button>' +
      '<button class="source-bulk-btn" type="button" data-source-action="group-clear" data-group="' + groupIndex + '">本竞品取消</button></div></div>';
    ['direct', 'leading', 'candidate'].forEach(sectionKey => {
      const sectionSources = (item.sources || [])
        .map((source, sourceIndex) => ({ source, sourceIndex }))
        .filter(({ source }) => (source.source_group || (source.channel === 'leading' ? 'leading' : source.direct_evidence === false ? 'candidate' : 'direct')) === sectionKey);
      if (sectionSources.length === 0) return;
      if (sectionKey === 'candidate') {
        html += '<details class="source-section source-section-candidate"><summary>后台检索线索 ' +
          sectionSources.length + ' 条（不作为来源提交）</summary><div class="sidebar-hint">' +
          '这些搜索入口只用于后台继续发现具体内容页，不参与评分，也不会被“全部勾选”。</div></details>';
        return;
      }
      html += '<div class="source-section source-section-' + sectionKey + '">';
      html += '<div class="source-section-title">' + sectionMeta[sectionKey].title +
        '<span>' + sectionMeta[sectionKey].hint + '</span></div>';
      sectionSources.forEach(({ source, sourceIndex }) => {
        const inputId = 'source-' + groupIndex + '-' + sourceIndex;
        const status = source.source_status || (sectionKey === 'candidate' ? '候选入口' : sectionKey === 'leading' ? '前瞻风向标' : source.authority === 'high' ? '强证据' : '需人工确认');
        const checked = sectionKey === 'candidate' || source.direct_evidence === false ? '' : ' checked';
        html += '<div class="source-option">';
        html += '<input type="checkbox" class="source-check" id="' + inputId + '" data-group="' + groupIndex + '" data-source="' + sourceIndex + '"' + checked + '>';
        html += '<label for="' + inputId + '"><strong>' + escapeHTML(source.label || source.url || '证据来源') + '</strong> <span class="source-status source-status-' + sectionKey + '">' + escapeHTML(status) + '</span><br>';
        html += '<a href="' + safeURL(source.url) + '" target="_blank" rel="noopener noreferrer">' + escapeHTML(source.url || '') + '</a>';
        if (source.note) html += '<br>' + escapeHTML(source.note);
        html += '</label></div>';
      });
      html += '</div>';
    });
    html += '</div>';
  });
  box.classList.add('show');
  box.innerHTML = html;
  if (!box.dataset.bulkBound) {
    box.addEventListener('click', handleSourceBulkAction);
    box.dataset.bulkBound = '1';
  }
}

function setDiscoveredSourceChecks(selector, checked) {
  document.querySelectorAll(selector).forEach(input => {
    if (!input.disabled) input.checked = checked;
  });
}

function handleSourceBulkAction(event) {
  const btn = event.target.closest('[data-source-action]');
  if (!btn) return;
  const action = btn.dataset.sourceAction;
  if (action === 'all-select') {
    setDiscoveredSourceChecks('#source-discovery-results .source-check:not([data-source-group="candidate"])', true);
  } else if (action === 'all-clear') {
    setDiscoveredSourceChecks('#source-discovery-results .source-check', false);
  } else if (action === 'group-select') {
    setDiscoveredSourceChecks('#source-discovery-results .source-check[data-group="' + btn.dataset.group + '"]', true);
  } else if (action === 'group-clear') {
    setDiscoveredSourceChecks('#source-discovery-results .source-check[data-group="' + btn.dataset.group + '"]', false);
  }
}

function competitorSelectOptions(selectedName) {
  const names = [...new Set(getScopeCompetitorNames())];
  if (names.length === 0) return '<option value="">先填写竞品名称</option>';
  return names.map(name => {
    const selected = name === selectedName ? ' selected' : '';
    return '<option value="' + escapeHTML(name) + '"' + selected + '>' + escapeHTML(name) + '</option>';
  }).join('');
}

function refreshManualEvidenceCompetitors() {
  document.querySelectorAll('#manual-evidence-list .manual-comp').forEach(select => {
    const current = select.value;
    select.innerHTML = competitorSelectOptions(current);
  });
}

function addManualEvidenceRow() {
  const names = getScopeCompetitorNames();
  const status = document.getElementById('ana-status');
  if (names.length === 0) {
    status.textContent = '请先填写竞品名称，再添加具体证据。';
    status.className = 'form-status error';
    return;
  }
  manualEvidenceCount += 1;
  const row = document.createElement('div');
  row.className = 'manual-evidence-row';
  row.dataset.id = String(manualEvidenceCount);
  row.innerHTML =
    '<div class="row-top">' +
      '<select class="manual-comp">' + competitorSelectOptions(names[0]) + '</select>' +
      '<select class="manual-type"><option value="official">官方/权威</option><option value="community">社区/社媒</option><option value="leading">前瞻风向标</option></select>' +
      '<button class="manual-evidence-remove" type="button" aria-label="删除证据">&times;</button>' +
    '</div>' +
    '<div class="manual-url-row"><input class="manual-url" type="url" placeholder="具体页面 URL，例如官网页面、报告页、帖子页、商品页">' +
      '<button class="btn-secondary manual-fetch" type="button">抓取</button></div>' +
    '<input class="manual-label" type="text" placeholder="来源标签，例如 蜜雪冰城加盟政策页">' +
    '<textarea class="manual-text" placeholder="粘贴可引用摘录或你核验后的事实摘要，例如门店数、价格带、加盟条件、用户评价。"></textarea>';
  document.getElementById('manual-evidence-list').appendChild(row);
  row.querySelector('.manual-evidence-remove').addEventListener('click', () => row.remove());
  row.querySelector('.manual-fetch').addEventListener('click', () => fetchManualEvidence(row));
  status.textContent = '';
  status.className = 'form-status';
}

async function fetchManualEvidence(row) {
  const status = document.getElementById('ana-status');
  const urlInput = row.querySelector('.manual-url');
  const labelInput = row.querySelector('.manual-label');
  const textInput = row.querySelector('.manual-text');
  const btn = row.querySelector('.manual-fetch');
  const url = urlInput?.value.trim() || '';
  if (!url) {
    status.textContent = '请先填写具体页面 URL。';
    status.className = 'form-status error';
    return;
  }
  btn.disabled = true;
  btn.textContent = '抓取中';
  status.textContent = '正在抓取页面内容...';
  status.className = 'form-status';
  try {
    const resp = await fetch('/api/fetch-source', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    if (labelInput && !labelInput.value.trim()) labelInput.value = data.title || url;
    if (textInput && !textInput.value.trim()) textInput.value = data.text || '';
    row.dataset.fetchMethod = data.fetch_method || 'html_parser';
    row.dataset.candidateOnly = data.candidate_only ? 'true' : 'false';
    status.textContent = data.candidate_only
      ? '已抓取页面内容，但文本质量较弱，建议人工核对后再作为强证据。'
      : '已抓取页面内容，请快速核对摘录是否可引用。';
    status.className = 'form-status';
  } catch (e) {
    status.textContent = '自动抓取失败: ' + e.message + '。可手动粘贴摘录继续分析。';
    status.className = 'form-status error';
  } finally {
    btn.disabled = false;
    btn.textContent = '抓取';
  }
}

function collectManualSourcesFor(name, sourceType) {
  const rows = document.querySelectorAll('#manual-evidence-list .manual-evidence-row');
  const sources = [];
  rows.forEach(row => {
    const comp = row.querySelector('.manual-comp')?.value || '';
    const type = row.querySelector('.manual-type')?.value || 'official';
    if (comp !== name || type !== sourceType) return;
    const url = row.querySelector('.manual-url')?.value.trim() || '';
    const label = row.querySelector('.manual-label')?.value.trim() || '';
    const text = row.querySelector('.manual-text')?.value.trim() || '';
    if (!url && !text) return;
    sources.push({
      url,
      label: label || (name + (sourceType === 'official' ? ' 手动官方证据' : sourceType === 'leading' ? ' 手动前瞻证据' : ' 手动社区证据')),
      scraped_text: text || (name + ' 手动导入来源，尚未填写摘录文本：' + url),
      fetch_method: row.dataset.fetchMethod || 'manual',
    });
  });
  return sources;
}

function selectedOfficialSourcesFor(name, context) {
  const selected = [];
  document.querySelectorAll('#source-discovery-results .source-check:checked').forEach(input => {
    const group = discoveredSourceItems[Number(input.dataset.group)];
    if (!group || group.name !== name) return;
    const source = (group.sources || [])[Number(input.dataset.source)];
    if (source?.source_group === 'candidate' || source?.direct_evidence === false || isSearchEntryURL(source?.url)) return;
    if (!source || source.channel === 'community' || source.channel === 'leading') return;
    const evidenceUrl = source.evidence_url || source.url;
    if (!evidenceUrl) return;
    selected.push({
      url: evidenceUrl,
      label: source.label || (name + ' 权威来源'),
      scraped_text: [
        source.direct_evidence === false ? name + ' 检索入口，需进入具体页面后复核' : name + ' 具体证据源',
        source.note || '',
        context || '',
        evidenceUrl,
      ].filter(Boolean).join('；'),
    });
  });
  return selected;
}

function selectedLeadingSourcesFor(name, context) {
  const selected = [];
  document.querySelectorAll('#source-discovery-results .source-check:checked').forEach(input => {
    const group = discoveredSourceItems[Number(input.dataset.group)];
    if (!group || group.name !== name) return;
    const source = (group.sources || [])[Number(input.dataset.source)];
    if (source?.source_group === 'candidate' || source?.direct_evidence === false || isSearchEntryURL(source?.url)) return;
    if (!source || source.channel !== 'leading') return;
    const evidenceUrl = source.evidence_url || source.url;
    if (!evidenceUrl) return;
    selected.push({
      url: evidenceUrl,
      label: source.label || (name + ' 前瞻风向标'),
      scraped_text: [
        source.direct_evidence === false ? name + ' 前瞻检索入口，需进入具体招聘/招投标/GitHub/专利页面后复核' : name + ' 前瞻证据源',
        source.note || '',
        context || '',
        evidenceUrl,
      ].filter(Boolean).join('；'),
      fetch_method: 'candidate_search',
    });
  });
  return selected;
}

function selectedCommunitySourcesFor(name, context) {
  const selected = [];
  document.querySelectorAll('#source-discovery-results .source-check:checked').forEach(input => {
    const group = discoveredSourceItems[Number(input.dataset.group)];
    if (!group || group.name !== name) return;
    const source = (group.sources || [])[Number(input.dataset.source)];
    if (source?.source_group === 'candidate' || source?.direct_evidence === false || isSearchEntryURL(source?.url)) return;
    if (!source || source.channel !== 'community') return;
    const evidenceUrl = source.evidence_url || source.url;
    if (!evidenceUrl) return;
    selected.push({
      url: evidenceUrl,
      label: source.label || (name + ' 社媒来源'),
      scraped_text: [
        source.direct_evidence === false ? name + ' 社媒检索入口，需打开具体帖子/商品页后再作为证据' : name + ' 社媒证据源',
        source.note || '',
        context || '',
        evidenceUrl,
      ].filter(Boolean).join('；'),
    });
  });
  return selected;
}

function getAnalysisMode() {
  return document.querySelector('input[name="analysis-mode"]:checked')?.value || 'standard';
}

function scopeTrackFields(scope) {
  return '<div class="scope-track-fields">' +
    '<label><span class="sidebar-hint">赛道</span><input id="scope-track-input" type="text" value="' + escapeHTML(scope.broad_track || '') + '" placeholder="例如：餐饮与消费"></label>' +
    '<label><span class="sidebar-hint">细分赛道</span><input id="scope-subtrack-input" type="text" value="' + escapeHTML(scope.sub_track || '') + '" placeholder="例如：中式火锅"></label>' +
    '</div>';
}

function renderTrackDraft(scope) {
  const panel = document.getElementById('analysis-scope-panel');
  panel.innerHTML = '<div class="source-discovery-header">请确认自动识别的赛道</div>' +
    scopeTrackFields(scope) +
    '<div class="sidebar-hint">如果识别不准确，可直接修改；确认后系统才会发现竞品。</div>';
}

function renderScopeDraft(scope) {
  const panel = document.getElementById('analysis-scope-panel');
  const competitors = scope.competitors || [];
  panel.innerHTML = '<div class="source-discovery-header">已按确认赛道发现候选竞品</div>' +
    scopeTrackFields(scope) + competitors.map((item, index) =>
      '<label class="source-option"><input class="scope-competitor-check" type="checkbox" data-index="' + index + '" checked>' +
      '<span><strong>' + escapeHTML(item.name) + '</strong> · ' + escapeHTML(zhText(item.relationship_type)) +
      '<br><span class="sidebar-hint">' + escapeHTML(item.reason || '') + '；置信度 ' +
      Math.round(Number(item.confidence || 0) * 100) + '%</span></span></label>').join('') +
    '<div class="sidebar-hint">取消不相关竞品；缺少预期竞品时点击“+ 添加竞品”。修改赛道会清空本列表并要求重新发现。</div>';
  refreshManualEvidenceCompetitors();
}

function openScopeCompetitorInput() {
  const row = document.getElementById('scope-manual-add');
  row.hidden = false;
  document.getElementById('scope-manual-name').focus();
}

function closeScopeCompetitorInput() {
  document.getElementById('scope-manual-add').hidden = true;
  document.getElementById('scope-manual-name').value = '';
}

function saveScopeCompetitor() {
  const input = document.getElementById('scope-manual-name');
  const name = input.value.trim();
  if (!name) return;
  if (!scopeDraft) {
    scopeDraft = {
      subject: document.getElementById('ana-target-name').value.trim(),
      broad_track: '', sub_track: '', competitors: [], confirmed: false,
    };
  }
  const competitors = Array.isArray(scopeDraft.competitors) ? scopeDraft.competitors : [];
  if (!competitors.some(item => item.name.toLocaleLowerCase() === name.toLocaleLowerCase())) {
    competitors.push({
      name, relationship_type: 'user_added', reason: '用户手动添加', confidence: 1, selected: true,
    });
  }
  scopeDraft = { ...scopeDraft, competitors };
  confirmedScope = null;
  renderScopeDraft(scopeDraft);
  document.getElementById('btn-freeze-scope').hidden = false;
  closeScopeCompetitorInput();
}

async function discoverAnalysisTrack() {
  const product = document.getElementById('ana-target-name').value.trim();
  const status = document.getElementById('ana-status');
  if (!product) {
    status.textContent = '请先填写我方产品名称。'; status.className = 'form-status error'; return;
  }
  confirmedScope = null;
  trackConfirmed = false;
  const resp = await fetch('/api/discover-scope', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ product, stage: 'track', analysis_mode: getAnalysisMode() }),
  });
  if (!resp.ok) {
    status.textContent = await resp.text(); status.className = 'form-status error'; return;
  }
  scopeDraft = await resp.json();
  scopeDraft.competitors = [];
  renderTrackDraft(scopeDraft);
  document.getElementById('btn-confirm-track').hidden = false;
  document.getElementById('btn-add-scope-competitor').hidden = true;
  document.getElementById('btn-freeze-scope').hidden = true;
  document.getElementById('btn-discover-track').textContent = '重新识别赛道';
  status.textContent = '请检查赛道；不准确时可修改，确认后再发现竞品。';
  status.className = 'form-status';
}

async function confirmTrackAndDiscoverCompetitors() {
  const status = document.getElementById('ana-status');
  const product = document.getElementById('ana-target-name').value.trim();
  const track = document.getElementById('scope-track-input')?.value.trim() || '';
  const subTrack = document.getElementById('scope-subtrack-input')?.value.trim() || track;
  if (!track) {
    status.textContent = '请确认或填写赛道后再发现竞品。'; status.className = 'form-status error'; return;
  }
  const resp = await fetch('/api/discover-scope', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      product, track, sub_track: subTrack, stage: 'competitors', competitors: [],
      analysis_mode: getAnalysisMode(),
    }),
  });
  if (!resp.ok) {
    status.textContent = await resp.text(); status.className = 'form-status error'; return;
  }
  scopeDraft = await resp.json();
  scopeDraft = { ...scopeDraft, broad_track: track, sub_track: subTrack };
  trackConfirmed = true;
  confirmedScope = null;
  renderScopeDraft(scopeDraft);
  document.getElementById('btn-confirm-track').hidden = true;
  document.getElementById('btn-add-scope-competitor').hidden = false;
  document.getElementById('btn-freeze-scope').hidden = false;
  status.textContent = '赛道已确认。请检查候选竞品，补充或取消后冻结研究范围。';
  status.className = 'form-status';
}

function freezeAnalysisScope() {
  const status = document.getElementById('ana-status');
  if (!scopeDraft || !trackConfirmed) {
    status.textContent = '请先确认赛道并发现候选竞品。'; status.className = 'form-status error'; return;
  }
  scopeDraft = {
    ...scopeDraft,
    broad_track: document.getElementById('scope-track-input')?.value.trim() || scopeDraft.broad_track || '',
    sub_track: document.getElementById('scope-subtrack-input')?.value.trim() || scopeDraft.sub_track || '',
  };
  const selected = [...document.querySelectorAll('.scope-competitor-check:checked')]
    .map(input => scopeDraft.competitors[Number(input.dataset.index)]).filter(Boolean);
  if (!selected.length) {
    status.textContent = '请至少选择 1 个竞品；也可以在竞品输入框手动添加后重新识别。';
    status.className = 'form-status error'; return;
  }
  confirmedScope = { ...scopeDraft, competitors: selected, confirmed: true };
  document.getElementById('btn-freeze-scope').hidden = true;
  status.textContent = '研究范围已冻结。修改产品、赛道、竞品或分析模式后需要重新确认。';
  status.className = 'form-status';
}

function invalidateConfirmedScope() {
  if (!confirmedScope && !scopeDraft) return;
  confirmedScope = null;
  scopeDraft = null;
  trackConfirmed = false;
  document.getElementById('btn-freeze-scope').hidden = true;
  document.getElementById('btn-confirm-track').hidden = true;
  document.getElementById('btn-add-scope-competitor').hidden = true;
  document.getElementById('btn-discover-track').textContent = '1. 自动识别赛道';
  document.getElementById('analysis-scope-panel').innerHTML =
    '<div class="source-discovery-header">研究对象已变化，请重新识别赛道</div>';
}

document.getElementById('btn-discover-track')?.addEventListener('click', discoverAnalysisTrack);
document.getElementById('btn-confirm-track')?.addEventListener('click', confirmTrackAndDiscoverCompetitors);
document.getElementById('btn-freeze-scope')?.addEventListener('click', freezeAnalysisScope);
document.getElementById('btn-add-scope-competitor')?.addEventListener('click', openScopeCompetitorInput);
document.getElementById('btn-save-scope-competitor')?.addEventListener('click', saveScopeCompetitor);
document.getElementById('btn-cancel-scope-competitor')?.addEventListener('click', closeScopeCompetitorInput);
document.getElementById('scope-manual-name')?.addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); saveScopeCompetitor(); }
});

async function discoverSources() {
  const track = confirmedScope?.broad_track || scopeDraft?.broad_track || '';
  const names = [...new Set(getScopeCompetitorNames())];
  const product = document.getElementById('ana-target-name')?.value.trim() || '';
  const status = document.getElementById('ana-status');
  if (!product && names.length === 0) {
    status.textContent = '请先填写我方产品名称；竞品和赛道可以留空。';
    status.className = 'form-status error';
    return;
  }

  const btn = document.getElementById('btn-discover-sources');
  btn.disabled = true;
  btn.textContent = '正在发现来源...';
  status.textContent = '正在生成候选权威 URL...';
  status.className = 'form-status';

  try {
    const resp = await fetch('/api/discover-sources', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track, product, competitors: names }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    renderDiscoveredSources(data.items || []);
    status.textContent = '已列出候选 URL，请保留可信来源后开始分析。';
    status.className = 'form-status';
  } catch (e) {
    status.textContent = '来源发现失败: ' + e.message;
    status.className = 'form-status error';
  } finally {
    btn.disabled = false;
    btn.textContent = '先预览可采集来源（推荐）';
  }
}
document.getElementById('btn-discover-sources').addEventListener('click', discoverSources);
document.getElementById('btn-add-manual-evidence').addEventListener('click', addManualEvidenceRow);
document.getElementById('ana-target-name').addEventListener('input', invalidateConfirmedScope);
document.getElementById('analysis-form-scroll').addEventListener('change', event => {
  if (event.target?.name === 'analysis-mode') invalidateConfirmedScope();
});
document.getElementById('analysis-scope-panel').addEventListener('input', event => {
  if (!scopeDraft || !['scope-track-input', 'scope-subtrack-input'].includes(event.target?.id)) return;
  scopeDraft = {
    ...scopeDraft,
    broad_track: document.getElementById('scope-track-input')?.value.trim() || '',
    sub_track: document.getElementById('scope-subtrack-input')?.value.trim() || '',
  };
  confirmedScope = null;
});
document.getElementById('analysis-scope-panel').addEventListener('change', event => {
  if (!scopeDraft || !trackConfirmed || !['scope-track-input', 'scope-subtrack-input'].includes(event.target?.id)) return;
  scopeDraft = { ...scopeDraft, competitors: [] };
  trackConfirmed = false;
  confirmedScope = null;
  renderTrackDraft(scopeDraft);
  document.getElementById('btn-confirm-track').hidden = false;
  document.getElementById('btn-add-scope-competitor').hidden = true;
  document.getElementById('btn-freeze-scope').hidden = true;
  const status = document.getElementById('ana-status');
  status.textContent = '赛道已修改，请按新赛道重新发现竞品。';
  status.className = 'form-status';
});

document.getElementById('btn-submit-ana').addEventListener('click', async () => {
  const productName = document.getElementById('ana-target-name')?.value.trim() || '';
  if (!confirmedScope) {
    const status = document.getElementById('ana-status');
    status.textContent = '开始分析前必须先确认赛道和竞品范围。';
    status.className = 'form-status error';
    return;
  }
  const track = confirmedScope.broad_track || '';
  if (!productName) {
    document.getElementById('ana-status').textContent = '请填写我方产品名称。其他信息可以留空，由 Agent 自动补全。';
    document.getElementById('ana-status').className = 'form-status error';
    return;
  }
  const threatTarget = {
    name: productName,
    positioning: selectedChoiceText('target-positioning', '定位尚待确认'),
    target_users: selectedChoiceText('target-users', '用户范围不限'),
    core_capabilities: selectedChoiceText('target-capabilities', '产品功能与体验、价格与商业模式'),
    competitive_concern: selectedChoiceText('target-concern', '全面识别竞品对我方产品的威胁与机会'),
  };
  threatTarget.needs_confirmation = Object.entries({
    positioning: threatTarget.positioning,
    target_users: threatTarget.target_users,
    core_capabilities: threatTarget.core_capabilities,
    competitive_concern: threatTarget.competitive_concern,
  }).filter(([, value]) => !value).map(([key]) => key);
  threatTarget.confidence = threatTarget.needs_confirmation.length ? 'low' : 'medium';

  const names = [...new Set(getScopeCompetitorNames())];
  if (names.length > MAX_ANALYSIS_COMPETITORS) {
    document.getElementById('ana-status').textContent = `本轮最多分析 ${MAX_ANALYSIS_COMPETITORS} 个竞品，请精简后再启动。`;
    document.getElementById('ana-status').className = 'form-status error';
    return;
  }
  const context = document.getElementById('ana-context').value.trim();
  const competitors = names.map(name => {
    const manualOfficialSources = collectManualSourcesFor(name, 'official');
    const manualCommunitySources = collectManualSourcesFor(name, 'community');
    const manualLeadingSources = collectManualSourcesFor(name, 'leading');
    const officialSources = selectedOfficialSourcesFor(name, context);
    const communitySources = selectedCommunitySourcesFor(name, context);
    const leadingSources = selectedLeadingSourcesFor(name, context);
    return {
      company: name,
      official_sources: manualOfficialSources.concat(officialSources).length > 0
        ? manualOfficialSources.concat(officialSources)
        : [{ url: '', label: name + ' 信息', scraped_text: context || name }],
      community_sources: manualCommunitySources.concat(communitySources).length > 0
        ? manualCommunitySources.concat(communitySources)
        : [{ url: '', label: name + ' 用户反馈', scraped_text: context || (name + ' 暂无补充用户反馈') }],
      leading_sources: manualLeadingSources.concat(leadingSources),
    };
  });

  const btn = document.getElementById('btn-submit-ana');
  btn.disabled = true;
  btn.textContent = '分析中...';
  const status = document.getElementById('ana-status');
  status.textContent = competitors.length
    ? '正在启动分析流水线，请等待...'
    : '未填写竞品，Agent 将自动发现默认 3 个候选竞品并开始分析...';
  status.className = 'form-status';

  try {
    const resp = await fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // 旧接口核心字段仍保持兼容：JSON.stringify({ product_name: productName, track, threat_target: threatTarget, competitors })
      body: JSON.stringify({
        product_name: productName, track, threat_target: threatTarget, competitors,
        analysis_mode: getAnalysisMode(), scope_snapshot: confirmedScope,
      }),
    });
    if (resp.ok) {
      const started = await resp.json();
      activeAnalysisId = started.analysis_id || '';
      resetDashboardForNewAnalysis(
        track || productName,
        'Agent 正在补全我方画像、发现竞品并采集证据。'
      );
      status.textContent = '分析已启动。关闭面板后可在仪表盘查看实时进展。';
      status.className = 'form-status';
      showNotice(
        '分析已开始',
        '我方产品：<strong>' + escapeHTML(productName) + '</strong><br>' +
        (competitors.length
          ? '竞品：' + competitors.map(c => '<strong>' + escapeHTML(c.company) + '</strong>').join('、')
          : `竞品：Agent 自动发现默认 3 个，最多 ${MAX_ANALYSIS_COMPETITORS} 个。`) +
        '<br>Agent 流水线正在运行，结果会实时更新到仪表盘。'
      );
      analysisStartNotified = true;
      analysisCompleteNotified = false;
      document.getElementById('track-badge').textContent = track || productName;
      activeCompetitorNames = competitors.length ? competitors.map(c => c.company).filter(Boolean) : [];
      switchView('dashboard');
      agentOutputs = [];
      saveToLocalStorage();
      pipelineComplete = false;
      document.getElementById('timeline').innerHTML = '';
      document.getElementById('debate-callout').style.display = 'none';
      cy.elements().remove();
      cy.resize(); cy.fit(undefined, 30);
      const initOverlay = document.getElementById('pipeline-init');
      if (initOverlay) {
        initOverlay.textContent = '正在采集证据并补全竞品画像...';
        initOverlay.classList.add('visible');
      }
      setExecAnalyzing('Agent 正在补全我方画像、发现竞品并采集证据。');
      startTime = Date.now();
      paused = false; pipelineComplete = false;
      if (elapsedInterval) clearInterval(elapsedInterval);
      elapsedInterval = setInterval(updateElapsed, 1000);
      showPauseButton();
      setTimeout(() => { closeAnalysisPage(); btn.disabled = false; btn.textContent = '开始分析'; }, 900);
    } else {
      status.textContent = '启动失败: ' + (await resp.text());
      status.className = 'form-status error';
      btn.disabled = false; btn.textContent = '开始分析';
    }
  } catch (e) {
    status.textContent = '网络错误: ' + e.message;
    status.className = 'form-status error';
    btn.disabled = false; btn.textContent = '开始分析';
  }
});

// 辩论提示
// ══════════════════════════════════════════════════════════════════
function showDebateCallout(analystA, analystB, qa, disputeSummary = '') {
  const callout = document.getElementById('debate-callout');
  document.getElementById('debate-a-text').innerHTML = renderDebateMarkdown(analystA.output_summary, true);
  document.getElementById('debate-b-text').innerHTML = renderDebateMarkdown(analystB.output_summary, true);
  let verdictText = '';
  if (qa && qa.status === 'completed') {
    verdictText = '<div class="dc-verdict-title">质检结论</div><div class="debate-markdown qa-markdown">' +
      renderDebateMarkdown(qa.output_summary || '交叉验证完成') + '</div>';
  } else if (qa && qa.status === 'running') {
    verdictText = '<span class="typing">&#36136;&#26816; Agent &#20132;&#21449;&#39564;&#35777;&#20013;</span>';
  } else {
    verdictText = '&#36136;&#26816; Agent &#31561;&#24453;&#20013;';
  }
  if (disputeSummary) {
    verdictText += '<div class="dc-dispute"><strong>分析分歧摘要</strong>' +
      renderDebateMarkdown(disputeSummary, true) + '</div>';
  }
  document.getElementById('debate-verdict').innerHTML = verdictText;
  callout.style.display = 'block';
}

// ══════════════════════════════════════════════════════════════════
// 状态栏
// ══════════════════════════════════════════════════════════════════
function updateStatusBar(outputs) {
  const bar = document.getElementById('status-bar');
  bar.querySelectorAll('.agent-status').forEach(e => e.remove());
  if (!outputs || outputs.length === 0) return;
  const roles = ['collector', 'analyst-a', 'analyst-b', 'qa', 'writer'];
  const labels = ['采集', '分析A', '分析B', '质检', '撰写'];
  const roleMap = {};
  outputs.forEach(o => { roleMap[o.role || o.node_id] = o; });
  roles.forEach((role, i) => {
    const output = roleMap[role];
    let cls = 'pending';
    if (output) cls = output.status;
    const span = document.createElement('span');
    span.className = 'agent-status';
    span.innerHTML = '<span class="status-dot ' + cls + '"></span>' + labels[i];
    span.style.cssText = 'font-size:13px;color:var(--text-secondary);display:flex;align-items:center;gap:4px;';
    bar.appendChild(span);
  });
}
function updateElapsed() {
  if (paused) return;
  const sec = Math.floor((Date.now() - startTime) / 1000);
  const m = Math.floor(sec / 60).toString().padStart(2, '0');
  const s = (sec % 60).toString().padStart(2, '0');
  document.getElementById('elapsed').textContent = m + ':' + s;
}

function stopElapsed(finalSec) {
  if (elapsedInterval) { clearInterval(elapsedInterval); elapsedInterval = null; }
  const m = Math.floor(finalSec / 60).toString().padStart(2, '0');
  const s = (finalSec % 60).toString().padStart(2, '0');
  document.getElementById('elapsed').textContent = '总耗时: ' + m + ':' + s;
}

function togglePause() {
  const btn = document.getElementById('btn-pause');
  if (!btn) return;
  if (pipelineComplete) return;

  paused = !paused;
  if (paused) {
    pausedElapsedSec = Math.floor((Date.now() - startTime) / 1000);
    if (elapsedInterval) { clearInterval(elapsedInterval); elapsedInterval = null; }
    if (ws) { ws.close(); ws = null; }
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    btn.textContent = '▶ 恢复';
    btn.classList.add('is-paused');
    updateElapsed();
    showNotice('分析已暂停', 'WebSocket 已断开。点击「恢复」重新连接并从本地缓存恢复最新状态。');
  } else {
    startTime = Date.now() - pausedElapsedSec * 1000;
    elapsedInterval = setInterval(updateElapsed, 1000);
    connectWebSocket();
    btn.textContent = '⏸ 暂停';
    btn.classList.remove('is-paused');
    showNotice('分析已恢复', 'WebSocket 已重新连接，正在接收实时更新。');
  }
}

function showPauseButton() {
  const btn = document.getElementById('btn-pause');
  if (btn) { btn.style.display = ''; btn.textContent = '⏸ 暂停'; btn.classList.remove('is-paused'); }
}

function hidePauseButton() {
  const btn = document.getElementById('btn-pause');
  if (btn) { btn.style.display = 'none'; }
}

// ══════════════════════════════════════════════════════════════════
// 本地存储恢复
// ══════════════════════════════════════════════════════════════════
function saveToLocalStorage() {
  try { localStorage.setItem(LS_KEY, JSON.stringify(agentOutputs)); } catch (e) { console.warn('saveToLocalStorage failed:', e); showNotice('会话状态保存失败，可能是存储空间不足。'); }
}
function loadFromLocalStorage() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) { const data = JSON.parse(raw); if (Array.isArray(data) && data.length > 0) return data; }
  } catch (e) { console.warn('loadFromLocalStorage failed:', e); }
  return null;
}
function restoreFromLocalStorage() {
  const saved = loadFromLocalStorage();
  if (saved) {
    console.log('Restoring ' + saved.length + ' agent outputs from localStorage');
    saved.forEach(output => {
      agentOutputs.push(output);
      addTimelineEntry(output);
    });
    updateStatusBar(agentOutputs);
    buildLandscapeMap(agentOutputs);
    refreshDataBoundViews(agentOutputs);
    return true;
  }
  return false;
}

function refreshDataBoundViews(outputs) {
  renderA3Dashboard(outputs);
  refreshCompareView();
  renderFullReport(outputs);
  if (currentView === 'history') refreshHistoryView();
}

function applyOutputs(outputs) {
  activeCompetitorNames = [];
  analysisStartNotified = false;
  analysisCompleteNotified = true;
  pipelineComplete = true;
  hidePauseButton();
  stopElapsed(Math.floor((Date.now() - startTime) / 1000));
  agentOutputs = Array.isArray(outputs) ? outputs : [];
  if (agentOutputs.length === 0) {
    clearDashboardToEmpty();
    return;
  }
  document.getElementById('timeline').innerHTML = '';
  cy.elements().remove();
  agentOutputs.forEach(output => {
    addTimelineEntry(output);
  });
  updateStatusBar(agentOutputs);
  buildLandscapeMap(agentOutputs);
  refreshDataBoundViews(agentOutputs);
  cy.resize(); cy.fit(undefined, 30);
}

function clearDashboardToEmpty() {
  agentOutputs = [];
  pipelineComplete = false;
  fallbackLoaded = false;
  activeCompetitorNames = [];
  document.getElementById('track-badge').textContent = '等待分析';
  if (elapsedInterval) {
    clearInterval(elapsedInterval);
    elapsedInterval = null;
  }
  paused = false;
  document.getElementById('elapsed').textContent = '00:00';
  hidePauseButton();
  document.getElementById('exec-title').textContent = '尚未开始分析';
  const execBody = document.getElementById('exec-body');
  execBody.classList.remove('is-running');
  execBody.classList.add('is-empty');
  execBody.innerHTML =
    '<div class="empty-state"><h2>选择一个起点</h2><p>当前仪表盘保持空白，不会在初始化时自动分析。你可以从左侧打开历史结果、加载参考样例，或直接新建一次竞品分析。</p><div class="empty-actions"><button class="btn-secondary" id="empty-new-analysis" type="button">新建分析</button><button class="btn-secondary" id="empty-load-history" type="button">历史结果</button><button class="btn-secondary" id="empty-load-demo" type="button">参考样例</button></div></div>';
  document.getElementById('exec-meta').textContent = '';
  document.getElementById('heatmap-table').innerHTML = '<div class="exec-placeholder">等待分析数据</div>';
  document.getElementById('evidence-strip').innerHTML = '<div class="ev-placeholder">等待证据数据</div>';
  const legend = document.getElementById('radarLegend');
  if (legend) legend.innerHTML = '';
  const actionBox = document.getElementById('response-actions');
  if (actionBox) actionBox.innerHTML = '<div class="exec-placeholder">等待 Writer Agent 生成行动建议</div>';
  document.getElementById('timeline').innerHTML = '';
  document.getElementById('compare-grid').innerHTML = '<div class="cc-empty">尚未开始分析。</div>';
  document.getElementById('compare-chips').innerHTML = '';
  document.getElementById('debate-callout').style.display = 'none';
  document.getElementById('fallback-banner').classList.remove('show');
  const initOverlay = document.getElementById('pipeline-init');
  if (initOverlay) {
    initOverlay.textContent = '等待新建分析，或从左侧加载历史/参考样例';
    initOverlay.classList.add('visible');
  }
  if (radarChart) {
    radarChart.destroy();
    radarChart = null;
  }
  cy.elements().remove();
  updateStatusBar([]);
  setTimeout(() => {
    document.getElementById('empty-new-analysis')?.addEventListener('click', openAnalysisPage);
    document.getElementById('empty-load-history')?.addEventListener('click', () => document.getElementById('sidebar-load-history').click());
    document.getElementById('empty-load-demo')?.addEventListener('click', () => showSampleSelector());
  }, 0);
}

function resetDashboardForNewAnalysis(trackLabel, message) {
  clearDashboardToEmpty();
  localStorage.removeItem(LS_KEY);
  agentOutputs = [];
  pipelineComplete = false;
  fallbackLoaded = false;
  analysisStartNotified = true;
  analysisCompleteNotified = false;
  document.getElementById('track-badge').textContent = trackLabel || '分析中';
  setExecAnalyzing(message || 'Agent 正在补全我方画像、发现竞品并采集证据。');
  const initOverlay = document.getElementById('pipeline-init');
  if (initOverlay) {
    initOverlay.textContent = '正在采集证据并补全竞品画像...';
    initOverlay.classList.add('visible');
  }
}

function restoreLatestHistory() {
  if (analysisStartNotified && !pipelineComplete) return false;
  try {
    const history = loadHistoryRecords();
    if (history.length > 0 && Array.isArray(history[0].outputs)) {
      document.getElementById('track-badge').textContent = history[0].track || document.getElementById('track-badge').textContent;
      applyOutputs(history[0].outputs);
      refreshHistoryView();
      return true;
    }
  } catch (e) { console.warn('restoreLatestHistory failed:', e); showNotice('历史记录恢复失败。'); }
  return false;
}

// ══════════════════════════════════════════════════════════════════
// 降级数据加载器
// ══════════════════════════════════════════════════════════════════
let fallbackLoaded = false;
let currentFallbackUrl = '/data/demo-fallback.json';

function showSampleSelector() {
  document.getElementById('sample-selector-overlay').classList.add('show');
}

function hideSampleSelector() {
  document.getElementById('sample-selector-overlay').classList.remove('show');
}

document.getElementById('sample-selector-close').addEventListener('click', hideSampleSelector);
document.getElementById('sample-selector-overlay').addEventListener('click', function(e) {
  if (e.target === this) hideSampleSelector();
});

document.querySelectorAll('.sample-option-card').forEach(card => {
  card.addEventListener('click', async function() {
    const url = this.getAttribute('data-url');
    hideSampleSelector();
    fallbackLoaded = false;
    currentFallbackUrl = url;
    await loadFallbackData();
    switchView('dashboard');
    const label = url.includes('milktea') ? '新茶饮 · 霸王茶姬' : 'AI代码助手 · GitHub Copilot';
    showNotice('已加载样例', label + ' — 实时AI分析结果显示在仪表盘中。');
  });
});

async function loadFallbackData() {
  if (analysisStartNotified && !pipelineComplete) {
    console.warn('Skip sample fallback while custom analysis is running.');
    return;
  }
  if (fallbackLoaded) return;
  fallbackLoaded = true;
  try {
    const resp = await fetch(currentFallbackUrl);
    if (resp.ok) {
      const data = await resp.json();
      console.log('Loaded ' + data.length + ' fallback outputs from ' + currentFallbackUrl);
      applyOutputs(data);
      const aA = data.find(o => o.role === 'analyst-a' || o.node_id === 'analyst-a');
      const aB = data.find(o => o.role === 'analyst-b' || o.node_id === 'analyst-b');
      const qa = data.find(o => o.role === 'qa' || o.node_id === 'qa');
      if (aA && aB) {
        showDebateCallout(aA, aB, qa);
      }
      savePipelineHistory(data);
    }
  } catch (e) { console.log('No fallback data available from ' + currentFallbackUrl); }
}

// ══════════════════════════════════════════════════════════════════
// 流程历史记录，用于历史视图
// ══════════════════════════════════════════════════════════════════
function loadHistoryRecords({ prune = true } = {}) {
  let history = [];
  try {
    const parsed = JSON.parse(localStorage.getItem(HISTORY_LS_KEY) || '[]');
    history = Array.isArray(parsed) ? parsed : [];
  } catch (e) {
    history = [];
  }
  const filtered = history.filter(item => {
    const ts = new Date(item?.date || 0).getTime();
    return Number.isFinite(ts) && ts >= HISTORY_CUTOFF_TS;
  });
  if (prune && filtered.length !== history.length) {
    try { localStorage.setItem(HISTORY_LS_KEY, JSON.stringify(filtered)); } catch (e) { console.warn('savePipelineHistory prune failed:', e); }
  }
  return filtered;
}

function savePipelineHistory(outputs) {
  try {
    const existing = loadHistoryRecords();
    existing.unshift({
      date: new Date().toISOString(),
      track: document.getElementById('track-badge').textContent,
      agentCount: outputs.length,
      completedCount: outputs.filter(o => o.status === 'completed').length,
      outputs: outputs.map(o => ({ ...o })),
    });
    if (existing.length > 50) existing.length = 50;
    localStorage.setItem(HISTORY_LS_KEY, JSON.stringify(existing));
  } catch (e) { console.warn('savePipelineHistory failed:', e); }
}

// ══════════════════════════════════════════════════════════════════
// 完整报告——参考 Verda 的章节化阅读体验，适配当前 Writer 数据契约
// ══════════════════════════════════════════════════════════════════
const REPORT_SECTION_LABELS = {
  executive_summary: '执行摘要',
  key_findings: '关键结论速览',
  landscape: '竞争格局',
  competitor_profiles: '逐竞品档案',
  threat_matrix: '逐竞品威胁矩阵',
  key_debate: '方法分歧与质检裁决',
  risk_opportunity: '风险、机会与观察信号',
  threat_assessment: '威胁判断与我方防御',
  recommendations: '优先行动建议',
  evidence_coverage: '证据覆盖看板',
  evidence: '证据索引',
  methodology: '方法与质量审计',
  limitations: '证据限制与待补项',
};

function reportParagraphs(text) {
  const parts = String(text || '').split(/\n\s*\n|\n(?=[一二三四五六七八九十0-9]+[.、）)])/).filter(Boolean);
  return parts.map(part => '<p>' + escapeHTML(zhText(part.trim())).replace(/\n/g, '<br>') + '</p>').join('');
}

function uniqueReportEvidence(outputs) {
  const seen = new Set();
  const evidence = [];
  (outputs || []).forEach(output => {
    (output.evidence || []).forEach(item => {
      const key = item.source_url || (item.source_label + '|' + item.quote);
      if (!key || seen.has(key)) return;
      seen.add(key);
      evidence.push(item);
    });
  });
  return evidence;
}

function buildFullReportModel(outputs) {
  const writer = (outputs || []).find(output => output.role === 'writer' || output.node_id === 'writer');
  if (!writer || writer.status !== 'completed') return null;
  const qa = (outputs || []).find(output => output.role === 'qa' || output.node_id === 'qa') || {};
  const sections = writer.report_sections || {};
  const target = writer.threat_target || qa.threat_target || {};
  const scores = writer.threat_scores || qa.threat_scores || {};
  const assessment = writer.threat_assessment || qa.threat_assessment || {};
  const actions = Array.isArray(writer.response_actions)
    ? [...writer.response_actions].sort((a, b) => Number(b.priority || 0) - Number(a.priority || 0)) : [];
  const gaps = Array.isArray(writer.evidence_gaps) ? writer.evidence_gaps : (qa.evidence_gaps || []);
  // 完整报告汇总全链路证据，不能只展示 QA/Writer 转述时保留下来的子集。
  const evidence = uniqueReportEvidence(outputs);
  const timestamp = writer.timestamp ? new Date(writer.timestamp) : new Date();
  return {
    title: (target.name || document.getElementById('track-badge')?.textContent || '我方产品') + '竞品威胁分析报告',
    target,
    confidence: Number(writer.confidence || qa.confidence || 0),
    timestamp,
    sections: {
      executive_summary: sections.executive_summary || writer.output_summary || '',
      key_findings: sections.key_findings || '',
      landscape: sections.landscape || '',
      competitor_profiles: sections.competitor_profiles || '',
      key_debate: sections.key_debate || '',
      risk_opportunity: sections.risk_opportunity || '',
      threat_assessment: sections.threat_assessment || '',
      recommendations: sections.recommendations || '',
      methodology: sections.methodology || '',
    },
    scores,
    assessment,
    actions,
    gaps,
    evidence,
    quality: qa.quality_metrics || writer.quality_metrics || {},
    reworkHistory: qa.rework_history || writer.rework_history || [],
    competitorNotes: writer.per_competitor_notes || qa.per_competitor_notes || {},
  };
}

function reportScoreCell(value, evidenceStrength = '') {
  const noEvidence = /无证据/.test(String(evidenceStrength || ''));
  if (noEvidence) return '<span class="report-score-low">待评估</span>';
  const score = Number(value || 0);
  return '<span class="report-score-' + scoreLevel(score) + '">' + escapeHTML(scoreLabel(score)) + '</span>';
}

function reportDomain(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch (_) { return ''; }
}

function reportMetricStripHTML(model) {
  const domains = new Set(model.evidence.map(item => reportDomain(item.source_url)).filter(Boolean));
  const strong = Object.values(model.assessment || {}).filter(item =>
    !/不足|无证据|待确认/.test(String(item?.evidence_strength || ''))).length;
  const metrics = [
    ['纳入竞品', Object.keys(model.scores || {}).length, '个'],
    ['可引用证据', model.evidence.length, '条'],
    ['独立信源', domains.size, '个'],
    ['证据较充分竞品', strong, '个'],
    ['QA 返工', model.reworkHistory.length, '轮'],
  ];
  return '<div class="report-metric-strip">' + metrics.map(([label, value, unit]) =>
    '<div class="report-metric"><strong>' + value + '</strong><span>' + unit + '</span><small>' + label + '</small></div>').join('') + '</div>';
}

function strongestThreatDimension(score) {
  return THREAT_DIMS.reduce((best, item) =>
    Number(score?.[item.key] || 0) > Number(score?.[best.key] || 0) ? item : best, THREAT_DIMS[0]);
}

function reportKeyFindingsHTML(model) {
  const intro = model.sections.key_findings ? reportParagraphs(model.sections.key_findings) : '';
  const ranked = Object.entries(model.scores || {}).sort((a, b) => Number(b[1].overall || 0) - Number(a[1].overall || 0)).slice(0, 4);
  const cards = ranked.map(([name, score], index) => {
    const dimension = strongestThreatDimension(score);
    const strength = model.assessment[name]?.evidence_strength || '待确认';
    return '<article class="report-finding-card"><div class="report-finding-rank">' + (index + 1) + '</div><div>' +
      '<h3>' + escapeHTML(name) + ' · ' + escapeHTML(scoreLabel(score.overall)) + '</h3>' +
      '<p>最突出维度：' + escapeHTML(dimension.label) + '（' + Number(score[dimension.key] || 0) + '）</p>' +
      '<span class="report-evidence-badge">证据：' + escapeHTML(zhText(strength)) + '</span></div></article>';
  }).join('');
  return intro + '<div class="report-finding-grid">' + cards + '</div>';
}

function reportCompetitorProfilesHTML(model) {
  const intro = model.sections.competitor_profiles ? reportParagraphs(model.sections.competitor_profiles) : '';
  const cards = Object.entries(model.scores || {}).sort((a, b) => Number(b[1].overall || 0) - Number(a[1].overall || 0)).map(([name, score]) => {
    const dimension = strongestThreatDimension(score);
    const assessment = model.assessment[name] || {};
    const note = model.competitorNotes[name] || assessment.reason || assessment.summary ||
      '当前结构化结论主要来自威胁矩阵，详细定位仍需结合证据索引复核。';
    return '<article class="report-profile-card"><div class="report-profile-head"><h3>' + escapeHTML(name) + '</h3>' +
      reportScoreCell(score.overall, assessment.evidence_strength) + '</div>' +
      '<div class="report-profile-tags"><span>' + escapeHTML(dimension.label) + ' ' + Number(score[dimension.key] || 0) + '</span>' +
      '<span>证据 ' + escapeHTML(zhText(assessment.evidence_strength || '待确认')) + '</span></div>' +
      '<p>' + escapeHTML(zhText(note)) + '</p></article>';
  }).join('');
  return intro + '<div class="report-profile-grid">' + cards + '</div>';
}

function evidencePortfolioType(item) {
  const value = String(item.source_tier || item.source_type || '').toLowerCase();
  if (/official|官方|\bo\b/.test(value)) return 'official';
  if (/community|社区|社媒|\bc\b/.test(value)) return 'community';
  if (/leading|前瞻|\bl\b/.test(value)) return 'leading';
  return 'benchmark';
}

function reportEvidenceCoverageHTML(model) {
  const meta = {
    official: ['O', '官方来源'], benchmark: ['B', '权威媒体/基准'],
    community: ['C', '社区口碑'], leading: ['L', '前瞻信号'],
  };
  const counts = { official: 0, benchmark: 0, community: 0, leading: 0 };
  model.evidence.forEach(item => { counts[evidencePortfolioType(item)] += 1; });
  const cards = Object.entries(meta).map(([key, [code, label]]) =>
    '<div class="report-coverage-card ' + (counts[key] ? '' : 'is-missing') + '"><b>' + code + '</b><strong>' + counts[key] + '</strong>' +
    '<span>' + label + '</span><small>' + (counts[key] ? '已有可引用证据' : '仍需补采') + '</small></div>').join('');
  return '<div class="report-coverage-grid">' + cards + '</div><p class="report-coverage-note">' +
    '当前共有 ' + model.evidence.length + ' 条可引用证据、' + model.gaps.length + ' 个待补缺口。搜索摘要和未读取页面不计入本看板。</p>';
}

function reportRiskOpportunityHTML(model) {
  if (model.sections.risk_opportunity) return reportParagraphs(model.sections.risk_opportunity);
  const ranked = Object.entries(model.scores || {}).sort((a, b) => Number(b[1].overall || 0) - Number(a[1].overall || 0));
  const risks = ranked.slice(0, 3).map(([name, score]) => '<li><strong>' + escapeHTML(name) + '</strong>：综合威胁 ' + Number(score.overall || 0) +
    '，重点关注' + escapeHTML(strongestThreatDimension(score).label) + '。</li>').join('');
  const signals = model.actions.filter(item => item.monitoring_signal).slice(0, 3).map(item =>
    '<li><strong>' + escapeHTML(item.competitor || '综合') + '</strong>：' + escapeHTML(zhText(item.monitoring_signal)) + '</li>').join('');
  return '<div class="report-risk-grid"><div><h3>优先风险</h3><ul>' + (risks || '<li>暂无可排序风险。</li>') + '</ul></div>' +
    '<div><h3>观察信号</h3><ul>' + (signals || '<li>尚未形成明确监测信号。</li>') + '</ul></div></div>';
}

function reportMethodologyHTML(model) {
  const methodGuide = '<div class="report-method-guide">' +
    '<div><h3>VRIO：判断优势能否持续</h3><p><strong>V（价值性）</strong>检查能力是否创造用户价值或降低成本；' +
    '<strong>R（稀缺性）</strong>检查同类竞品是否普遍具备；<strong>I（难模仿性）</strong>检查技术、数据、品牌、渠道或供应链是否难以复制；' +
    '<strong>O（组织承接）</strong>检查竞品能否通过团队、流程和资源持续放大优势。VRIO 主要用于判断能力追赶、渠道壁垒和长期扩张威胁。</p></div>' +
    '<div><h3>SWOT：判断优势如何转化为市场压力</h3><p><strong>S（优势）</strong>和<strong>W（劣势）</strong>描述竞品及我方的内部条件；' +
    '<strong>O（机会）</strong>和<strong>T（威胁）</strong>描述外部市场窗口与风险。本系统把四项连接成“证据—市场行为—用户或渠道影响”的因果链，' +
    '重点判断用户替代、定位重叠、渠道扩张和我方暴露面。</p></div>' +
    '<p class="report-method-note">两种方法读取同一份 O/B/C/L 证据并独立分析。VRIO 侧重优势是否持久，SWOT 侧重优势在当前市场如何形成压力；质检 Agent 根据证据调和分歧，而不是机械平均分数。</p></div>';
  const narrative = model.sections.methodology ? reportParagraphs(model.sections.methodology) :
    '<p>分析依次经过研究范围确认、O/B/C/L 分层证据采集、VRIO 与 SWOT 双方法独立判断、质检调和和质量门复审。质量门依据证据覆盖、矩阵完整性和结构错误决定返回采集、返回分析或进入写作。</p>';
  const metrics = Object.entries(model.quality || {}).map(([key, value]) =>
    '<div><span>' + escapeHTML(fieldLabel(key)) + '</span><strong>' + escapeHTML(valueLabel(value)) + '</strong></div>').join('');
  const history = model.reworkHistory.map((item, index) => '<li>第 ' + (index + 1) + ' 轮：' +
    escapeHTML(zhText(item.reason || item.route || item.decision || '完成质量复审')) + '</li>').join('');
  return methodGuide + narrative + (metrics ? '<div class="report-quality-grid">' + metrics + '</div>' : '') +
    (history ? '<ol class="report-rework-list">' + history + '</ol>' : '<p>本轮未记录额外返工。</p>');
}

function reportThreatMatrixHTML(model) {
  const names = Object.keys(model.scores || {});
  if (!names.length) return '<p>暂无结构化威胁矩阵。</p>';
  const head = THREAT_DIMS.map(d => '<th>' + escapeHTML(d.label) + '</th>').join('');
  const rows = names.map(name => {
    const score = model.scores[name] || {};
    const strength = model.assessment[name]?.evidence_strength || '';
    const dims = THREAT_DIMS.map(d => '<td>' + reportScoreCell(score[d.key], strength) + '</td>').join('');
    return '<tr><td><strong>' + escapeHTML(name) + '</strong></td>' + dims +
      '<td>' + reportScoreCell(score.overall, strength) + '</td><td>' + escapeHTML(zhText(strength || '待确认')) + '</td></tr>';
  }).join('');
  return '<div class="report-table-wrap"><table class="report-table"><thead><tr><th>竞品</th>' + head +
    '<th>综合威胁</th><th>证据充分度</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}

function reportActionsHTML(model) {
  if (!model.actions.length) return reportParagraphs(model.sections.recommendations || '暂无正式行动建议。');
  return model.actions.map((action, index) => {
    const type = valueLabel(action.response_type || 'monitoring');
    const dimension = THREAT_DIMS.find(item => item.key === action.related_threat_dimension)?.label ||
      fieldLabel(action.related_threat_dimension || 'overall');
    const priority = Number(action.priority || 0);
    const confidence = Number(action.confidence || 0);
    return '<div class="report-action"><div class="report-action-title"><span class="report-priority">P' + (index + 1) +
      (priority ? ' · ' + priority : '') + '</span> ' +
      escapeHTML(action.competitor || '综合') + ' · ' + escapeHTML(action.concrete_action || action.action || '待明确行动') +
      '</div><div class="report-action-meta"><span>类型：' + escapeHTML(type) + '</span> · <span>关联维度：' + escapeHTML(dimension) + '</span>' +
      (confidence ? ' · <span>置信度：' + Math.round(confidence * 100) + '%</span>' : '') +
      (action.requires_human_confirmation ? ' · <span class="report-human-check">需要人工确认</span>' : '') +
      (action.evidence_basis ? '<br>证据依据：' + escapeHTML(zhText(action.evidence_basis)) : '') +
      (action.monitoring_signal ? '<br>监控信号：' + escapeHTML(zhText(action.monitoring_signal)) : '') + '</div></div>';
  }).join('');
}

function reportEvidenceHTML(model) {
  if (!model.evidence.length) return '<p>本轮没有通过整理的可引用证据。</p>';
  return model.evidence.map((item, index) => '<div class="report-evidence-item"><div><strong>[' + (index + 1) + '] ' +
    escapeHTML(item.source_label || '未命名来源') + '</strong></div>' +
    (item.quote ? '<p>“' + escapeHTML(zhText(item.quote)) + '”</p>' : '') +
    '<div class="report-evidence-meta">' + (item.source_url ? '<a href="' + escapeHTML(item.source_url) +
      '" target="_blank" rel="noopener noreferrer">' + escapeHTML(item.source_url) + '</a>' : '未提供 URL') +
    (item.relevance ? '<br>支撑关系：' + escapeHTML(zhText(item.relevance)) : '') + '</div></div>').join('');
}

function reportLimitationsHTML(model) {
  const gapItems = model.gaps.map(gap => '<p><strong>' + escapeHTML(gap.competitor || '综合') + '</strong>：' +
    escapeHTML(zhText(gap.recommended_response || gap.rationale || '需要补充证据')) + '</p>').join('');
  const qualityItems = Object.entries(model.quality || {}).map(([key, value]) =>
    '<p>' + escapeHTML(fieldLabel(key)) + '：' + escapeHTML(valueLabel(value)) + '</p>').join('');
  return '<div class="report-limitations">' + (gapItems || '<p>Writer 未报告明确证据缺口。</p>') + qualityItems + '</div>';
}

function renderFullReport(outputs) {
  const content = document.getElementById('full-report-content');
  const toc = document.getElementById('report-toc');
  if (!content || !toc) return;
  const model = buildFullReportModel(outputs);
  if (!model) {
    toc.innerHTML = '';
    content.innerHTML = '<div class="empty-state"><h2>等待完整报告</h2><p>Pipeline 完成后，撰写 Agent 的章节化结论会显示在这里。</p></div>';
    return;
  }
  const sectionOrder = [
    'executive_summary', 'key_findings', 'landscape', 'competitor_profiles',
    'threat_matrix', 'key_debate', 'risk_opportunity', 'threat_assessment',
    'recommendations', 'evidence_coverage', 'evidence', 'methodology', 'limitations',
  ];
  toc.innerHTML = '<div class="report-toc-title">报告目录</div>' +
    '<div class="report-read-progress"><div><span>阅读进度</span><b id="report-read-percent">0%</b></div>' +
    '<i><em id="report-read-bar"></em></i></div>' + sectionOrder.map((key, index) =>
    '<a href="#report-' + key + '">' + (index + 1) + '. ' + REPORT_SECTION_LABELS[key] + '</a>').join('');

  const contentByKey = {
    executive_summary: reportParagraphs(model.sections.executive_summary),
    key_findings: reportKeyFindingsHTML(model),
    landscape: reportParagraphs(model.sections.landscape),
    competitor_profiles: reportCompetitorProfilesHTML(model),
    threat_matrix: reportThreatMatrixHTML(model),
    key_debate: reportParagraphs(model.sections.key_debate || '本轮未记录需要裁决的显著方法分歧。'),
    risk_opportunity: reportRiskOpportunityHTML(model),
    threat_assessment: reportParagraphs(model.sections.threat_assessment),
    recommendations: reportActionsHTML(model),
    evidence_coverage: reportEvidenceCoverageHTML(model),
    evidence: reportEvidenceHTML(model),
    methodology: reportMethodologyHTML(model),
    limitations: reportLimitationsHTML(model),
  };
  content.innerHTML = '<header class="report-cover"><div class="report-kicker">RIVALTRACK · COMPETITIVE INTELLIGENCE</div>' +
    '<h1>' + escapeHTML(model.title) + '</h1><div class="report-meta"><span>我方产品：' +
    escapeHTML(model.target.name || '待确认') + '</span><span>报告日期：' + escapeHTML(model.timestamp.toLocaleString('zh-CN')) +
    '</span><span>综合置信度：' + Math.round(model.confidence * 100) + '%</span></div></header>' +
    reportMetricStripHTML(model) +
    sectionOrder.map(key => '<section class="report-section" id="report-' + key + '"><h2>' +
      REPORT_SECTION_LABELS[key] + (model.sections[key]
        ? '<button class="report-revise-btn btn-secondary" type="button" data-report-section="' + key + '">高亮 / 批注 / AI 修改</button>'
        : '') + '</h2>' + contentByKey[key] + '</section>').join('');
  applyReportAnnotations(outputs);
  setupReportReadingProgress();
}

function markReportQuote(section, quote, annotation) {
  if (!quote) return false;
  const walker = document.createTreeWalker(section, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    const index = node.nodeValue.indexOf(quote);
    if (index >= 0) {
      const range = document.createRange();
      range.setStart(node, index);
      range.setEnd(node, index + quote.length);
      const mark = document.createElement('mark');
      mark.className = 'report-annotation-mark' + (annotation.intent === 'comment_only' ? ' has-comment' : '');
      mark.dataset.annotationId = annotation.annotation_id || '';
      if (annotation.comment) mark.title = annotation.comment;
      range.surroundContents(mark);
      return true;
    }
    node = walker.nextNode();
  }
  return false;
}

function applyReportAnnotations(outputs) {
  const writer = (outputs || []).find(item => item.role === 'writer' || item.node_id === 'writer');
  (writer?.report_annotations || []).forEach(annotation => {
    const section = document.getElementById('report-' + annotation.section_id);
    if (!section) return;
    markReportQuote(section, String(annotation.quote || ''), annotation);
    if (annotation.intent === 'comment_only' && annotation.comment) {
      const note = document.createElement('div');
      note.className = 'report-annotation-note';
      note.dataset.annotationId = annotation.annotation_id || '';
      note.innerHTML = '<strong>批注</strong>：' + escapeHTML(annotation.comment);
      section.appendChild(note);
    }
  });
}

function saveReportAnnotationLocally(annotation) {
  const writer = agentOutputs.find(item => item.role === 'writer' || item.node_id === 'writer');
  if (!writer) return;
  writer.report_annotations = Array.isArray(writer.report_annotations) ? writer.report_annotations : [];
  writer.report_annotations.push(annotation);
  saveToLocalStorage();
  renderFullReport(agentOutputs);
}

function setupReportReadingProgress() {
  const scroller = document.querySelector('#view-report .report-view-scroll');
  const sections = [...document.querySelectorAll('#full-report-content .report-section')];
  const links = [...document.querySelectorAll('#report-toc a')];
  if (!scroller) return;
  scroller.onscroll = () => {
    const max = scroller.scrollHeight - scroller.clientHeight;
    const percent = max > 0 ? Math.min(100, Math.round(scroller.scrollTop / max * 100)) : 0;
    const percentNode = document.getElementById('report-read-percent');
    const barNode = document.getElementById('report-read-bar');
    if (percentNode) percentNode.textContent = percent + '%';
    if (barNode) barNode.style.width = percent + '%';
    let activeId = '';
    sections.forEach(section => {
      if (section.getBoundingClientRect().top <= 150) activeId = section.id;
    });
    links.forEach(link => link.classList.toggle('active', link.getAttribute('href') === '#' + activeId));
  };
  scroller.onscroll();
}

function buildFullReportMarkdown(outputs) {
  const model = buildFullReportModel(outputs);
  if (!model) return '';
  const lines = ['# ' + model.title, '',
    '- 我方产品：' + (model.target.name || '待确认'),
    '- 报告日期：' + model.timestamp.toLocaleString('zh-CN'),
    '- 综合置信度：' + Math.round(model.confidence * 100) + '%', '',
    '## 执行摘要', '', model.sections.executive_summary || '', '',
    '## 关键结论速览', '', model.sections.key_findings || '',
  ];
  Object.entries(model.scores).sort((a, b) => Number(b[1].overall || 0) - Number(a[1].overall || 0)).slice(0, 4)
    .forEach(([name, score]) => lines.push('- **' + name + '**：综合威胁 ' + Number(score.overall || 0) +
      '，最突出维度为' + strongestThreatDimension(score).label + '。'));
  lines.push('',
    '## 竞争格局', '', model.sections.landscape || '', '',
    '## 逐竞品档案', '', model.sections.competitor_profiles || '', '');
  Object.entries(model.scores).forEach(([name, score]) => lines.push('- **' + name + '**：综合威胁 ' +
    Number(score.overall || 0) + '；证据充分度：' + (model.assessment[name]?.evidence_strength || '待确认') + '。'));
  lines.push('',
    '## 逐竞品威胁矩阵', '',
    '| 竞品 | 用户替代 | 能力追赶 | 分发渠道 | 战略扩张 | 综合威胁 | 证据充分度 |',
    '| --- | ---: | ---: | ---: | ---: | ---: | --- |');
  Object.entries(model.scores).forEach(([name, score]) => {
    const strength = model.assessment[name]?.evidence_strength || '待确认';
    const display = value => /无证据/.test(strength) ? '待评估' : String(Number(value || 0));
    lines.push('| ' + name + ' | ' + display(score.user_substitution) + ' | ' + display(score.capability_catch_up) +
      ' | ' + display(score.distribution) + ' | ' + display(score.strategic_expansion) + ' | ' +
      display(score.overall) + ' | ' + strength + ' |');
  });
  lines.push('', '## 方法分歧与质检裁决', '', model.sections.key_debate || '无显著分歧。', '',
    '## 风险、机会与观察信号', '', model.sections.risk_opportunity || '详见威胁矩阵与行动建议。', '',
    '## 威胁判断与我方防御', '', model.sections.threat_assessment || '', '', '## 优先行动建议', '');
  model.actions.forEach((action, index) => {
    lines.push((index + 1) + '. **' + (action.competitor || '综合') + '**：' +
      (action.concrete_action || action.action || '待明确行动'));
    if (action.evidence_basis) lines.push('   - 证据依据：' + zhText(action.evidence_basis));
    if (action.monitoring_signal) lines.push('   - 监控信号：' + zhText(action.monitoring_signal));
  });
  const coverage = { official: 0, benchmark: 0, community: 0, leading: 0 };
  model.evidence.forEach(item => { coverage[evidencePortfolioType(item)] += 1; });
  lines.push('', '## 证据覆盖看板', '',
    '- 官方来源（O）：' + coverage.official + ' 条',
    '- 权威媒体/基准（B）：' + coverage.benchmark + ' 条',
    '- 社区口碑（C）：' + coverage.community + ' 条',
    '- 前瞻信号（L）：' + coverage.leading + ' 条', '', '## 证据索引', '');
  model.evidence.forEach((item, index) => lines.push('[' + (index + 1) + '] ' +
    (item.source_label || '未命名来源') + ' — ' + (item.source_url || '未提供 URL') +
    (item.quote ? '\n> ' + zhText(item.quote) : '')));
  lines.push('', '## 方法与质量审计', '', model.sections.methodology ||
    '分析先确认研究范围并采集 O/B/C/L 分层证据。VRIO 从价值性、稀缺性、难模仿性和组织承接能力判断竞品优势能否持续；SWOT 从优势、劣势、机会和威胁判断竞品优势如何转化为用户替代、渠道扩张和我方暴露风险。两条分析链独立工作，质检 Agent 根据证据调和分歧，质量门决定返工或进入写作。', '');
  Object.entries(model.quality || {}).forEach(([key, value]) => lines.push('- ' + fieldLabel(key) + '：' + valueLabel(value)));
  lines.push('', '## 证据限制与待补项', '');
  model.gaps.forEach(gap => lines.push('- **' + (gap.competitor || '综合') + '**：' +
    zhText(gap.recommended_response || gap.rationale || '需要补充证据')));
  return lines.join('\n');
}

function csvEscape(value) {
  let text = String(value ?? '');
  if (/^[=+\-@]/.test(text)) text = "'" + text;
  return '"' + text.replace(/"/g, '""') + '"';
}

function rowsToCSV(rows) {
  return '\uFEFF' + rows.map(row => row.map(csvEscape).join(',')).join('\r\n');
}

function buildThreatMatrixCSV(outputs) {
  const model = buildFullReportModel(outputs);
  const rows = [['竞品', '用户替代威胁', '能力追赶威胁', '分发渠道威胁', '战略扩张威胁', '综合威胁', '证据充分度']];
  Object.entries(model?.scores || {}).forEach(([name, score]) => rows.push([
    name, score.user_substitution, score.capability_catch_up, score.distribution,
    score.strategic_expansion, score.overall, model.assessment[name]?.evidence_strength || '待确认',
  ]));
  return rowsToCSV(rows);
}

function buildEvidenceCSV(outputs) {
  const model = buildFullReportModel(outputs);
  const rows = [['证据编号', '来源名称', '来源地址', '摘录', '相关性', '来源层级']];
  (model?.evidence || []).forEach(item => rows.push([
    item.evidence_id, item.source_label, item.source_url, item.quote, item.relevance, zhText(item.source_tier),
  ]));
  return rowsToCSV(rows);
}

function buildActionsCSV(outputs) {
  const model = buildFullReportModel(outputs);
  const rows = [['优先级', '竞品', '行动类型', '关联维度', '具体行动', '证据依据', '监测信号']];
  (model?.actions || []).forEach((item, index) => rows.push([
    item.priority || index + 1, item.competitor, zhText(item.response_type), zhText(item.threat_dimension),
    item.concrete_action || item.action, item.evidence_basis, item.monitoring_signal,
  ]));
  return rowsToCSV(rows);
}

function downloadCSV(content, filename) {
  const url = URL.createObjectURL(new Blob([content], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a'); link.href = url; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove(); URL.revokeObjectURL(url);
}

function openRevisionModal(sectionId) {
  const selection = window.getSelection()?.toString().trim() || '';
  document.getElementById('revision-section-id').value = sectionId;
  document.getElementById('revision-quote').value = selection;
  document.getElementById('revision-comment').value = '';
  document.getElementById('revision-intent').value = 'comment_only';
  syncRevisionIntentUI();
  document.getElementById('revision-result').innerHTML = '';
  document.getElementById('revision-modal-overlay').classList.add('show');
}

document.getElementById('full-report-content')?.addEventListener('click', event => {
  const button = event.target.closest('[data-report-section]');
  if (button) openRevisionModal(button.dataset.reportSection);
});

document.getElementById('revision-modal-close')?.addEventListener('click', () =>
  document.getElementById('revision-modal-overlay').classList.remove('show'));
document.getElementById('btn-cancel-revision')?.addEventListener('click', () =>
  document.getElementById('revision-modal-overlay').classList.remove('show'));

function syncRevisionIntentUI() {
  const intent = document.getElementById('revision-intent').value;
  const commentGroup = document.getElementById('revision-comment-group');
  const commentLabel = document.getElementById('revision-comment-label');
  const button = document.getElementById('btn-request-revision');
  commentGroup.hidden = intent === 'highlight_only';
  commentLabel.textContent = intent === 'comment_only' ? '批注内容' : '具体修改要求';
  button.textContent = intent === 'highlight_only' ? '保存高亮' : intent === 'comment_only' ? '保存批注' : '生成 AI 建议稿';
}
document.getElementById('revision-intent')?.addEventListener('change', syncRevisionIntentUI);

document.getElementById('btn-request-revision')?.addEventListener('click', async () => {
  const result = document.getElementById('revision-result');
  const intent = document.getElementById('revision-intent').value;
  const quote = document.getElementById('revision-quote').value.trim();
  const comment = document.getElementById('revision-comment').value.trim();
  if (intent === 'highlight_only' && !quote) { result.textContent = '请先在报告中选中要高亮的文字。'; return; }
  if (intent === 'comment_only' && !comment) { result.textContent = '请填写批注内容。'; return; }
  if (!activeAnalysisId && ['highlight_only', 'comment_only'].includes(intent)) {
    saveReportAnnotationLocally({
      annotation_id: 'local_' + Date.now(),
      section_id: document.getElementById('revision-section-id').value,
      quote, comment, intent,
    });
    document.getElementById('revision-modal-overlay').classList.remove('show');
    return;
  }
  if (!activeAnalysisId) { result.textContent = 'AI 修改需要服务端报告编号，请重新运行一次分析。'; return; }
  result.textContent = intent === 'highlight_only' ? '正在保存高亮…' : intent === 'comment_only' ? '正在保存批注…' : '正在生成建议稿…';
  const response = await fetch('/api/report/revise', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      report_id: activeAnalysisId,
      section_id: document.getElementById('revision-section-id').value,
      quote, intent, comment,
    }),
  });
  if (!response.ok) { result.textContent = await response.text(); return; }
  const revision = await response.json();
  if (revision.kind === 'annotation') {
    saveReportAnnotationLocally(revision.annotation);
    document.getElementById('revision-modal-overlay').classList.remove('show');
    return;
  }
  result.innerHTML = '<div class="revision-diff"><h4>修改差异</h4>' + revision.diff.map(row =>
    '<div class="diff-' + row.kind + '">' + escapeHTML((row.kind === 'add' ? '+ ' : row.kind === 'remove' ? '- ' : '  ') + row.text) + '</div>'
  ).join('') + '</div><div class="revision-actions"><button type="button" data-revision-decision="accepted">接受建议稿</button>' +
    '<button type="button" data-revision-decision="rejected">拒绝</button></div>';
  result.dataset.revision = JSON.stringify(revision);
});

document.getElementById('revision-result')?.addEventListener('click', async event => {
  const button = event.target.closest('[data-revision-decision]');
  if (!button) return;
  const result = event.currentTarget;
  const revision = JSON.parse(result.dataset.revision || '{}');
  const decision = button.dataset.revisionDecision;
  const response = await fetch('/api/report/revision-decision', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ report_id: activeAnalysisId, revision_id: revision.revision_id, decision }),
  });
  if (!response.ok) { result.textContent = await response.text(); return; }
  if (decision === 'accepted') {
    const writer = agentOutputs.find(item => item.role === 'writer' || item.node_id === 'writer');
    if (writer?.report_sections) writer.report_sections[revision.section_id] = revision.proposed_text;
    renderFullReport(agentOutputs); saveToLocalStorage();
  }
  document.getElementById('revision-modal-overlay').classList.remove('show');
});

function downloadFullReportMarkdown() {
  const markdown = buildFullReportMarkdown(agentOutputs);
  if (!markdown) {
    showNotice('暂无完整报告', '请等待撰写 Agent 完成后再导出。');
    return;
  }
  const model = buildFullReportModel(agentOutputs);
  const filename = String(model?.title || '竞品分析报告').replace(/[\\/:*?"<>|]/g, '_') + '.md';
  const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

document.getElementById('btn-export-report')?.addEventListener('click', downloadFullReportMarkdown);
document.getElementById('btn-export-threat-csv')?.addEventListener('click', () =>
  downloadCSV(buildThreatMatrixCSV(agentOutputs), '威胁矩阵.csv'));
document.getElementById('btn-export-evidence-csv')?.addEventListener('click', () =>
  downloadCSV(buildEvidenceCSV(agentOutputs), '证据表.csv'));
document.getElementById('btn-export-actions-csv')?.addEventListener('click', () =>
  downloadCSV(buildActionsCSV(agentOutputs), '行动清单.csv'));
document.getElementById('btn-open-evidence-workspace')?.addEventListener('click', async () => {
  const overlay = document.getElementById('evidence-workspace-overlay');
  const list = document.getElementById('evidence-workspace-list');
  const metricsBox = document.getElementById('human-quality-metrics');
  overlay.classList.add('show'); list.textContent = '正在读取证据工作区…';
  const [evidenceResponse, metricsResponse] = await Promise.all([
    fetch('/api/evidence'), fetch('/api/quality/human-metrics'),
  ]);
  if (!evidenceResponse.ok || !metricsResponse.ok) { list.textContent = '证据工作区读取失败。'; return; }
  const evidence = await evidenceResponse.json();
  const metrics = await metricsResponse.json();
  metricsBox.innerHTML = '<div class="workspace-metrics">' + Object.entries(metrics).map(([key, value]) =>
    '<span><strong>' + escapeHTML(key) + '</strong> ' + escapeHTML(value) + '</span>').join('') + '</div>';
  list.innerHTML = (evidence.items || []).map(item => '<article class="workspace-evidence" data-evidence-id="' +
    escapeHTML(item.evidence_id) + '"><strong>' + escapeHTML(item.source_label || '未命名来源') + '</strong>' +
    '<div>' + escapeHTML(item.quote || '') + '</div><a href="' + safeURL(item.source_url) + '" target="_blank" rel="noopener noreferrer">查看原始页面</a>' +
    '<div class="revision-actions"><button type="button" data-evidence-review="accepted">认可</button>' +
    '<button type="button" data-evidence-review="rejected">驳回</button><span>当前：' + escapeHTML(zhText(item.human_status)) +
    '</span></div></article>').join('') || '<div class="empty-state">尚无跨报告证据。</div>';
});
document.getElementById('evidence-workspace-close')?.addEventListener('click', () =>
  document.getElementById('evidence-workspace-overlay').classList.remove('show'));
document.getElementById('evidence-workspace-list')?.addEventListener('click', async event => {
  const button = event.target.closest('[data-evidence-review]');
  if (!button) return;
  const card = button.closest('[data-evidence-id]');
  const response = await fetch('/api/evidence/' + encodeURIComponent(card.dataset.evidenceId) + '/review', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status: button.dataset.evidenceReview }),
  });
  if (response.ok) card.querySelector('.revision-actions span').textContent = '当前：' +
    (button.dataset.evidenceReview === 'accepted' ? '已认可' : '已驳回');
});
document.getElementById('btn-print-report')?.addEventListener('click', () => window.print());

function refreshHistoryView() {
  const container = document.getElementById('history-list');
  try {
    const history = loadHistoryRecords();
    if (history.length === 0) {
      container.innerHTML = '<div class="hi-empty">暂无历史报告。运行一次完整的 Pipeline 后将在此显示记录。</div>';
      return;
    }
    container.innerHTML = history.map((h, i) => {
      const d = new Date(h.date);
      const dateStr = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0') +
        ' ' + String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0');
      return '<div class="history-item" data-idx="' + i + '">' +
        '<div class="hi-main"><div class="hi-title">' + escapeHTML(h.track) + ' · ' + h.completedCount + '/' + h.agentCount + ' Agent 完成</div>' +
        '<div class="hi-meta">' + h.outputs.map(o => escapeHTML(o.label || o.role)).join(' → ') + '</div></div>' +
        '<div class="hi-actions"><div class="hi-date">' + dateStr + '</div>' +
        '<button class="hi-delete" type="button" data-idx="' + i + '" aria-label="删除历史记录">删除</button></div></div>';
    }).join('');
    container.querySelectorAll('.hi-delete').forEach(btn => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const idx = parseInt(btn.dataset.idx);
        const current = loadHistoryRecords();
        if (!Number.isInteger(idx) || !current[idx]) return;
        current.splice(idx, 1);
        try { localStorage.setItem(HISTORY_LS_KEY, JSON.stringify(current)); } catch (e) { console.warn('clearHistoryItem failed:', e); }
        refreshHistoryView();
      });
    });
    container.querySelectorAll('.history-item').forEach(item => {
      item.addEventListener('click', () => {
        const idx = parseInt(item.dataset.idx);
        const history = loadHistoryRecords();
        if (history[idx] && history[idx].outputs) {
          applyOutputs(history[idx].outputs);
          document.querySelectorAll('.topnav-links button').forEach(b => b.classList.remove('active'));
          document.querySelector('button[data-view="dashboard"]').classList.add('active');
          document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
          document.getElementById('view-dashboard').classList.add('active');
          currentView = 'dashboard';
        }
      });
    });
  } catch (e) {
    container.innerHTML = '<div class="hi-empty">无法加载历史记录。</div>';
  }
}

// ══════════════════════════════════════════════════════════════════
// 对比视图——竞品卡片
// ══════════════════════════════════════════════════════════════════
function refreshCompareView() {
  const grid = document.getElementById('compare-grid');
  const chips = document.getElementById('compare-chips');
  const compMap = new Map();

  collectCompetitors(agentOutputs).forEach(([name, evidence]) => {
    const normalizedName = matchCompetitor(name) || name;
    if (!normalizedName || SOURCE_PAGE_KEYWORDS.test(normalizedName)) return;
    const sources = new Set((evidence || []).map(e => e.source_url).filter(Boolean));
    compMap.set(normalizedName, {
      name: normalizedName,
      evidence: evidence || [],
      sources,
      score: hasNoScoreableEvidence(normalizedName, agentOutputs)
        ? null
        : findThreatScoresFor(normalizedName, agentOutputs),
    });
  });

  if (compMap.size === 0) {
    grid.innerHTML = '<div class="cc-empty">\u7b49\u5f85 Pipeline \u6570\u636e&hellip;<br><small style="font-size:14px;">Agent \u8f93\u51fa\u5230\u8fbe\u540e\u81ea\u52a8\u586b\u5145\u7ade\u54c1\u4e3b\u4f53\u5bf9\u6bd4\u3002</small></div>';
    chips.innerHTML = '';
    return;
  }

  chips.innerHTML = [...compMap.keys()].map(n => '<span class="agent-chip">' + escapeHTML(n) + '</span>').join('');
  const threatColors = { high: 'cc-threat-high', medium: 'cc-threat-medium', low: 'cc-threat-low' };
  grid.innerHTML = [...compMap.entries()].map(([name, data]) => {
    const overall = data.score?.overall ?? null;
    const threat = overall === null ? 'unknown' : scoreLevel(overall);
    const threatCls = threatColors[threat] || '';
    const latestSource = data.evidence[data.evidence.length - 1]?.source_label || '\u2014';
    return '<div class="compare-card">' +
      '<h3>' + escapeHTML(name) + '</h3>' +
      '<div class="cc-subtitle">' + data.sources.size + ' \u4e2a\u6570\u636e\u6e90 \u00b7 ' + data.evidence.length + ' \u6761\u8bc1\u636e</div>' +
      '<div class="cc-stat"><span class="cc-stat-label">\u5a01\u80c1\u7b49\u7ea7</span><span class="cc-stat-value ' + threatCls + '">' + escapeHTML(overall === null ? '\u5f85\u5224\u65ad' : scoreLabel(overall)) + '</span></div>' +
      '<div class="cc-stat"><span class="cc-stat-label">\u6570\u636e\u6e90\u6570</span><span class="cc-stat-value">' + data.sources.size + '</span></div>' +
      '<div class="cc-stat"><span class="cc-stat-label">\u8bc1\u636e\u6761\u76ee</span><span class="cc-stat-value">' + data.evidence.length + '</span></div>' +
      '<div class="cc-stat"><span class="cc-stat-label">\u6700\u8fd1\u6765\u6e90</span><span class="cc-stat-value" style="font-size:14px;">' + escapeHTML(zhText(latestSource)) + '</span></div>' +
      '</div>';
  }).join('');
}

// ══════════════════════════════════════════════════════════════════
// 设置操作
// ══════════════════════════════════════════════════════════════════
function clearLocalCache() {
  localStorage.removeItem(LS_KEY);
  localStorage.removeItem(HISTORY_LS_KEY);
  alert('本地缓存已清除。刷新页面后将重新加载。');
}
function fitGraph() {
  switchView('dashboard');
  cy.resize();
  cy.fit(undefined, 45);
}
function zoomGraphIn() {
  switchView('dashboard');
  cy.zoom({ level: Math.min(cy.zoom() * 1.18, cy.maxZoom()), renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
}
function zoomGraphOut() {
  switchView('dashboard');
  cy.zoom({ level: Math.max(cy.zoom() / 1.18, cy.minZoom()), renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
}
document.getElementById('graph-fit').addEventListener('click', fitGraph);
document.getElementById('graph-zoom-in').addEventListener('click', zoomGraphIn);
document.getElementById('graph-zoom-out').addEventListener('click', zoomGraphOut);
document.getElementById('sidebar-load-history').addEventListener('click', () => {
  if (restoreLatestHistory()) {
    switchView('dashboard');
    showNotice('已显示历史结果', '已恢复最近一次分析结果。');
  } else {
    showNotice('暂无历史结果', '当前浏览器还没有保存过历史分析。');
  }
});
document.getElementById('sidebar-load-demo').addEventListener('click', () => {
  showSampleSelector();
});
document.getElementById('sidebar-clear-cache').addEventListener('click', clearLocalCache);

// ══════════════════════════════════════════════════════════════════
// WebSocket 客户端
// ══════════════════════════════════════════════════════════════════
let ws = null, reconnectTimer = null, reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 10;
function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  console.log('Connecting to WS: ' + WS_URL);
  try { ws = new WebSocket(WS_URL); } catch (e) { scheduleReconnect(); return; }
  ws.onopen = () => {
    console.log('WS connected');
    connected = true; reconnectAttempts = 0;
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
  };
  ws.onmessage = (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch (e) { console.warn('WS message parse failed:', e); return; }
    if (!['node_update','pipeline_complete','error','heartbeat','full_state'].includes(msg.type)) return;
    handleMessage(msg);
  };
  ws.onclose = () => { connected = false; scheduleReconnect(); };
  ws.onerror = () => {};
}
function scheduleReconnect() {
  if (paused) return;
  if (reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
    document.getElementById('fallback-banner').classList.add('show');
    return;
  }
  const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);
  reconnectAttempts++;
  reconnectTimer = setTimeout(connectWebSocket, delay);
}
function handleMessage(msg) {
  switch (msg.type) {
    case 'node_update': {
      const output = msg.payload;
      if (!output || !output.node_id) return;
      if (!analysisStartNotified && output.status === 'running') {
        analysisStartNotified = true;
        analysisCompleteNotified = false;
        showNotice('分析已开始', roleLabel(output) + ' 已启动，Agent 流水线正在实时更新。');
      }
      const idx = agentOutputs.findIndex(o => (o.node_id || o.role) === output.node_id);
      if (idx >= 0) agentOutputs[idx] = output; else agentOutputs.push(output);
      updateTimelineEntry(output);
      updateStatusBar(agentOutputs);
      saveToLocalStorage();
      buildLandscapeMap(agentOutputs);
      refreshDataBoundViews(agentOutputs);
      break;
    }
    case 'full_state': {
      const outputs = Array.isArray(msg.payload) ? msg.payload : [];
      if (outputs.length === 0) {
        clearDashboardToEmpty();
        break;
      }
      agentOutputs = outputs;
      document.getElementById('timeline').innerHTML = '';
      outputs.forEach(o => {
        addTimelineEntry(o);
      });
      updateStatusBar(outputs);
      saveToLocalStorage();
      buildLandscapeMap(outputs);
      refreshDataBoundViews(outputs);
      break;
    }
    case 'pipeline_complete': {
      const outputs = Array.isArray(msg.payload) ? msg.payload : [];
      if (outputs.length === 0) {
        clearDashboardToEmpty();
        break;
      }
      pipelineComplete = true;
      agentOutputs = outputs;
      document.getElementById('timeline').innerHTML = '';
      outputs.forEach(o => {
        updateTimelineEntry(o);
      });
      updateStatusBar(outputs);
      saveToLocalStorage();
      const aA = outputs.find(o => o.role === 'analyst-a' || o.node_id === 'analyst-a');
      const aB = outputs.find(o => o.role === 'analyst-b' || o.node_id === 'analyst-b');
      const qa = outputs.find(o => o.role === 'qa' || o.node_id === 'qa');
      if (aA && aB && (aA.output_summary !== aB.output_summary || aA.confidence !== aB.confidence)) {
        showDebateCallout(aA, aB, qa);
      }
      buildLandscapeMap(outputs);
      autoExpandAllCards();
      savePipelineHistory(outputs);
      refreshDataBoundViews(outputs);
      if (!analysisCompleteNotified) {
        analysisCompleteNotified = true;
        hidePauseButton();
        const finalSec = Math.floor((Date.now() - startTime) / 1000);
        stopElapsed(finalSec);
        const writer = outputs.find(o => o.role === 'writer' || o.node_id === 'writer');
        const completedCount = outputs.filter(o => o.status === 'completed').length;
        let noticeBody = '本轮共完成 <strong>' + completedCount + '</strong> / ' + outputs.length + ' 个 Agent。<br><br>';
        if (writer && writer.status === 'completed' && writer.output_summary) {
          noticeBody += '<strong>竞品分析总结:</strong> ' + escapeHTML(writer.output_summary.slice(0, 500));
        } else {
          noticeBody += '已更新：对我方威胁矩阵、竞争知识图谱和应对行动清单。';
        }
        showNotice('分析已完成', noticeBody);
      }
      break;
    }
    case 'error': {
      const err = msg.payload || {};
      if (err.node_id) {
        const errorOutput = { node_id: err.node_id, role: err.node_id, status: 'error', label: err.node_id, output_summary: err.message || 'Unknown error', evidence: [], dependencies: [], disagreements: [] };
        const existingIdx = agentOutputs.findIndex(o => (o.node_id || o.role) === err.node_id);
        if (existingIdx >= 0) {
          agentOutputs[existingIdx] = { ...agentOutputs[existingIdx], ...errorOutput };
        } else {
          agentOutputs.push(errorOutput);
        }
        updateTimelineEntry(errorOutput);
        updateStatusBar(agentOutputs);
        if (err.node_id === 'pipeline') {
          pipelineComplete = true;
          analysisCompleteNotified = true;
          const titleEl = document.getElementById('exec-title');
          const bodyEl = document.getElementById('exec-body');
          if (titleEl) titleEl.textContent = '分析失败';
          if (bodyEl) {
            bodyEl.classList.remove('is-running');
            bodyEl.classList.add('is-empty');
            bodyEl.textContent = err.message || '分析失败，请查看 Agent 活动日志中的错误信息。';
          }
        }
      }
      break;
    }
  }
}

// ══════════════════════════════════════════════════════════════════
// Cytoscape 点击事件处理
// ══════════════════════════════════════════════════════════════════
let clickTimer = null;
cy.on('click', 'edge.conflict-edge', (evt) => {
  const edge = evt.target;
  const aA = getOutputByRole(agentOutputs, 'analyst-a');
  const aB = getOutputByRole(agentOutputs, 'analyst-b');
  const qa = getOutputByRole(agentOutputs, 'qa');
  if (aA && aB) showDebateCallout(aA, aB, qa, edge.data('summary'));
});

cy.on('click', 'node', (evt) => {
  if (Date.now() - lastClickTime < DEBOUNCE_MS) return;
  const node = evt.target;

  // 竞品或语义节点：展示所有 Agent 输出中的证据
  if (node.hasClass('competitor') || node.hasClass('semantic')) {
    const compName = node.data('compName');
    if (!compName) return;
    const evidence = [];
    agentOutputs.forEach(o => {
      if (o.evidence && Array.isArray(o.evidence)) {
        o.evidence.forEach(e => {
          if ((matchCompetitorFromNames(e.source_label || '', [compName]) || matchCompetitor(e.source_label || '')) === compName) {
            evidence.push(e);
          }
        });
      }
    });
    if (clickTimer) clearTimeout(clickTimer);
    clickTimer = setTimeout(() => {
      lastClickTime = Date.now();
      showCompetitorModal(compName, evidence, node.data('threat'), node.data('disputed'), node.data('noEvidence'));
      clickTimer = null;
    }, DEBOUNCE_MS);
    return;
  }

  // 赛道中心节点：展示全部证据概览
  if (node.hasClass('track-center')) {
    if (clickTimer) clearTimeout(clickTimer);
    clickTimer = setTimeout(() => {
      lastClickTime = Date.now();
      const allEvidence = [];
      agentOutputs.forEach(o => {
        if (o.evidence && Array.isArray(o.evidence)) allEvidence.push(...o.evidence);
      });
      showCompetitorModal(node.data('label'), allEvidence, null, false);
      clickTimer = null;
    }, DEBOUNCE_MS);
    return;
  }
});
cy.on('dblclick', 'node', () => {
  if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
  cy.fit(undefined, 50);
});

function showCompetitorModal(compName, evidenceList, threat, disputed, noEvidence) {
  const overlay = document.getElementById('modal-overlay');
  const title = document.getElementById('modal-title');
  const body = document.getElementById('modal-body');
  const threatLabel = { high: '⚠ 高威胁', medium: '● 中等威胁', low: '○ 低威胁' };
  title.textContent = compName + ' — 证据详情';
  let html = '';
  if (threat) {
    html += '<div style="display:flex;gap:16px;margin-bottom:16px;flex-wrap:wrap;">' +
      '<span style="font-size:14px;padding:4px 12px;border-radius:99px;background:' + (threat === 'high' ? '#fef2f2' : threat === 'medium' ? '#fffbeb' : '#f0fdf4') + ';color:' + (threat === 'high' ? '#dc2626' : threat === 'medium' ? '#d97706' : '#16a34a') + ';font-weight:600;">' + (threatLabel[threat] || threat) + '</span>';
    if (disputed) html += '<span style="font-size:14px;padding:4px 12px;border-radius:99px;background:#fef2f2;color:#dc2626;font-weight:600;">⚡ 分析师分歧</span>';
    if (noEvidence) html += '<span style="font-size:14px;padding:4px 12px;border-radius:99px;background:#fffbeb;color:#b45309;font-weight:600;" title="LLM 判定为高威胁，但前端未匹配到相关证据——可能是竞品名称格式与证据标签不一致">⚠ 证据未匹配</span>';
    html += '</div>';
  }
  html += '<p style="font-size:15px;color:var(--text-secondary);margin-bottom:20px;line-height:1.7;">共 <strong>' + evidenceList.length + '</strong> 条相关证据</p>';
  if (evidenceList.length > 0) {
    evidenceList.forEach((e, i) => {
      html += '<div class="ev-item"><strong>证据 ' + (i + 1) + ': </strong>' + escapeHTML(e.source_label || '') + '<br>' +
        '<a href="' + safeURL(e.source_url) + '" target="_blank" rel="noopener noreferrer">' + escapeHTML(evidenceLinkLabel(e.source_url)) + ': ' + escapeHTML(e.source_url || '') + '</a>' +
        '<div class="quote">' + escapeHTML(e.quote || '') + '</div>' +
        '<div style="font-size:12px;color:var(--text-muted);margin-top:6px;">' + escapeHTML(e.relevance || '') + '</div></div>';
    });
  } else {
    html += '<p style="font-size:15px;color:var(--text-muted);">无详细证据记录</p>';
  }
  body.innerHTML = html;
  overlay.classList.add('show');
}

// ══════════════════════════════════════════════════════════════════
// 演示脚本
// ══════════════════════════════════════════════════════════════════
function startDemoScript() {
  const staggerSetting = document.getElementById('setting-stagger-anim');
  if (!staggerSetting || staggerSetting.value === 'on') applyStaggerAnimation();
  startTime = Date.now();
  if (elapsedInterval) clearInterval(elapsedInterval);
  elapsedInterval = setInterval(updateElapsed, 1000);
}

// ══════════════════════════════════════════════════════════════════
// A3 仪表盘渲染器
// ══════════════════════════════════════════════════════════════════
const THREAT_DIMS = [
  { key: 'user_substitution', label: '用户替代', tip: '竞品是否让目标用户直接转向替代方案' },
  { key: 'capability_catch_up', label: '能力追赶', tip: '竞品是否正在补齐我方核心能力差距' },
  { key: 'distribution', label: '分发渠道', tip: '竞品是否拥有更强渠道、流量、销售或生态入口' },
  { key: 'strategic_expansion', label: '战略扩张', tip: '竞品是否可能进入我方核心市场' },
];
const A3_DIMS = THREAT_DIMS.map(d => d.label).concat(['综合威胁']);
const A3_COMPETITOR_COLORS = ['#4f46e5', '#d97706', '#0f766e', '#dc2626', '#7c3aed', '#059669', '#b45309', '#2563eb'];
let radarChart = null;

function renderRadarLegend(datasets) {
  const legend = document.getElementById('radarLegend');
  if (!legend) return;
  if (!datasets.length) {
    legend.innerHTML = '';
    return;
  }
  legend.innerHTML = datasets.map(dataset => (
    '<span class="chart-legend-item" title="' + escapeHTML(dataset.label) + '">' +
      '<span class="chart-legend-dot" style="background:' + escapeHTML(dataset.borderColor) + '"></span>' +
      '<span class="chart-legend-label">' + escapeHTML(dataset.label) + '</span>' +
    '</span>'
  )).join('');
}

function uniqueNames(names) {
  const out = [];
  (names || []).forEach(name => {
    const cleaned = String(name || '').trim();
    if (cleaned && !out.some(n => n.toLowerCase() === cleaned.toLowerCase())) out.push(cleaned);
  });
  return out;
}

function outputThreatScoreNames(outputs) {
  const names = [];
  const targetName = getOurProductName(outputs || []);
  outputs.forEach(o => {
    const scores = o.threat_scores;
    if (!scores || typeof scores !== 'object') return;
    Object.keys(scores).forEach(key => {
      if (
        scores[key] && typeof scores[key] === 'object'
        && !THREAT_DIMS.some(d => d.key === key)
        && key !== 'overall'
        && !isSelfCompetitorName(key, targetName)
      ) names.push(key);
    });
  });
  return uniqueNames(names);
}

function isSelfCompetitorName(name, targetName) {
  if (!name || !targetName) return false;
  const normalizedName = String(name).toLowerCase().replace(/\s+/g, '');
  const normalizedTarget = String(targetName).toLowerCase().replace(/\s+/g, '');
  return normalizedName === normalizedTarget || normalizedName.includes(normalizedTarget + '(');
}

function viewCompetitorNames(outputs) {
  // 优先级一：用户输入的竞品名称，可靠性最高
  const fromActive = uniqueNames(activeCompetitorNames);
  if (fromActive.length) return fromActive;

  // 优先级二：任一 Agent 输出中的威胁评分键
  const fromScores = outputThreatScoreNames(outputs || []);
  if (fromScores.length) return fromScores;

  // 优先级三：通过已知竞品表，从证据来源标签中提取
  const fromEvidence = [];
  (outputs || []).forEach(o => {
    (o.evidence || []).forEach(e => {
      const matched = matchCompetitorFromNames(e.source_label || '', fromScores) || matchCompetitor(e.source_label || '');
      if (matched) fromEvidence.push(matched);
    });
  });
  if (fromEvidence.length) return uniqueNames(fromEvidence).slice(0, 6);

  // 优先级四：尝试从证据标签提取专有名称
  const rawLabels = [];
  (outputs || []).forEach(o => {
    (o.evidence || []).forEach(e => {
      const label = String(e.source_label || '').trim();
      // 提取疑似公司名：至少两个字符，且不是纯数字或常见词
      if (label && label.length >= 2 && label.length <= 30 && !/^\d+$/.test(label)) {
        rawLabels.push(label.split(/\s*[-:|\\(（]/)[0].trim());
      }
    });
  });
  return uniqueNames(rawLabels).slice(0, 6);
}

function nameHash(name) {
  let h = 0;
  String(name || '').split('').forEach(ch => { h = ((h << 5) - h + ch.charCodeAt(0)) | 0; });
  return Math.abs(h);
}

function extractRecommendationText(outputs) {
  const writer = (outputs || []).find(o => o.role === 'writer' || o.node_id === 'writer');
  if (!writer) return '';
  const sections = writer.report_sections || {};
  return String(sections.recommendations || sections.recommendation || writer.output_summary || '');
}

function collectCompetitors(outputs) {
  const map = new Map();
  const names = viewCompetitorNames(outputs);
  const targetName = getOurProductName(outputs || []);
  names.forEach(name => map.set(name, []));
  const landscapeMap = extractCompetitors(outputs);
  const hasUserNames = names.length > 0;

  // 只收集已存在于竞品图中的实体证据
  // 防止解析证据来源标签时混入非竞品名称
  const allowedNames = new Set(names.map(n => n.toLowerCase()));
  landscapeMap.forEach((entry, name) => {
    if (isSelfCompetitorName(name, targetName)) return;
    if (hasUserNames && !names.some(n => n.toLowerCase() === String(name).toLowerCase())) return;
    if (!map.has(name)) map.set(name, []);
    map.get(name).push(...entry.evidence.map(e => ({ ...e, agentConf: 0.5 })));
  });
  (outputs || []).forEach(o => {
    (o.evidence || []).forEach(e => {
      const label = (e.source_label || '').trim();
      const matched = matchCompetitorFromNames(label, names) || matchCompetitor(label);
      if (!matched) return;
      // 仅在匹配名称属于允许的竞品时加入
      if (!allowedNames.has(matched.toLowerCase())) return;
      if (!map.has(matched)) map.set(matched, []);
      map.get(matched).push({ ...e, agentRole: o.role, agentConf: o.confidence || 0.5 });
    });
  });
  return [...map.entries()];
}


function normalizeThreatScores(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const out = {};
  THREAT_DIMS.forEach(d => {
    const value = Number(raw[d.key]);
    out[d.key] = Number.isFinite(value) ? Math.max(0, Math.min(100, Math.round(value))) : null;
  });
  const present = THREAT_DIMS.map(d => out[d.key]).filter(v => v !== null);
  const overallRaw = Number(raw.overall);
  out.overall = Number.isFinite(overallRaw)
    ? Math.max(0, Math.min(100, Math.round(overallRaw)))
    : (present.length ? Math.round(present.reduce((a, b) => a + b, 0) / present.length) : null);
  return out;
}

function findThreatScoresFor(name, outputs) {
  const competitorCount = viewCompetitorNames(outputs).length;
  for (const output of [...outputs].reverse()) {
    const scores = output.threat_scores;
    if (!scores || typeof scores !== 'object') continue;
    if (scores[name]) return normalizeThreatScores(scores[name]);
    const lowerKey = Object.keys(scores).find(k => k.toLowerCase() === String(name).toLowerCase());
    if (lowerKey && typeof scores[lowerKey] === 'object') return normalizeThreatScores(scores[lowerKey]);
    const flat = normalizeThreatScores(scores);
    if (competitorCount <= 1 && flat && flat.overall !== null) return flat;
  }
  return null;
}

function findThreatAssessmentFor(name, outputs) {
  const target = String(name || '').toLowerCase();
  for (const output of [...(outputs || [])].reverse()) {
    const assessment = output.threat_assessment;
    if (!assessment || typeof assessment !== 'object') continue;
    const key = Object.keys(assessment).find(k => String(k).toLowerCase() === target);
    if (key && assessment[key] && typeof assessment[key] === 'object') return assessment[key];
  }
  return null;
}

function hasNoScoreableEvidence(name, outputs) {
  const strength = findThreatAssessmentFor(name, outputs)?.evidence_strength;
  return /无证据|no evidence/i.test(String(strength || ''));
}



function scoreLevel(score) {
  if (score >= 70) return 'high';
  if (score >= 40) return 'medium';
  return 'low';
}

function scoreLabel(score) {
  const level = scoreLevel(score);
  const text = { high: '高', medium: '中', low: '低' }[level];
  return text + ' ' + score;
}

function getOurProductName(outputs) {
  // 从包含威胁目标的任一 Agent 输出中提取名称
  for (const o of (outputs || [])) {
    const tt = o.threat_target;
    if (tt && typeof tt === 'object' && tt.name) return tt.name;
  }
  // 降级策略：检查本地保存的威胁目标
  return null;
}

function renderExecBanner(outputs) {
  const writer = outputs.find(o => o.role === 'writer' || o.node_id === 'writer');
  const titleEl = document.getElementById('exec-title');
  const bodyEl = document.getElementById('exec-body');
  const metaEl = document.getElementById('exec-meta');
  if (!titleEl) return;
  const ourName = getOurProductName(outputs);
  const ourLabel = ourName ? '我方: ' + ourName + ' · ' : '';
  if (writer && writer.status === 'completed' && writer.output_summary) {
    bodyEl.classList.remove('is-empty', 'is-running');
    titleEl.textContent = ourLabel + '竞品分析报告';
    bodyEl.textContent = writer.output_summary;
    const completed = outputs.filter(o => o.status === 'completed').length;
    const confs = outputs.filter(o => o.confidence).map(o => (o.confidence * 100).toFixed(0) + '%');
    metaEl.textContent = 'Agent 完成: ' + completed + '/' + outputs.length + (confs.length ? ' · 置信度: ' + confs.join(' / ') : '') + ' · ' + new Date().toLocaleString('zh-CN');
    // 更新赛道徽标，显示当前产品名称
    if (ourName) {
      const badge = document.getElementById('track-badge');
      if (badge) badge.textContent = ourName;
    }
  } else if (writer && writer.status === 'running') {
    bodyEl.classList.remove('is-empty');
    bodyEl.classList.add('is-running');
    titleEl.textContent = (ourName ? '我方: ' + ourName + ' · ' : '') + '撰写中…';
    bodyEl.innerHTML = '<span class="typing">Writer Agent 正在生成执行摘要</span>';
  } else if (outputs.some(o => o.status === 'running')) {
    setExecAnalyzing('Agent 正在分析竞品证据，完成后会生成执行摘要。');
  }
}

function renderRadarChart(outputs) {
  const canvas = document.getElementById('radarChart');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const competitors = collectCompetitors(outputs);
  const labels = competitors.length > 0 ? competitors.map(c => c[0]) : ['Competitor A', 'Competitor B', 'Competitor C'];
  const datasets = labels.map((name, i) => {
    const scoreObj = findThreatScoresFor(name, outputs);
    const noEvidence = hasNoScoreableEvidence(name, outputs);
    const scores = noEvidence
      ? THREAT_DIMS.map(() => null).concat([null])
      : scoreObj
      ? THREAT_DIMS.map(d => scoreObj[d.key] || 0).concat([scoreObj.overall || 0])
      : THREAT_DIMS.map(() => 0).concat([0]);
    return {
      label: name + (noEvidence ? '（待评估）' : ''), data: scores,
      backgroundColor: 'transparent',
      borderColor: A3_COMPETITOR_COLORS[i % A3_COMPETITOR_COLORS.length], borderWidth: 1.5,
      borderDash: i >= 3 ? [4, 3] : undefined,
      pointBackgroundColor: A3_COMPETITOR_COLORS[i % A3_COMPETITOR_COLORS.length], pointBorderColor: '#fff',
      pointRadius: 3, pointHoverRadius: 5,
    };
  });
  if (radarChart) radarChart.destroy();
  renderRadarLegend(datasets);
  radarChart = new Chart(ctx, {
    type: 'radar',
    data: { labels: A3_DIMS, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      layout: { padding: { top: 4, right: 12, bottom: 4, left: 12 } },
      scales: { r: { beginAtZero: true, max: 100, ticks: { stepSize: 20, backdropColor: 'transparent', font: { size: 10 } }, pointLabels: { font: { size: 12 } }, grid: { color: '#e4e0d8' }, angleLines: { color: '#e4e0d8' } } },
      plugins: { legend: { display: false } },
    },
  });
}


function renderHeatmap(outputs) {
  const container = document.getElementById('heatmap-table');
  if (!container) return;
  const qa = (outputs || []).find(o => o.role === 'qa' || o.node_id === 'qa');
  if (qa && qa.status === 'error' && String(qa.output_summary || '').includes('威胁矩阵生成失败')) {
    container.innerHTML = '<div class="exec-placeholder">威胁矩阵生成失败，请重新运行或补充证据。</div>';
    return;
  }
  const competitors = collectCompetitors(outputs);
  const compNames = competitors.length > 0 ? competitors.map(c => c[0]) : activeCompetitorNames.slice(0, 3);
  if (compNames.length === 0) {
    container.innerHTML = '<div class="exec-placeholder">等待 QA 生成对我方威胁矩阵</div>';
    return;
  }
  let html = '<div class="heatmap-dim-header"><div class="heatmap-hcell heatmap-header" style="flex:0 0 96px;"></div>';
  THREAT_DIMS.forEach(d => {
    html += '<div class="heatmap-hcell heatmap-header" data-tooltip="' + escapeHTML(d.tip) + '">' + escapeHTML(d.label) + '</div>';
  });
  html += '<div class="heatmap-hcell heatmap-header">综合等级</div></div>';
  compNames.forEach((name, i) => {
    const scoreObj = findThreatScoresFor(name, outputs);
    const noEvidence = hasNoScoreableEvidence(name, outputs);
    html += '<div class="heatmap-data-row"><div class="heatmap-hcell heatmap-row-label" style="flex:0 0 96px;">' + escapeHTML(name) + '</div>';
    if (scoreObj && !noEvidence) {
      THREAT_DIMS.forEach(d => {
        const score = scoreObj[d.key] ?? 0;
        const cls = 'threat-' + scoreLevel(score);
        html += '<div class="heatmap-hcell ' + cls + '" data-tooltip="' + escapeHTML(d.label + ': ' + score + '分') + '">' + escapeHTML(scoreLabel(score)) + '</div>';
      });
      const overall = scoreObj.overall ?? Math.round(THREAT_DIMS.reduce((sum, d) => sum + (scoreObj[d.key] || 0), 0) / THREAT_DIMS.length);
      html += '<div class="heatmap-hcell threat-' + scoreLevel(overall) + '" data-tooltip="默认等权平均，后续可按当前竞争担忧调整权重">' + escapeHTML(scoreLabel(overall)) + '</div>';
    } else if (noEvidence) {
      THREAT_DIMS.forEach(() => {
        html += '<div class="heatmap-hcell heatmap-na">待评估</div>';
      });
      html += '<div class="heatmap-hcell heatmap-na">待评估</div>';
    } else {
      THREAT_DIMS.forEach(d => {
        html += '<div class="heatmap-hcell heatmap-na">暂无</div>';
      });
      html += '<div class="heatmap-hcell heatmap-na">暂无</div>';
    }
    html += '</div>';
  });
  container.innerHTML = html;
}


function collectResponseActions(outputs) {
  const actions = [];
  (outputs || []).forEach(o => {
    if (Array.isArray(o.response_actions)) o.response_actions.forEach(a => actions.push(a));
  });
  return actions.sort((a, b) => Number(b.priority || 0) - Number(a.priority || 0));
}


function renderResponseActions(outputs) {
  const container = document.getElementById('response-actions');
  if (!container) return;
  const writer = (outputs || []).find(o => o.role === 'writer' || o.node_id === 'writer');
  if (writer && writer.status === 'error' && String(writer.output_summary || '').includes('行动建议生成失败')) {
    container.innerHTML = '<div class="exec-placeholder">行动建议生成失败，请重新运行或补充证据。</div>';
    return;
  }
  const actions = collectResponseActions(outputs);
  if (actions.length === 0) {
    container.innerHTML = '<div class="exec-placeholder">等待撰写 Agent 生成正式行动建议</div>';
    return;
  }
  container.innerHTML = actions.slice(0, 9).map((a, i) => {
    const type = VALUE_LABELS[a.response_type] || '行动';
    const dimension = THREAT_DIMS.find(d => d.key === a.related_threat_dimension)?.label || fieldLabel(a.related_threat_dimension || 'overall');
    return '<div class="response-action-card">' +
      '<div class="response-action-top"><span class="response-action-type">' + escapeHTML(type) + '</span><span class="response-action-priority">P' + (i + 1) + ' · ' + escapeHTML(String(a.priority || '')) + '</span></div>' +
      '<div class="response-action-title">' + escapeHTML(a.concrete_action || a.action || '待补充明确动作') + '</div>' +
      '<div class="response-action-meta">' +
        '<div>竞品: ' + escapeHTML(a.competitor || '未指定') + '</div>' +
        '<div>维度: ' + escapeHTML(dimension) + '</div>' +
        '<div>依据: ' + escapeHTML(a.evidence_basis || '等待证据补充') + '</div>' +
        '<div>监控: ' + escapeHTML(a.monitoring_signal || '待定义') + '</div>' +
      '</div></div>';
  }).join('');
}

function renderEvidenceStrip(outputs) {
  const strip = document.getElementById('evidence-strip');
  if (!strip) return;
  const allEvidence = [];
  outputs.forEach(o => {
    if (o.evidence && Array.isArray(o.evidence)) {
      o.evidence.forEach(e => { allEvidence.push({ ...e, agentRole: o.role, agentLabel: roleLabel(o), agentConf: o.confidence }); });
    }
  });
  if (allEvidence.length === 0) {
    strip.innerHTML = '<div class="ev-placeholder">等待采集 Agent 证据…</div>';
    return;
  }
  strip.innerHTML = allEvidence.map((e, i) => {
    const quote = e.quote ? e.quote.slice(0, 200) : '';
    const label = zhText(e.source_label || '来源 #' + (i + 1));
    const confPct = e.agentConf ? (e.agentConf * 100).toFixed(0) + '%' : '';
    return '<div class="ev-strip-card">' +
      '<div class="ev-source"><span class="src-badge">' + escapeHTML(e.agentLabel || e.agentRole || 'agent') + '</span>' + escapeHTML(label) + '</div>' +
      (quote ? '<div class="ev-quote">"' + escapeHTML(zhText(quote)) + '"</div>' : '') +
      '<div class="ev-relevance-strip">' + (e.relevance ? escapeHTML(zhText(e.relevance).slice(0, 120)) : '') + '</div>' +
      '<div class="ev-meta-row"><span>' + (e.source_url ? '<a href="' + safeURL(e.source_url) + '" target="_blank" rel="noopener noreferrer" style="color:var(--accent-indigo);text-decoration:none;">' + escapeHTML(evidenceLinkLabel(e.source_url)) + '</a>' : '') + '</span><span>置信度: ' + confPct + '</span></div>' +
      '</div>';
  }).join('');
}

function renderA3Dashboard(outputs) {
  if (!outputs || outputs.length === 0) return;
  renderExecBanner(outputs);
  renderRadarChart(outputs);
  renderHeatmap(outputs);
  renderResponseActions(outputs);
  renderScoringMethodology();
  renderEvidenceStrip(outputs);
}

// ══════════════════════════════════════════════════════════════════
// 评分方法
// ══════════════════════════════════════════════════════════════════
function renderScoringMethodology() {
  const list = document.getElementById('sm-dim-list');
  if (!list) return;
  const dimDetails = THREAT_DIMS.map(d => ({
    name: d.label,
    desc: d.tip + '。内部使用 0-100 分，UI 显示高/中/低 + 分数。综合等级默认按四维等权平均。',
    kw: d.key,
  }));
  list.innerHTML = dimDetails.map(d =>
    '<div class="sm-dim-card">' +
    '<div class="sm-dim-name">' + escapeHTML(d.name) + '</div>' +
    '<div class="sm-dim-desc">' + escapeHTML(d.desc) + '</div>' +
    '<div class="sm-dim-kw">字段: ' + escapeHTML(d.kw) + '</div>' +
    '</div>'
  ).join('');
}


// 切换评分方法说明
document.addEventListener('DOMContentLoaded', () => {
  const smHeader = document.getElementById('sm-header');
  if (smHeader) {
    smHeader.addEventListener('click', () => {
      document.getElementById('scoring-methodology').classList.toggle('open');
    });
  }
});
// 立即绑定一次，以兼容页面加载事件已经触发的情况
(function bindSM() {
  const smHeader = document.getElementById('sm-header');
  if (smHeader) {
    smHeader.addEventListener('click', () => {
      document.getElementById('scoring-methodology').classList.toggle('open');
    });
  }
})();

// ══════════════════════════════════════════════════════════════════
// 初始化
// ══════════════════════════════════════════════════════════════════
function init() {
  clearDashboardToEmpty();
  refreshHistoryView();
  connectWebSocket();
  document.getElementById('btn-pause').addEventListener('click', togglePause);
}
init();
