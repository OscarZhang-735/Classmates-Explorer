const $ = (id) => document.getElementById(id);
const terminal = new Set(['completed', 'partial', 'failed', 'cancelled']);
const labels = {queued:'排队中',running:'查询中',waiting_rate_limit:'等待 GitHub 额度恢复',completed:'查询完成',partial:'部分完成',failed:'查询失败',cancelled:'已取消'};
let taskId = null, page = 1, timer = null, generation = 0, resultRequest = 0, pollRequest = 0;

async function api(url, options = {}) {
  const response = await fetch(url, {headers:{'Content-Type':'application/json'}, ...options});
  const body = await response.json();
  if (!response.ok) {
    const failure = new Error(typeof body.detail === 'string' ? body.detail : '请求参数无效');
    failure.status = response.status; throw failure;
  }
  return body;
}
function error(message) { $('error').textContent = message || ''; $('error').hidden = !message; }
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
function date(value) { return value ? new Date(value).toLocaleString('zh-CN') : '—'; }
function row(item) {
  const tr = document.createElement('tr');
  const repo = document.createElement('td');
  repo.append(link(item.name, item.url), text('small', `★ ${item.stars} · 创建 ${date(item.created_at)}`), text('small', `推送 ${date(item.pushed_at)}`));
  const owner = document.createElement('td'), profile = document.createElement('div'); profile.className = 'owner';
  const image = document.createElement('img'); image.className = 'avatar'; image.alt = ''; image.loading = 'lazy'; image.referrerPolicy = 'no-referrer';
  try { const url = new URL(item.owner.avatar_url); if (url.protocol === 'https:') image.src = url.href; } catch (_) {}
  profile.append(image, link(item.owner.login, item.owner.url));
  owner.append(profile, text('p', item.owner.name), text('span', item.owner.type, 'badge'));
  const info = document.createElement('td');
  info.append(text('p', item.owner.bio), text('small', [item.owner.company, item.owner.location].filter(Boolean).join(' · ') || '未填写公司或地区'));
  if (item.owner.website_url) info.append(link(item.owner.website_url, item.owner.website_url));
  info.append(text('small', `资料采集 ${date(item.owner.collected_at)}`));
  const contribution = document.createElement('td'), c = item.contribution;
  if (c.status === 'completed') {
    contribution.append(text('div', c.total.toLocaleString(), 'total'), text('p', `Commit ${c.commits} · PR ${c.pull_requests}`), text('p', `Issue ${c.issues} · Review ${c.reviews}`));
    if (c.restricted) contribution.append(text('small', `受限贡献 ${c.restricted}`));
    contribution.append(text('small', `采集 ${date(c.collected_at)}`));
  } else contribution.append(text('p', {pending:'等待获取',failed:'获取失败',not_applicable:'不适用'}[c.status] || '未知'));
  tr.append(repo, owner, info, contribution); return tr;
}
async function refreshResults(id = taskId, version = generation) {
  if (!id) return;
  const request = ++resultRequest;
  const params = new URLSearchParams({page, per_page:50, search:$('search').value, sort:$('sort').value});
  const result = await api(`/api/tasks/${id}/results?${params}`);
  if (id !== taskId || version !== generation || request !== resultRequest) return;
  $('results').replaceChildren(...result.items.map(row));
  if (!result.items.length) { const tr = document.createElement('tr'), td = text('td','暂无结果'); td.colSpan = 4; tr.append(td); $('results').append(tr); }
  $('page-label').textContent = `共 ${result.total} 条 · 第 ${page} / ${Math.max(1,Math.ceil(result.total/50))} 页`;
  $('prev').disabled = page <= 1; $('next').disabled = page * 50 >= result.total;
}
async function poll() {
  const id = taskId, version = generation, request = ++pollRequest; clearTimeout(timer);
  try {
    const task = await api(`/api/tasks/${id}`);
    if (id !== taskId || version !== generation || request !== pollRequest) return;
    $('task-panel').hidden = false;
    $('task-title').textContent = labels[task.status];
    const p = task.progress;
    $('progress').textContent = `一级 Fork ${p.forks_fetched} / ${task.total_direct_forks ?? '待查询'} · 所有者完成 ${p.owners_completed} / ${p.owners_total} · 失败 ${p.owners_failed}`;
    $('interval').textContent = `全站贡献区间：${task.from.slice(0,10)} 至 ${task.to.slice(0,10)}（UTC，当天零点截止）`;
    const notices = [];
    if (task.truncated) notices.push(`已按上限截断，仅展示最新 ${task.limit} 个一级 Fork，约 ${task.omitted_forks} 个未纳入。`);
    if (task.error) notices.push(task.error.message);
    if (task.resume_at) notices.push(`预计恢复：${date(task.resume_at * 1000)}`);
    if (task.status === 'cancelled') notices.push('已保留结果，可继续未完成部分。');
    $('task-notice').textContent = notices.join(' '); $('task-notice').hidden = !notices.length;
    $('cancel').hidden = terminal.has(task.status);
    $('retry').hidden = !(['partial','failed','cancelled'].includes(task.status) && task.retryable);
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
    taskId = task.id; generation++; page = 1;
    localStorage.setItem('explorer-task', taskId); await poll();
  } catch (e) { error(e.message); } finally { $('submit').disabled = false; }
});
for (const action of ['cancel','retry']) $(action).onclick = async () => {
  $(action).disabled = true;
  try { await api(`/api/tasks/${taskId}/${action}`, {method:'POST'}); error(''); await poll(); }
  catch (e) { error(e.message); } finally { $(action).disabled = false; }
};
$('prev').onclick = () => { page--; refreshResults().catch(e => error(e.message)); };
$('next').onclick = () => { page++; refreshResults().catch(e => error(e.message)); };
$('sort').onchange = () => { page = 1; refreshResults().catch(e => error(e.message)); };
let searchTimer;
$('search').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => {page=1;refreshResults().catch(e=>error(e.message));},250); };
const saved = localStorage.getItem('explorer-task');
if (saved && /^[a-f0-9]{32}$/.test(saved)) { taskId = saved; poll(); }
