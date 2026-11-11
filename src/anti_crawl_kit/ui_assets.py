"""``ack-ui`` 的前端页面（内嵌字符串，不引入构建步骤）。

页面只做四件事：列配方、填参数、运行、看日志和报告。没有拖拽编辑器，
也没有选择器点选——那些属于另一个产品。
"""

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ack-ui · 本地控制台</title>
<style>
:root {
  --bg: #f5f6f8; --panel: #fff; --text: #1f2328; --muted: #656d76;
  --line: #e3e6ea; --ok: #1a7f37; --warn: #9a6700; --fail: #cf222e;
  --info: #0969da; --radius: 10px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1216; --panel: #171b21; --text: #e6edf3; --muted: #9aa4b2;
    --line: #2a313a; --ok: #4ac26b; --warn: #d29922; --fail: #ff7b72;
    --info: #6cb6ff;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans SC",
        "PingFang SC", "Microsoft YaHei", sans-serif;
}
.top {
  display: flex; align-items: center; justify-content: space-between;
  padding: 14px 22px; border-bottom: 1px solid var(--line); background: var(--panel);
}
.brand { font-weight: 650; letter-spacing: 0.01em; }
.tag {
  font-family: ui-monospace, Consolas, monospace; font-size: 11px;
  color: var(--muted); border: 1px solid var(--line); border-radius: 999px;
  padding: 1px 8px; margin-left: 6px;
}
.status { font-size: 12px; color: var(--muted); }
.status.busy { color: var(--warn); }
.status.ok { color: var(--ok); }
.status.bad { color: var(--fail); }
.layout { display: grid; grid-template-columns: 260px 1fr; min-height: calc(100vh - 53px); }
.side { border-right: 1px solid var(--line); padding: 18px 14px; background: var(--panel); }
.side h2 { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; margin: 18px 0 8px; }
.side h2:first-child { margin-top: 0; }
.side ul { list-style: none; margin: 0; padding: 0; }
.side li { margin-bottom: 2px; }
.side button, .side a {
  display: flex; justify-content: space-between; gap: 8px; width: 100%;
  text-align: left; padding: 7px 9px; border: 0; border-radius: 7px;
  background: transparent; color: var(--text); font: inherit; font-size: 13px;
  cursor: pointer; text-decoration: none;
}
.side button:hover, .side a:hover { background: var(--bg); }
.side button.active { background: var(--bg); font-weight: 600; }
.side .meta { color: var(--muted); font-size: 11px; white-space: nowrap; }
.main { padding: 26px 30px 60px; max-width: 980px; }
.empty { color: var(--muted); padding: 60px 0; text-align: center; }
h1 { font-size: 22px; margin: 0 0 6px; font-weight: 650; }
.desc { color: var(--muted); margin: 0 0 18px; white-space: pre-wrap; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px 18px; margin-bottom: 14px; }
.card h3 { font-size: 13px; margin: 0 0 10px; color: var(--muted); font-weight: 500; }
.fields { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 10px; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.field input {
  padding: 7px 9px; border: 1px solid var(--line); border-radius: 7px;
  background: var(--bg); color: var(--text); font: inherit; font-size: 13px;
}
.field input:focus { outline: 2px solid var(--info); outline-offset: -1px; }
.row { display: flex; align-items: center; gap: 14px; margin-top: 14px; flex-wrap: wrap; }
.check { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); }
.btn {
  padding: 8px 16px; border-radius: 8px; border: 1px solid var(--text);
  background: var(--text); color: var(--bg); font: inherit; font-weight: 600;
  cursor: pointer; text-decoration: none; display: inline-block;
}
.btn:disabled { opacity: 0.5; cursor: default; }
.btn.ghost { background: transparent; color: var(--text); border-color: var(--line); font-weight: 500; }
.log {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-size: 12px;
  background: #10141a; color: #d6dee8; border-radius: 8px; padding: 12px;
  min-height: 160px; max-height: 420px; overflow: auto; white-space: pre-wrap;
  line-height: 1.7;
}
.log:empty::before { content: "运行后这里会显示步骤日志"; color: #6b7684; }
.log .t { color: #7d8794; }
.log .ok { color: #4ac26b; }
.log .fail { color: #ff7b72; }
.log .warn { color: #d29922; }
.log .info { color: #6cb6ff; }
.handoff { border: 1px solid var(--warn); border-radius: 8px; padding: 12px; margin-top: 12px; background: var(--panel); }
.handoff h3 { color: var(--warn); margin: 0 0 6px; font-size: 13px; }
.handoff textarea {
  width: 100%; margin: 8px 0; padding: 8px; border: 1px solid var(--line);
  border-radius: 7px; background: var(--bg); color: var(--text); font: inherit;
}
.steps { margin: 0; padding-left: 18px; color: var(--muted); font-size: 12px; }
.steps li { margin: 3px 0; }
.steps code { color: var(--text); }
a.link { color: var(--info); }
footer { color: var(--muted); font-size: 11px; margin-top: 30px; }
@media (max-width: 720px) {
  .layout { grid-template-columns: 1fr; }
  .side { border-right: 0; border-bottom: 1px solid var(--line); }
  .main { padding: 20px 16px 40px; }
}
</style>
</head>
<body>
<header class="top">
  <div class="brand">anti-crawl-kit <span class="tag">ack-ui</span></div>
  <div class="status" id="status">就绪</div>
</header>
<main class="layout">
  <aside class="side">
    <h2>配方</h2>
    <ul id="recipes"><li class="meta">加载中…</li></ul>
    <h2>历史报告</h2>
    <ul id="runs"><li class="meta">加载中…</li></ul>
  </aside>
  <section class="main" id="panel">
    <div class="empty">从左侧选一个配方，填参数，点运行。</div>
  </section>
</main>
<script>
const recipesEl = document.getElementById('recipes');
const runsEl = document.getElementById('runs');
const panel = document.getElementById('panel');
const statusEl = document.getElementById('status');

let current = null;
let jobId = null;
let source = null;

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = 'status' + (cls ? ' ' + cls : '');
}

async function api(path, options) {
  const res = await fetch(path, options);
  const text = await res.text();
  if (!res.ok) throw new Error(text || res.statusText);
  return text ? JSON.parse(text) : {};
}

async function loadRecipes() {
  try {
    const rows = await api('/api/recipes');
    if (!rows.length) {
      recipesEl.innerHTML = '<li class="meta">examples/recipes 下没有配方</li>';
      return;
    }
    recipesEl.innerHTML = rows.map(function (row) {
      const label = row.title || row.id || row.path;
      const meta = row.valid ? (row.steps + ' 步') : '无效';
      return '<li><button data-path="' + esc(row.path) + '"' +
        (row.valid ? '' : ' title="' + esc(row.error) + '"') + '>' +
        '<span>' + esc(label) + '</span><span class="meta">' + esc(meta) + '</span></button></li>';
    }).join('');
    recipesEl.querySelectorAll('button').forEach(function (btn) {
      btn.addEventListener('click', function () { selectRecipe(btn.dataset.path, btn); });
    });
  } catch (err) {
    recipesEl.innerHTML = '<li class="meta">' + esc(err.message) + '</li>';
  }
}

async function loadRuns() {
  try {
    const rows = await api('/api/runs');
    if (!rows.length) {
      runsEl.innerHTML = '<li class="meta">还没有 HTML 报告</li>';
      return;
    }
    runsEl.innerHTML = rows.slice(0, 30).map(function (row) {
      return '<li><a href="' + esc(row.url) + '" target="_blank">' +
        '<span>' + esc(row.name) + '</span><span class="meta">' + esc(row.kind) + '</span></a></li>';
    }).join('');
  } catch (err) {
    runsEl.innerHTML = '<li class="meta">' + esc(err.message) + '</li>';
  }
}

async function selectRecipe(path, btn) {
  recipesEl.querySelectorAll('button').forEach(function (b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  try {
    current = await api('/api/recipes/detail?path=' + encodeURIComponent(path));
    renderRecipe();
  } catch (err) {
    panel.innerHTML = '<div class="empty">' + esc(err.message) + '</div>';
  }
}

function renderRecipe() {
  const inputs = Object.entries(current.inputs || {}).map(function (entry) {
    const key = entry[0];
    const raw = entry[1];
    const spec = (raw && typeof raw === 'object') ? raw : { default: raw };
    const type = spec.type === 'number' ? 'number' : 'text';
    const required = spec.required ? ' required' : '';
    const value = spec.default == null ? '' : spec.default;
    return '<label class="field"><span>' + esc(key) +
      (spec.required ? ' *' : '') + '</span><input name="' + esc(key) +
      '" type="' + type + '" value="' + esc(value) + '"' + required + '></label>';
  }).join('');

  const steps = (current.steps || []).map(function (step) {
    const kind = step.kind === 'agent' ? ' <span class="meta">(agent)</span>' : '';
    return '<li><code>' + esc(step.id) + '</code> · ' + esc(step.uses || 'agent') + kind + '</li>';
  }).join('');

  const needsBrowser = (current.steps || []).some(function (step) {
    return String(step.uses || '').indexOf('browser_') === 0;
  });

  panel.innerHTML =
    '<h1>' + esc(current.title || current.id) + '</h1>' +
    '<p class="desc">' + esc(current.description || '') + '</p>' +
    '<div class="card"><h3>参数</h3>' +
    '<form id="run-form"><div class="fields">' + inputs + '</div>' +
    '<div class="row">' +
    '<label class="check"><input type="checkbox" id="with-browser"' +
    (needsBrowser ? ' checked' : '') + '> 启动浏览器</label>' +
    '<button class="btn" type="submit" id="run-btn">运行</button>' +
    '</div></form></div>' +
    '<div class="card"><h3>步骤</h3><ol class="steps">' + steps + '</ol></div>' +
    '<div class="card"><h3>日志</h3><div class="log" id="log"></div>' +
    '<div id="result"></div></div>' +
    '<footer>只监听本机；cookie 请用环境变量，不要贴进页面。</footer>';

  document.getElementById('run-form').addEventListener('submit', function (event) {
    event.preventDefault();
    runRecipe();
  });
}

async function runRecipe() {
  const form = document.getElementById('run-form');
  const inputs = {};
  new FormData(form).forEach(function (value, key) { inputs[key] = value; });
  const withBrowser = document.getElementById('with-browser').checked;
  const logEl = document.getElementById('log');
  logEl.textContent = '';
  document.getElementById('run-btn').disabled = true;
  setStatus('运行中…', 'busy');

  try {
    const data = await api('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recipe: current.path, inputs: inputs, with_browser: withBrowser })
    });
    jobId = data.job_id;
    watch(jobId);
  } catch (err) {
    logEl.innerHTML = '<span class="fail">' + esc(err.message) + '</span>';
    setStatus('启动失败', 'bad');
    document.getElementById('run-btn').disabled = false;
  }
}

function appendEvent(event) {
  const logEl = document.getElementById('log');
  if (!logEl) return;
  const time = (event.at || '').slice(11);
  let cls = 'info';
  let text = '';
  if (event.type === 'step_start') {
    text = '→ ' + event.step_id + '  (' + event.uses + ')';
  } else if (event.type === 'step_ok') {
    cls = 'ok';
    text = '✓ ' + event.step_id + '  ' + (event.elapsed_ms || 0) + 'ms';
  } else if (event.type === 'step_failed') {
    cls = 'fail';
    text = '✗ ' + event.step_id + '  ' + (event.error || '');
  } else if (event.type === 'step_skipped') {
    cls = 'warn';
    text = '- ' + event.step_id + '  跳过';
  } else if (event.type === 'needs_agent') {
    cls = 'warn';
    text = '? ' + event.step_id + '  需要人工判断';
  } else if (event.type === 'done') {
    cls = event.status === 'done' ? 'ok' : 'fail';
    text = '■ 结束：' + event.status + (event.error ? '  ' + event.error : '');
  } else {
    text = event.type;
  }
  const line = document.createElement('div');
  line.innerHTML = '<span class="t">' + esc(time) + '</span> ' +
    '<span class="' + cls + '">' + esc(text) + '</span>';
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
}

function watch(id) {
  if (source) source.close();
  source = new EventSource('/api/jobs/' + id + '/events');
  source.onmessage = function (message) {
    const event = JSON.parse(message.data);
    appendEvent(event);
    if (event.type === 'needs_agent') showHandoff(event);
    if (event.type === 'done') {
      source.close();
      source = null;
      finish(id);
    }
  };
  source.onerror = function () {
    // 服务端关闭或网络抖动：EventSource 会自己重连，这里不重复建连。
  };
}

async function finish(id) {
  const btn = document.getElementById('run-btn');
  if (btn) btn.disabled = false;
  try {
    const snapshot = await api('/api/jobs/' + id);
    const result = document.getElementById('result');
    if (!result) return;
    if (snapshot.status === 'needs_agent') {
      setStatus('等待判断', 'busy');
      return;
    }
    const cls = snapshot.status === 'done' ? 'ok' : 'bad';
    setStatus(snapshot.status === 'done' ? '完成' : '失败', cls);
    result.innerHTML = '<div class="row">' +
      '<a class="btn ghost" href="/api/jobs/' + id + '/report" target="_blank">打开 HTML 报告</a>' +
      '<span class="meta">' + esc(snapshot.error || '') + '</span></div>';
  } catch (err) {
    setStatus('读取结果失败', 'bad');
  }
}

function showHandoff(event) {
  const logEl = document.getElementById('log');
  const handoff = event.handoff || {};
  const box = document.createElement('div');
  box.className = 'handoff';
  box.innerHTML = '<h3>需要判断 · ' + esc(handoff.step_id || event.step_id) + '</h3>' +
    '<div>' + esc(handoff.prompt || '') + '</div>' +
    '<textarea id="handoff-value" rows="4" placeholder="按 schema 填 JSON，或写一句结论"></textarea>' +
    '<button class="btn" id="resume-btn">继续</button>';
  logEl.appendChild(box);
  document.getElementById('resume-btn').addEventListener('click', async function () {
    const raw = document.getElementById('handoff-value').value;
    let value = raw;
    try { value = JSON.parse(raw); } catch (err) { /* 不是 JSON 就按字符串传 */ }
    try {
      await api('/api/jobs/' + jobId + '/resume', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: value })
      });
      box.remove();
      setStatus('运行中…', 'busy');
      watch(jobId);
    } catch (err) {
      setStatus('继续失败：' + err.message, 'bad');
    }
  });
}

loadRecipes();
loadRuns();
</script>
</body>
</html>
"""
