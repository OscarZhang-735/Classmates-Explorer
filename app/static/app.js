const $ = (id) => document.getElementById(id);
const terminal = new Set(['completed', 'partial', 'failed', 'cancelled']);
let taskId = null, page = 1, timer = null, generation = 0, resultRequest = 0, pollRequest = 0;
let language = localStorage.getItem('explorer-language') === 'zh-CN' ? 'zh-CN' : 'en';
let theme = localStorage.getItem('explorer-theme') === 'light' ? 'light' : 'dark';
let lastTask = null, lastResult = null, lastError = '';
const messages = {
  'zh-CN': {
    portfolioStarsBadge:'★ {0}',portfolioStarsTitle:'自有公开非 Fork 仓库中，Stars 最高的最多 10 个仓库合计',
    rankLabel:'排名第 {0}',
    scaleScores:'曲线缩放',scaledScoreNote:'缩放分 · 原始 {0}/100',originalScoreDetails:'原始得分明细',
    scaleScoresHint:'按当前筛选结果（跨所有分页）的最高分等比例缩放至 100；全为 0 时保持 0。仅改变总分显示。',
    explorationScore:'探索评分',sortScoreDesc:'探索评分：高 → 低',sortScoreAsc:'探索评分：低 → 高',sortProfessional:'专业信号：高 → 低',sortActivity:'活跃度：高 → 低',
    scoreGuide:'评分规则',scoreGuideText:'总计100分。专业信号 50 分：自有公开非 Fork 仓库中星数最高的 10 个仓库合计 stars 25、PR/Review/Issue 协作 10、账号年限 10、非 Fork 仓库数 5。活跃度 50 分：公开贡献估计值 28、贡献总量 7、活跃周数 10、最近贡献 5。数量采用对数递减收益，最近贡献每 30 天减半。公开贡献估计值为总量减受限数量，以采集凭据可见范围为准；日历活跃度包含共享的受限贡献。缺失项不计分、不放大其他权重；覆盖率按已知指标权重计算，暂定分是已知数据下限。所有时间均相对快照截止日。评分用于发现伙伴，不代表技能认证。',
    scoreParts:'专业信号 {0}/50 · 活跃度 {1}/50',scoreCoverage:'数据覆盖率 {0}%',scoreProvisional:'暂定分 · 数据不完整',scoreDetails:'查看得分明细',scorePending:'采集结束后评分',scoreUnknown:'未知',
    score_stars:'代表作 Stars',score_collaboration:'PR / Review / Issue 协作',score_account_age:'账号年限',score_original_repositories:'非 Fork 公开仓库',score_public_contributions:'公开贡献估计值',score_total_contributions:'贡献总量',score_consistency:'活跃周数',score_recency:'最近贡献',
    publicEstimate:'公开贡献估计值 {0}',calendarStats:'活跃周 {0} · 最近贡献 {1}',
    title:'Classmates Explorer · Fork 探索',language:'语言',apiDocs:'API 文档 ↗',themeLight:'☀ 浅色模式',themeDark:'☾ 深色模式',intro:'查询公开仓库的 Fork、所有者资料和贡献。',
    tokenNotice:'请在服务端配置 GITHUB_TOKEN 并重启。',tokenConfig:'GitHub API 配置',tokenConfigured:'Token 已配置',tokenNotConfigured:'未配置',tokenPlaceholder:'粘贴 GitHub Token',saveToken:'保存 Token',showToken:'显示 Token',hideToken:'隐藏 Token',confirmRevealToken:'Token 属于敏感凭据。确定显示原文吗？',repositoryUrl:'公开仓库 URL',explore:'探索',
    limitNote:'⚠ 最多显示最新 {0} 个一级 Fork，并遵守默认API限流策略。',unlimitedLimitNote:'⚠ 无限制模式：使用所有可用的 GitHub API points',unlimitedMode:'无限制模式（不推荐）',unlimitedDescription:'使用可用 GitHub points，额度每小时重置后继续。',importJson:'导入 JSON 快照',
    preparing:'准备查询',exportJson:'导出 JSON',exportCsv:'导出 CSV',exportWhenDone:'查询完成后可导出',retry:'重试未完成部分',cancel:'取消任务',
    searchUsername:'搜索用户名',usernamePlaceholder:'输入 GitHub 用户名',sort:'排序',sortForkDesc:'Fork 时间：新 → 旧',sortForkAsc:'Fork 时间：旧 → 新',
    sortContribDesc:'贡献总数：高 → 低',sortContribAsc:'贡献总数：低 → 高',sortReposDesc:'公开仓库数：高 → 低',sortReposAsc:'公开仓库数：低 → 高',
    sortStarsDesc:'Fork Star 数：高 → 低',sortStarsAsc:'Fork Star 数：低 → 高',sortAccountDesc:'账号创建：新 → 旧',sortAccountAsc:'账号创建：旧 → 新',
    accountCreated:'账号创建',forkCreated:'Fork 创建',accountCreatedTime:'账号创建时间',forkCreatedTime:'Fork 创建时间',withinYear:'仅限一年内',from:'从',to:'至',clearFilters:'清除筛选',forkRepository:'Fork 仓库',owner:'所有者',
    basicInfo:'基本信息',yearContributions:'近一年全站贡献',previous:'上一页',next:'下一页',dataCaveat:'数据以 GitHub API 为准，含用户选择公开的私有贡献。总数可能与分类之和不同，采集期间数据也可能变化。',
    footer:'OscarZhang-735',created:'创建 {0}',pushed:'推送 {0}',noCompanyLocation:'未填写公司或地区',accountCreated:'账号创建 {0}',repositoryStats:'公开仓库 {0}（非 Fork：{1}）',
    profileCollected:'资料采集 {0}',restricted:'受限贡献 {0}',collected:'采集 {0}',contributionBreakdown:'Commit {0} · PR {1} · Issue {2} · Review {3}',userType:'个人账号',organizationType:'组织',noResults:'暂无结果',pageLabel:'共 {0} 条 · 第 {1} / {2} 页',
    progress:'一级 Fork {0}/{1} · 所有者 {2}/{3} · 失败 {4}',pendingTotal:'待查询',interval:'贡献统计：{0} 至 {1}（UTC）',
    truncated:'仅显示最新 {0} 个一级 Fork，约 {1} 个未纳入。',resume:'预计恢复：{0}',imported:'本地快照 · 原任务状态：{0}',cancelledNotice:'结果已保留，可继续未完成部分。',
    invalidRequest:'请求参数无效',unknownError:'请求失败，请检查输入或稍后重试。',importTooLarge:'导入文件不能超过 {0} MB',
    queued:'排队中',running:'查询中',waiting_rate_limit:'等待 GitHub 额度恢复',completed:'查询完成',partial:'部分完成',failed:'查询失败',cancelled:'已取消',
    contribution_pending:'等待获取',contribution_failed:'获取失败',contribution_not_applicable:'不适用',contribution_unknown:'未知',
  },
  en: {
    portfolioStarsBadge:'★ {0}',portfolioStarsTitle:'Stars across up to 10 top owned public non-fork repositories',
    rankLabel:'Rank {0}',
    scaleScores:'Scale scores',scaledScoreNote:'Scaled · Original {0}/100',originalScoreDetails:'Original score breakdown',
    scaleScoresHint:'Scale proportionally so the highest score across all filtered pages is 100. All-zero scores stay zero. Changes only the displayed total.',
    explorationScore:'Explorer score',sortScoreDesc:'Explorer score: highest first',sortScoreAsc:'Explorer score: lowest first',sortProfessional:'Professional signals: highest first',sortActivity:'Activity: highest first',
    scoreGuide:'Scoring rules',scoreGuideText:'100 points in total. Professional signals (50): stars across the top 10 owned public non-fork repositories (25), PR/review/issue collaboration (10), account age (10), and non-fork repositories (5). Activity (50): estimated public contributions (28), total contributions (7), active weeks (10), and recent contribution (5). Counts use logarithmic diminishing returns; recency halves every 30 days. Public contributions are estimated as total minus restricted, within the collecting credential’s visibility; calendar activity includes shared restricted contributions. Missing metrics earn no points and do not increase other weights. Coverage is the known share of metric weights; provisional scores are lower bounds from known data. Dates are relative to the snapshot endpoint. Scores help discover peers and do not certify skills.',
    scoreParts:'Professional {0}/50 · Activity {1}/50',scoreCoverage:'Data coverage {0}%',scoreProvisional:'Provisional · Incomplete data',scoreDetails:'Score breakdown',scorePending:'Scored after collection',scoreUnknown:'Unknown',
    score_stars:'Portfolio stars',score_collaboration:'PR / review / issue collaboration',score_account_age:'Account age',score_original_repositories:'Public non-fork repositories',score_public_contributions:'Estimated public contributions',score_total_contributions:'Total contributions',score_consistency:'Active weeks',score_recency:'Recent contribution',
    publicEstimate:'Estimated public contributions {0}',calendarStats:'Active weeks {0} · Last contribution {1}',
    title:'Classmates Explorer · Explore Forks',language:'Language',apiDocs:'API docs ↗',themeLight:'☀ Light mode',themeDark:'☾ Dark mode',intro:'Explore public-repo forks, owner profiles, and contributions.',
    tokenNotice:'Set GITHUB_TOKEN on the server and restart.',tokenConfig:'GitHub API settings',tokenConfigured:'Token Configured',tokenNotConfigured:'Not configured',tokenPlaceholder:'Paste a GitHub token',saveToken:'Save token',showToken:'Show token',hideToken:'Hide token',confirmRevealToken:'Tokens are sensitive credentials. Show the full token?',repositoryUrl:'Public repository URL',explore:'Explore',
    limitNote:'⚠ Up to {0} newest direct forks, and comply with the default API rate limiting policy.',unlimitedLimitNote:'⚠ Unlimited mode: uses all available GitHub API points',unlimitedMode:'Unlimited mode (Not Recommended)',unlimitedDescription:'Uses available GitHub points and continues after each hourly reset.',importJson:'Import JSON snapshot',
    preparing:'Preparing query',exportJson:'Export JSON',exportCsv:'Export CSV',exportWhenDone:'Available after the query completes',retry:'Retry unfinished work',cancel:'Cancel task',
    searchUsername:'Search username',usernamePlaceholder:'Enter a GitHub username',sort:'Sort by',sortForkDesc:'Fork date: newest first',sortForkAsc:'Fork date: oldest first',
    sortContribDesc:'Contributions: highest first',sortContribAsc:'Contributions: lowest first',sortReposDesc:'Public repositories: most first',sortReposAsc:'Public repositories: fewest first',
    sortStarsDesc:'Fork stars: most first',sortStarsAsc:'Fork stars: fewest first',sortAccountDesc:'Account date: newest first',sortAccountAsc:'Account date: oldest first',
    accountCreated:'Account created',forkCreated:'Fork created',accountCreatedTime:'Account creation date',forkCreatedTime:'Fork creation date',withinYear:'Within one year',from:'From',to:'To',clearFilters:'Clear filters',forkRepository:'Fork repository',owner:'Owner',
    basicInfo:'Profile',yearContributions:'GitHub-wide contributions (past year)',previous:'Previous',next:'Next',dataCaveat:'Counts use GitHub-visible data, including shared private contributions. Totals may differ from category sums, and data may change during collection.',
    footer:'OscarZhang-735',created:'Created {0}',pushed:'Pushed {0}',noCompanyLocation:'No company or location',accountCreated:'Account created {0}',repositoryStats:'Public repositories {0} (Non-fork: {1})',
    profileCollected:'Profile collected {0}',restricted:'Restricted contributions {0}',collected:'Collected {0}',contributionBreakdown:'Commits {0} · Pull requests {1} · Issues {2} · Reviews {3}',userType:'User',organizationType:'Organization',noResults:'No results',pageLabel:'{0} results · Page {1} of {2}',
    progress:'Forks {0}/{1} · Owners {2}/{3} · Failed {4}',pendingTotal:'pending',interval:'Contribution period: {0}–{1} UTC',
    truncated:'Showing the {0} newest direct forks; about {1} omitted.',resume:'Resumes around {0}',imported:'Local snapshot · Original status: {0}',cancelledNotice:'Results saved. Resume to continue.',
    invalidRequest:'Invalid request parameters',unknownError:'Request failed. Check the input or try again later.',importTooLarge:'Import file must be no larger than {0} MB',
    queued:'Queued',running:'Running',waiting_rate_limit:'Waiting for GitHub rate limit',completed:'Completed',partial:'Partially completed',failed:'Failed',cancelled:'Cancelled',
    contribution_pending:'Pending',contribution_failed:'Failed to fetch',contribution_not_applicable:'Not applicable',contribution_unknown:'Unknown',
  },
};
const apiErrorKeys = {
  '服务端仍在运行旧版本，请重启应用后刷新页面':'serverRestartRequired',
  '仅允许同源请求':'sameOrigin','任务不存在':'taskNotFound','任务提交过于频繁':'submissionRate',
  '任务队列已满，请稍后重试':'queueFull','请先在服务端 .env 中配置 GITHUB_TOKEN':'tokenMissing',
  '账号创建时间的起始日期不能晚于结束日期':'accountRange','Fork 创建时间的起始日期不能晚于结束日期':'forkRange',
  '仅查询完成的任务可以导出':'exportIncomplete','导入文件为空':'emptyImport',
  '不是有效的 Classmates Explorer v1 JSON 快照':'invalidSnapshot','该任务不可重试，请创建新任务':'cannotRetry',
  '凭据已更换，请创建新任务':'credentialChanged','日期筛选必须为有效的 YYYY-MM 或 YYYY-MM-DD':'invalidDateFilter',
  '任务运行期间不能更换 GitHub Token':'tokenTaskActive','尚未配置 GitHub Token':'tokenNotConfiguredError',
};
Object.assign(messages.en, {
  serverRestartRequired:'The server is still running an older version. Restart the app, then refresh this page.',
  sameOrigin:'Same-origin requests only',taskNotFound:'Task not found',submissionRate:'Too many task submissions',queueFull:'Task queue is full; try again later',
  tokenMissing:'Set GITHUB_TOKEN in the server .env file first',accountRange:'Account start date must not be after end date',forkRange:'Fork start date must not be after end date',
  exportIncomplete:'Only completed tasks can be exported',emptyImport:'Import file is empty',invalidSnapshot:'Not a valid Classmates Explorer v1 JSON snapshot',
  cannotRetry:'This task cannot be retried; start a new task',credentialChanged:'The token changed; start a new task',
  tokenTaskActive:'The GitHub token cannot be changed while a task is running',
  tokenNotConfiguredError:'GitHub token is not configured',
  invalidDateFilter:'Use a valid YYYY-MM or YYYY-MM-DD date filter',
  error_contribution_unavailable:'Could not fetch this user’s contributions',error_partial_contributions:'Some contributions could not be fetched; you can retry',
  error_owner_unavailable:'GitHub did not return a fork owner; the checkpoint was saved',error_invalid_cursor:'GitHub pagination did not advance; the query stopped',
  error_internal_error:'Task processing failed; results and checkpoint were saved',error_not_found:'Repository not found or inaccessible',
  error_private_repository:'Only public repositories are supported',error_token_missing:'Set GITHUB_TOKEN on the server',error_credential_changed:'The token changed; start a new task',
  error_budget_exhausted:'This run reached its GitHub request budget',error_upstream_timeout:'GitHub contribution query timed out',
  error_upstream_unavailable:'GitHub is temporarily unavailable',error_rate_limit_exhausted:'GitHub continues to rate limit requests; retry later',
  error_token_invalid:'GITHUB_TOKEN is invalid or expired',error_access_denied:'GitHub denied access; check token permissions',
  error_batch_resource_limit:'GitHub contribution query exceeded resource limits',error_upstream_error:'GitHub rejected the request',
  error_graphql_error:'GitHub returned a query error',error_imported_partial:'This imported snapshot is incomplete',
  error_unknown:'The task failed; check the task details or retry later',
});
function t(key, ...args) { return (messages[language][key] ?? messages.en[key] ?? key).replace(/\{(\d+)\}/g, (_, i) => args[Number(i)] ?? ''); }
function apiError(message) {
  if (language !== 'en') return message || t('invalidRequest');
  if (typeof message !== 'string') return t('invalidRequest');
  for (const prefix of [messages.en.invalidRequest, messages['zh-CN'].invalidRequest]) {
    if (message.startsWith(prefix)) return t('invalidRequest') + message.slice(prefix.length);
  }
  const size = message.match(/^导入文件不能超过 (\d+) MB$/);
  return size ? t('importTooLarge', size[1]) : t(apiErrorKeys[message] || 'unknownError');
}
function error(message) { lastError = message || ''; $('error').textContent = apiError(lastError); $('error').hidden = !lastError; }
function applyTheme() {
  document.documentElement.dataset.theme = theme;
  $('theme-toggle').textContent = t(theme === 'dark' ? 'themeLight' : 'themeDark');
  $('theme-toggle').setAttribute('aria-pressed', String(theme === 'dark'));
}
function renderTokenStatus(configured) {
  const status = $('token-status');
  status.dataset.configured = String(configured);
  status.classList.toggle('configured', configured);
  status.textContent = t(configured ? 'tokenConfigured' : 'tokenNotConfigured');
  $('reveal-token').hidden = !configured;
  $('reveal-token').textContent = t($('github-token').type === 'text' ? 'hideToken' : 'showToken');
}
function renderUnlimitedMode(enabled) {
  $('unlimited-mode').checked = enabled;
  $('unlimited-mode').dataset.enabled = String(enabled);
  $('limit-note').textContent = enabled ? t('unlimitedLimitNote') : t('limitNote', $('limit-note').dataset.limit);
}
function applyLanguage() {
  document.documentElement.lang = language;
  document.title = t('title');
  $('language').value = language;
  for (const input of document.querySelectorAll('input[type="date"]')) input.lang = language;
  $('language').setAttribute('aria-label', t('language'));
  $('scale-scores').closest('label').title = t('scaleScoresHint');
  for (const node of document.querySelectorAll('[data-i18n]')) node.textContent = t(node.dataset.i18n);
  for (const node of document.querySelectorAll('[data-i18n-placeholder]')) node.placeholder = t(node.dataset.i18nPlaceholder);
  renderTokenStatus($('token-status').dataset.configured === 'true');
  renderUnlimitedMode($('unlimited-mode').dataset.enabled === 'true');
  applyTheme();
  if (lastTask) renderTask(lastTask);
  if (lastResult) renderResults(lastResult);
  error(lastError);
}

