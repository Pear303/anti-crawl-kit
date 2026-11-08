// 通用登录检测（起点，不是万能）
// 返回 true = 已登录；false = 未登录/登录墙。
//
// 用法：
//   python examples/08_logged_in_encrypted.py --login-js examples/platforms/login_check_generic.js ...
//
// 接新平台时，优先改成平台自己的锚点：
//   - B站：SESSDATA + 顶栏头像
//   - 小红书：web_session + .user-info
//   - 知乎：z_c0 + 头像
//   - 抖音：sessionid + 右上角头像
// 不要只判断 cookie 存在，cookie 可能是过期的。
() => {
  function visible(el) {
    if (!el) return false;
    var r = el.getBoundingClientRect();
    if (r.width <= 2 || r.height <= 2) return false;
    var s = window.getComputedStyle(el);
    if (!s) return false;
    return s.display !== 'none' && s.visibility !== 'hidden' && parseFloat(s.opacity) !== 0;
  }

  // 明显的登录/注册按钮 → 未登录
  var loginSelectors = [
    'a[href*="login"]',
    'a[href*="signin"]',
    'button[class*="login"]',
    '[class*="login-btn"]',
    '[class*="LoginButton"]',
    '[data-testid*="login"]'
  ];
  for (var i = 0; i < loginSelectors.length; i++) {
    var btn = document.querySelector(loginSelectors[i]);
    if (btn && visible(btn)) return false;
  }

  // 头像/用户菜单 → 已登录
  var avatarSelectors = [
    'img[class*="avatar"]',
    '[class*="avatar"] img',
    '[class*="user-info"] img',
    '[class*="userInfo"] img',
    'header img[src*="avatar"]',
    'a[href*="/user/"] img',
    'a[href*="/profile/"] img'
  ];
  for (var j = 0; j < avatarSelectors.length; j++) {
    var img = document.querySelector(avatarSelectors[j]);
    if (img && visible(img)) return true;
  }

  // 兜底：页面上出现「退出登录」「我的主页」等入口
  var text = document.body ? (document.body.innerText || '') : '';
  if (text.indexOf('退出登录') !== -1 || text.indexOf('我的主页') !== -1 ||
      text.indexOf('个人中心') !== -1) {
    return true;
  }
  return false;
}
