"""反检测隐身脚本模板（与 patchright 配合使用）。

核心原则（2026-08 实战结论）：
  - patchright 已在驱动层处理 navigator.webdriver / CDP Runtime.enable /
    命令行参数等泄漏，**不要再手工伪造 navigator 或硬件属性**。
  - 真实值自洽最安全；硬编码 "RTX 3060"、plugins 数量等反而是检测信号。
  - 本文件只保留 patchright 不覆盖的 JS 层兜底。

用法::

    page.add_init_script(STEALTH_JS)
    # 或者使用 BrowserSession(stealth=True)（默认已注入）
"""

# ── 核心隐身脚本 ─────────────────────────────────────
STEALTH_JS = """(() => {
  // 1. 修复 iframe contentWindow 属性描述符（部分站点登录 iframe 依赖）
  var origDesc = Object.getOwnPropertyDescriptor(
    HTMLIFrameElement.prototype, 'contentWindow'
  );
  if (origDesc) {
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
      get: function() {
        return origDesc.get.call(this);
      },
    });
  }

  // 2. 绕过 Permissions API 检测（自动登录场景通知权限）
  if (navigator.permissions) {
    var origQuery = navigator.permissions.query;
    navigator.permissions.query = function(params) {
      if (params.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission });
      }
      return origQuery.call(navigator.permissions, params);
    };
  }

  // 3. chrome.runtime 兜底（个别站点检测扩展环境）
  if (window.chrome && !window.chrome.runtime) {
    window.chrome.runtime = {};
  }
})()"""


# ── Performance API 网络请求捕获 ───────────────────────
# 用于「新平台接口发现」：找出页面实际调用了哪些 JSON 接口。
NETWORK_CAPTURE_JS = """(() => {
  var entries = performance.getEntriesByType('resource');
  return entries
    .filter(function(e) {
      var lower = e.name.toLowerCase();
      return (lower.indexOf('/api/') !== -1 || lower.indexOf('/v1/') !== -1
        || lower.indexOf('/v2/') !== -1 || lower.indexOf('/v3/') !== -1
        || lower.indexOf('/x/') !== -1 || lower.indexOf('.json') !== -1
        || lower.indexOf('graphql') !== -1 || lower.indexOf('search') !== -1
        || lower.indexOf('feed') !== -1 || lower.indexOf('hot') !== -1
        || lower.indexOf('trending') !== -1 || lower.indexOf('list') !== -1)
        && lower.indexOf('.js') === -1 && lower.indexOf('.css') === -1
        && lower.indexOf('.png') === -1 && lower.indexOf('.jpg') === -1
        && lower.indexOf('.svg') === -1 && lower.indexOf('.woff') === -1;
    })
    .map(function(e) {
      return { url: e.name, method: 'GET', initiatorType: e.initiatorType };
    });
})()"""


# ── Pinia/Vuex Store 状态读取 ──────────────────────────
PINIA_STORE_JS = """(() => {
  var stores = [];
  try {
    var app = document.querySelector('#app');
    if (!app || !app.__vue_app__) return stores;
    var gp = app.__vue_app__.config && app.__vue_app__.config.globalProperties;

    var pinia = gp && gp.$pinia;
    if (pinia && pinia._s) {
      pinia._s.forEach(function(store, id) {
        var state = {};
        for (var k in store) {
          try {
            if (k.startsWith('$') || k.startsWith('_')) continue;
            if (typeof store[k] === 'function') continue;
            state[k] = store[k];
          } catch(e) {}
        }
        stores.push({ type: 'pinia', id: id, state: state });
      });
    }

    var vuex = gp && gp.$store;
    if (vuex && vuex._modules && vuex._modules.root && vuex._modules.root._children) {
      var children = vuex._modules.root._children;
      for (var modName in children) {
        if (children.hasOwnProperty(modName)) {
          stores.push({
            type: 'vuex',
            id: modName,
            state: children[modName].state || {},
          });
        }
      }
    }
  } catch(e) {}
  return stores;
})()"""


# ── 框架检测 ──────────────────────────────────────────
FRAMEWORK_DETECT_JS = """(() => {
    var r = {};
    try {
        var app = document.querySelector('#app');
        r.vue3 = !!(app && app.__vue_app__);
        r.vue2 = !!(app && app.__vue__);
        r.react = !!(window.__REACT_DEVTOOLS_GLOBAL_HOOK__) || !!document.querySelector('[data-reactroot]');
        r.nextjs = !!(window.__NEXT_DATA__);
        r.nuxt = !!(window.__NUXT__);
        if (r.vue3 && app.__vue_app__) {
            var gp = app.__vue_app__.config && app.__vue_app__.config.globalProperties;
            r.pinia = !!(gp && gp.$pinia);
            r.vuex = !!(gp && gp.$store);
        }
    } catch(e) {}
    return r;
})()"""


# ── __INITIAL_STATE__ 提取 ─────────────────────────────
INITIAL_STATE_JS = """(() => {
    var scripts = document.querySelectorAll('script:not([src])');
    for (var i = 0; i < scripts.length; i++) {
        var text = scripts[i].textContent || '';
        var idx = text.indexOf('__INITIAL_STATE__');
        if (idx !== -1) {
            try {
                return window.__INITIAL_STATE__;
            } catch(e) {}
        }
    }
    return null;
})()"""