async function api(url, options = {}) {
  const response = await fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
  const body = await response.json();
  if (!response.ok) {
    let message = typeof body.detail === 'string' ? body.detail : t('invalidRequest');
    if (Array.isArray(body.detail)) {
      const obsoleteSort = body.detail.some(issue => issue.loc?.join('.') === 'query.sort'
        && issue.type === 'literal_error' && ['score', 'professional', 'activity'].includes(issue.input));
      const fields = [...new Set(body.detail.filter(issue => Array.isArray(issue.loc))
        .map(issue => issue.loc.join('.')))];
      message = obsoleteSort ? '服务端仍在运行旧版本，请重启应用后刷新页面'
        : `${t('invalidRequest')}${fields.length ? ` (${fields.join(', ')})` : ''}`;
    }
    const failure = new Error(message);
    failure.status = response.status; throw failure;
  }
  return body;
}
function text(tag, value, className) {
  const node = document.createElement(tag); node.textContent = value ?? '—';
  if (className) node.className = className; return node;
}
function link(label, url) {
  const node = text('a', label);
  try { const parsed = new URL(url); if (['http:', 'https:'].includes(parsed.protocol)) {
    node.href = parsed.href; node.target = '_blank'; node.rel = 'noopener noreferrer';
  }} catch (_) { /* Empty or malformed external profile URL stays plain text. */ }
  return node;
}
function date(value) { return value ? new Date(value).toLocaleString(language) : '—'; }
function row(item, rank) {
  const tr = document.createElement('tr');
  const repo = document.createElement('td');
  const ranking = text('span', `#${rank}`, 'rank-badge');
  ranking.title = t('rankLabel', rank);
  repo.append(ranking, link(item.name, item.url), text('small', t('created', date(item.created_at))), text('small', t('pushed', date(item.pushed_at))));
  const owner = document.createElement('td'), profile = document.createElement('div'); profile.className = 'owner';
  const image = document.createElement('img'); image.className = 'avatar'; image.alt = ''; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer';
  try { const url = new URL(item.owner.avatar_url); if (url.protocol === 'https:') image.src = url.href; } catch (_) {}
  profile.append(image, link(item.owner.login, item.owner.url));
  const badges = document.createElement('div'); badges.className = 'badges';
  const portfolioStars = text('span', t('portfolioStarsBadge', item.owner.top_repository_stars ?? '—'), 'badge star-badge');
  portfolioStars.title = t('portfolioStarsTitle');
  badges.append(text('span', t(item.owner.type === 'Organization' ? 'organizationType' : 'userType'), 'badge'), portfolioStars);
  owner.append(profile, text('p', item.owner.name), badges);
  const info = document.createElement('td');
  info.append(text('p', item.owner.bio), text('small', [item.owner.company, item.owner.location].filter(Boolean).join(' · ') || t('noCompanyLocation')));
  if (item.owner.website_url) info.append(link(item.owner.website_url, item.owner.website_url));
  info.append(text('small', t('accountCreated', date(item.owner.account_created_at))));
  info.append(text('small', t('repositoryStats', item.owner.public_repositories ?? '—', item.owner.original_repositories ?? '—')));
  info.append(text('small', t('profileCollected', date(item.owner.collected_at))));
  const contribution = document.createElement('td'), c = item.contribution;
  if (c.status === 'completed') {
    contribution.append(text('div', c.total.toLocaleString(language), 'total'), text('p', t('contributionBreakdown', c.commits, c.pull_requests, c.issues, c.reviews)));
    if (c.restricted) contribution.append(text('small', t('restricted', c.restricted)));
    if (item.score?.public_contributions != null) contribution.append(text('small', t('publicEstimate', item.score.public_contributions)));
    contribution.append(text('small', t('calendarStats', c.active_weeks ?? '—', date(c.last_contribution_at))));
    contribution.append(text('small', t('collected', date(c.collected_at))));
  } else contribution.append(text('p', t(['pending','failed','not_applicable'].includes(c.status) ? `contribution_${c.status}` : 'contribution_unknown')));
  const scoreCell = document.createElement('td'), score = item.score;
  scoreCell.className = 'score-cell';
  if (score?.total != null) {
    const scaled = $('scale-scores').checked;
    const maximum = lastResult?.max_score;
    const displayed = scaled && maximum > 0 ? Math.min(100, Math.round(score.total / maximum * 1000) / 10) : score.total;
    scoreCell.append(text('div', `${displayed}`, 'total'));
    if (scaled) scoreCell.append(text('small', t('scaledScoreNote', score.total)));
    scoreCell.append(text('small', t('scoreParts', score.professional, score.activity)), text('small', t('scoreCoverage', score.coverage)));
    if (score.status === 'provisional') scoreCell.append(text('span', t('scoreProvisional'), 'badge'));
    const details = document.createElement('details');
    details.append(text('summary', t(scaled ? 'originalScoreDetails' : 'scoreDetails')));
    for (const [key, value] of Object.entries(score.components)) {
      details.append(text('small', `${t(`score_${key}`)}: ${value.points ?? t('scoreUnknown')} / ${value.maximum}`));
    }
    scoreCell.append(details);
  } else scoreCell.append(text('small', t(score?.status === 'not_applicable' ? 'contribution_not_applicable' : 'scorePending')));
  tr.append(repo, owner, info, contribution, scoreCell); return tr;
}
function renderResults(result) {
  lastResult = result;
  const firstRank = (result.page - 1) * result.per_page + 1;
  $('results').replaceChildren(...result.items.map((item, index) => row(item, firstRank + index)));
  if (!result.items.length) { const tr = document.createElement('tr'), td = text('td', t('noResults')); td.colSpan = 5; tr.append(td); $('results').append(tr); }
  $('page-label').textContent = t('pageLabel', result.total, page, Math.max(1,Math.ceil(result.total/50)));
  $('prev').disabled = page <= 1; $('next').disabled = page * 50 >= result.total;
}
async function refreshResults(id = taskId, version = generation) {
  if (!id) return;
  const request = ++resultRequest;
  const [sort, direction] = $('sort').value.split(':');
  const params = new URLSearchParams({page, per_page:50, search:$('search').value, sort, direction});
  const filters = {
    account_created_from: $('account-created-from').value,
    account_created_to: $('account-created-to').value,
    fork_created_from: $('fork-created-from').value,
    fork_created_to: $('fork-created-to').value,
  };
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
  const result = await api(`/api/tasks/${id}/results?${params}`);
  if (id !== taskId || version !== generation || request !== resultRequest) return;
  renderResults(result);
}
function renderTask(task) {
  lastTask = task;
  $('task-panel').hidden = false;
  $('task-title').textContent = t(task.status);
  const p = task.progress;
  $('progress').textContent = t('progress', p.forks_fetched, task.total_direct_forks ?? t('pendingTotal'), p.owners_completed, p.owners_total, p.owners_failed);
  $('interval').textContent = t('interval', task.from.slice(0,10), task.to.slice(0,10));
  const notices = [];
  if (task.truncated) notices.push(t('truncated', task.limit, task.omitted_forks));
  if (task.error) notices.push(language === 'en' ? t(messages.en[`error_${task.error.code}`] ? `error_${task.error.code}` : 'error_unknown') : task.error.message);
  if (task.resume_at) notices.push(t('resume', date(task.resume_at * 1000)));
  if (task.imported) notices.push(t('imported', t(task.source_status)));
  if (task.status === 'cancelled') notices.push(t('cancelledNotice'));
  $('task-notice').textContent = notices.join(' '); $('task-notice').hidden = !notices.length;
  $('cancel').hidden = terminal.has(task.status);
  $('retry').hidden = !(['partial','failed','cancelled'].includes(task.status) && task.retryable);
  for (const format of ['json','csv']) {
    const button = $(`export-${format}`), enabled = task.status === 'completed';
    button.disabled = !enabled; button.title = enabled ? '' : t('exportWhenDone');
  }
}
async function poll() {
  const id = taskId, version = generation, request = ++pollRequest; clearTimeout(timer);
  try {
    const task = await api(`/api/tasks/${id}`);
    if (id !== taskId || version !== generation || request !== pollRequest) return;
    renderTask(task);
    await refreshResults(id, version);
    if (!terminal.has(task.status) && version === generation && request === pollRequest) timer = setTimeout(poll, 2000);
  } catch (e) {
    if (version === generation && request === pollRequest) {
      error(e.message);
      if (e.status === 404) { localStorage.removeItem('explorer-task'); $('task-panel').hidden = true; }
      else timer = setTimeout(poll, 5000);
    }
  }
}
$('query-form').addEventListener('submit', async (event) => {
  event.preventDefault(); error(''); $('submit').disabled = true;
  try {
    const task = await api('/api/tasks', {method:'POST', body:JSON.stringify({repository_url:$('repo-url').value})});
    taskId = task.id; generation++; page = 1; lastTask = null; lastResult = null;
    localStorage.setItem('explorer-task', taskId); await poll();
  } catch (e) { error(e.message); } finally { $('submit').disabled = false; }
});
async function saveToken() {
  const token = $('github-token').value;
  if (!token) { $('github-token').focus(); return; }
  error(''); $('save-token').disabled = true;
  try {
    const result = await api('/api/config/github', {method:'POST', body:JSON.stringify({token})});
    $('github-token').value = '';
    $('github-token').type = 'password';
    $('reveal-token').textContent = t('showToken');
    renderTokenStatus(result.configured);
  } catch (e) { error(e.message); } finally { $('save-token').disabled = false; }
}
$('save-token').onclick = saveToken;
$('reveal-token').onclick = async () => {
  const input = $('github-token');
  if (input.type === 'text') {
    input.type = 'password'; input.value = ''; $('reveal-token').textContent = t('showToken'); return;
  }
  if (!window.confirm(t('confirmRevealToken'))) return;
  $('reveal-token').disabled = true; error('');
  try {
    const result = await api('/api/config/github/reveal', {method:'POST'});
    input.value = result.token; input.type = 'text'; $('reveal-token').textContent = t('hideToken');
  } catch (e) { error(e.message); } finally { $('reveal-token').disabled = false; }
};
$('github-token').onkeydown = (event) => {
  if (event.key === 'Enter') { event.preventDefault(); saveToken(); }
};
$('token-config').ontoggle = () => {
  if (!$('token-config').open && $('github-token').type === 'text') {
    $('github-token').type = 'password'; $('github-token').value = '';
    $('reveal-token').textContent = t('showToken');
  }
};
$('unlimited-mode').onchange = async () => {
  const previous = $('unlimited-mode').dataset.enabled === 'true';
  const enabled = $('unlimited-mode').checked;
  $('unlimited-mode').disabled = true; error('');
  try {
    const result = await api('/api/config/github/unlimited', {method:'POST', body:JSON.stringify({enabled})});
    renderUnlimitedMode(result.enabled);
  } catch (e) { renderUnlimitedMode(previous); error(e.message); }
  finally { $('unlimited-mode').disabled = false; }
};
$('import').onclick = () => $('import-file').click();
$('import-file').onchange = async () => {
  const file = $('import-file').files[0];
  if (!file) return;
  error(''); $('import').disabled = true;
  try {
    const maxBytes = Number($('import-file').dataset.maxBytes);
    if (file.size > maxBytes) throw new Error(`导入文件不能超过 ${Math.floor(maxBytes / 1024 / 1024)} MB`);
    const task = await api('/api/imports', {method:'POST', body:await file.text()});
    taskId = task.id; generation++; page = 1; lastTask = null; lastResult = null;
    localStorage.setItem('explorer-task', taskId); await poll();
  } catch (e) { error(e.message); }
  finally { $('import').disabled = false; $('import-file').value = ''; }
};
function download(format) {
  if (!taskId) return;
  const anchor = document.createElement('a');
  anchor.href = `/api/tasks/${encodeURIComponent(taskId)}/export?format=${format}`;
  anchor.download = ''; document.body.append(anchor); anchor.click(); anchor.remove();
}
$('export-json').onclick = () => download('json');
$('export-csv').onclick = () => download('csv');
for (const action of ['cancel','retry']) $(action).onclick = async () => {
  $(action).disabled = true;
  try { await api(`/api/tasks/${taskId}/${action}`, {method:'POST'}); error(''); await poll(); }
  catch (e) { error(e.message); } finally { $(action).disabled = false; }
};
$('prev').onclick = () => { page--; refreshResults().catch(e => error(e.message)); };
$('next').onclick = () => { page++; refreshResults().catch(e => error(e.message)); };
$('sort').onchange = () => { page = 1; refreshResults().catch(e => error(e.message)); };
$('scale-scores').onchange = () => { if (lastResult) renderResults(lastResult); };
function localDateValue(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}
$('fork-within-year').onchange = () => {
  if ($('fork-within-year').checked) {
    const end = new Date();
    const start = new Date(end);
    start.setFullYear(start.getFullYear() - 1);
    $('fork-created-from').value = localDateValue(start);
    $('fork-created-to').value = localDateValue(end);
  } else {
    $('fork-created-from').value = '';
    $('fork-created-to').value = '';
  }
  page = 1; refreshResults().catch(e => error(e.message));
};
for (const id of ['account-created-from','account-created-to','fork-created-from','fork-created-to']) {
  $(id).onchange = () => {
    if (id.startsWith('fork-created-')) $('fork-within-year').checked = false;
    page = 1; refreshResults().catch(e => error(e.message));
  };
}
$('clear-filters').onclick = () => {
  $('search').value = '';
  for (const id of ['account-created-from','account-created-to','fork-created-from','fork-created-to']) $(id).value = '';
  $('fork-within-year').checked = false;
  page = 1; refreshResults().catch(e => error(e.message));
};
$('language').onchange = () => {
  language = $('language').value === 'en' ? 'en' : 'zh-CN';
  localStorage.setItem('explorer-language', language);
  applyLanguage();
};
$('theme-toggle').onclick = () => {
  theme = theme === 'dark' ? 'light' : 'dark';
  localStorage.setItem('explorer-theme', theme);
  applyTheme();
};
let searchTimer;
$('search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => {page=1;refreshResults().catch(e=>error(e.message));},250); };
applyLanguage();
const saved = localStorage.getItem('explorer-task');
if (saved && /^[a-f0-9]{32}$/.test(saved)) { taskId = saved; poll(); }
