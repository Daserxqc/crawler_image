# -*- coding: utf-8 -*-
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tax_platform" / "web" / "static" / "account.html"

# fmt: off
PARTS = [
r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>\u4e2a\u4eba\u4e2d\u5fc3 - \u7a0e\u52a1\u4eba\u4e8b\u68c0\u7d22</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;600;700&family=ZCOOL+XiaoWei&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="/static/css/app.css?v=35" />
</head>
<body class="app" data-page="account">
  <header class="app-header" id="app-header"></header>
  <main class="app-main">
    <div class="shell shell-account">
      <section class="account-banner" aria-label="\u4e2a\u4eba\u4e2d\u5fc3">
        <div class="account-banner-inner">
          <div class="account-avatar-lg" id="account-avatar" aria-hidden="true"></div>
          <div class="account-banner-text">
            <p class="account-banner-name" id="account-display-name">\u52a0\u8f7d\u4e2d\u2026</p>
            <p class="account-banner-sub" id="account-account-line"></p>
            <p class="account-banner-meta" id="account-meta"></p>
          </div>
        </div>
      </section>
      <div class="account-layout">
        <aside class="account-sidebar">
          <nav class="account-nav" aria-label="\u5bfc\u822a">
            <button type="button" class="account-nav-item is-active" data-section="overview">\u6982\u89c8</button>
            <button type="button" class="account-nav-item" data-section="watches">\u6211\u7684\u5173\u6ce8 <span class="account-nav-badge" id="nav-badge-watches" hidden></span></button>
            <button type="button" class="account-nav-item" data-section="notify">\u53d8\u52a8\u901a\u77e5 <span class="account-nav-badge" id="nav-badge-notify" hidden></span></button>
            <button type="button" class="account-nav-item" data-section="settings">\u8d26\u53f7\u8bbe\u7f6e</button>
            <button type="button" class="account-nav-item" data-section="help">\u4f7f\u7528\u5e2e\u52a9</button>
          </nav>
        </aside>
        <div class="account-main">
          <section class="account-section panel-card is-active" id="section-overview">
            <header class="account-section-head">
              <h2 class="account-section-title">\u6982\u89c8</h2>
              <p class="account-section-lead">\u5173\u6ce8\u6570\u636e\u3001\u5feb\u6377\u5165\u53e3\u4e0e\u6700\u8fd1\u52a8\u6001\u3002</p>
            </header>
            <div class="account-stats account-stats-compact">
              <button type="button" class="stat-card account-stat-link" data-goto="watches"><div class="stat-value" id="stat-watches-total">0</div><div class="stat-label">\u5173\u6ce8\u603b\u6570</div></button>
              <button type="button" class="stat-card account-stat-link" data-goto="watches" data-filter="person"><div class="stat-value" id="stat-watches-person">0</div><div class="stat-label">\u5173\u6ce8\u4eba\u5458</div></button>
              <button type="button" class="stat-card account-stat-link" data-goto="watches" data-filter="org"><div class="stat-value" id="stat-watches-org">0</div><div class="stat-label">\u5173\u6ce8\u5355\u4f4d</div></button>
              <button type="button" class="stat-card account-stat-link" data-goto="notify"><div class="stat-value" id="stat-notify">0</div><div class="stat-label">\u53d8\u52a8\u901a\u77e5</div></button>
            </div>
            <div class="account-overview-tools">
              <a class="account-tool" href="/"><span aria-hidden="true">\u2315</span>\u4eba\u5458\u67e5\u8be2</a>
              <a class="account-tool" href="/changes"><span aria-hidden="true">\u21bb</span>\u53d8\u52a8\u6d41</a>
              <button type="button" class="account-tool" data-goto="watches"><span aria-hidden="true">\u2605</span>\u7ba1\u7406\u5173\u6ce8</button>
              <button type="button" class="account-tool" data-goto="notify"><span aria-hidden="true">\u2709</span>\u67e5\u770b\u901a\u77e5</button>
            </div>
            <h3 class="watch-subheading">\u6700\u8fd1\u52a8\u6001</h3>
            <div class="account-timeline" id="account-timeline"><div class="status muted">\u52a0\u8f7d\u4e2d\u2026</div></div>
          </section>
""",
r"""
          <section class="account-section panel-card" id="section-watches">
            <header class="account-section-head">
              <h2 class="account-section-title">\u6211\u7684\u5173\u6ce8</h2>
              <p class="account-section-lead">\u641c\u7d22\u4eba\u5458\u6216\u9009\u62e9\u5355\u4f4d\u5173\u6ce8\uff0c\u652f\u6301\u7b5b\u9009\u4e0e\u53d6\u6d88\u3002</p>
            </header>
            <div class="account-quick-add" id="account-quick-add">
              <h3 class="watch-add-heading">\u6dfb\u52a0\u5173\u6ce8</h3>
              <div class="login-method-tabs watch-add-tabs" role="tablist">
                <button type="button" class="login-method is-active" data-mode="person">\u5173\u6ce8\u4eba\u5458</button>
                <button type="button" class="login-method" data-mode="bureau">\u5173\u6ce8\u5355\u4f4d</button>
              </div>
              <div id="qa-panel-person" class="watch-add-panel">
                <form id="qa-person-form" class="login-form watch-person-form" autocomplete="off">
                  <div class="field"><label for="qa-person-q">\u59d3\u540d</label><input id="qa-person-q" type="search" placeholder="\u5982 \u5f20\u4e09" autocomplete="off" /></div>
                  <button class="btn login-primary watch-submit-btn" type="submit" id="qa-person-search-btn">\u641c\u7d22</button>
                </form>
                <div id="qa-person-hits" class="watch-pick-list" hidden></div>
              </div>
              <div id="qa-panel-bureau" class="watch-add-panel" hidden>
                <form id="qa-bureau-form" class="login-form watch-bureau-form" autocomplete="off">
                  <div class="watch-bureau-fields">
                    <div class="field"><label for="qa-bureau-level">\u5c42\u7ea7</label><select id="qa-bureau-level"><option value="">\u5168\u90e8</option></select></div>
                    <div class="field"><label for="qa-bureau-pick">\u5355\u4f4d</label><select id="qa-bureau-pick" required><option value="">\u8bf7\u9009\u62e9\u5355\u4f4d</option></select></div>
                  </div>
                  <button class="btn login-primary watch-submit-btn" type="submit">\u5173\u6ce8\u6b64\u5355\u4f4d</button>
                </form>
              </div>
              <p class="login-status" id="qa-status" role="status"></p>
            </div>
            <div class="account-toolbar">
              <input id="watch-filter-q" type="search" class="account-filter-input" placeholder="\u641c\u7d22\u5173\u6ce8\u540d\u79f0" />
              <div class="account-filter-chips">
                <button type="button" class="account-chip is-active" data-watch-filter="all">\u5168\u90e8</button>
                <button type="button" class="account-chip" data-watch-filter="person">\u4eba\u5458</button>
                <button type="button" class="account-chip" data-watch-filter="org">\u5355\u4f4d</button>
              </div>
            </div>
            <div id="account-watch-list" class="account-watch-list"><div class="status muted">\u52a0\u8f7d\u4e2d\u2026</div></div>
          </section>
""",
r"""
          <section class="account-section panel-card" id="section-notify">
            <header class="account-section-head">
              <h2 class="account-section-title">\u53d8\u52a8\u901a\u77e5</h2>
              <p class="account-section-lead">\u5173\u6ce8\u5bf9\u8c61\u6709\u65b0\u4efb\u514d\u65f6\u4f1a\u5728\u6b64\u7559\u4e0b\u63d0\u9192\u3002\u90ae\u7bb1\u767b\u5f55\u7528\u6237\u8fd8\u4f1a\u5c1d\u8bd5\u6536\u5230\u90ae\u4ef6\u3002</p>
            </header>
            <p class="account-notify-legend">\u72b6\u6001\u8bf4\u660e\uff1a\u672c\u9875\u59cb\u7ec8\u4fdd\u7559\u63d0\u9192\u5185\u5bb9\u3002\u300c\u7b49\u5f85\u53d1\u90ae\u4ef6\u300d= \u6392\u961f\u4e2d\uff1b\u300c\u90ae\u4ef6\u5df2\u53d1\u51fa\u300d= \u5df2\u9001\u8fbe\u90ae\u7bb1\uff1b\u300c\u90ae\u4ef6\u672a\u53d1\u51fa\u300d= \u672a\u9001\u8fbe\uff0c\u53ef\u5728\u6b64\u67e5\u770b\u3002</p>
            <div class="account-filter-chips account-notify-filters">
              <button type="button" class="account-chip is-active" data-notify-filter="all">\u5168\u90e8</button>
              <button type="button" class="account-chip" data-notify-filter="pending">\u7b49\u5f85\u53d1\u90ae\u4ef6</button>
              <button type="button" class="account-chip" data-notify-filter="sent">\u90ae\u4ef6\u5df2\u53d1\u51fa</button>
              <button type="button" class="account-chip" data-notify-filter="failed">\u90ae\u4ef6\u672a\u53d1\u51fa</button>
            </div>
            <div id="account-notify-list" class="account-notify-list"><div class="status muted">\u52a0\u8f7d\u4e2d\u2026</div></div>
            <div class="account-panel-foot"><button type="button" class="btn secondary" id="notify-load-more" hidden>\u52a0\u8f7d\u66f4\u591a</button></div>
          </section>
""",
r"""
          <section class="account-section panel-card" id="section-settings">
            <header class="account-section-head">
              <h2 class="account-section-title">\u8d26\u53f7\u8bbe\u7f6e</h2>
              <p class="account-section-lead">\u4fee\u6539\u6635\u79f0\u3001\u67e5\u770b\u767b\u5f55\u4fe1\u606f\u3002</p>
            </header>
            <div class="account-profile-card">
              <h3 class="watch-subheading">\u4fee\u6539\u6635\u79f0</h3>
              <p class="muted account-nickname-hint" id="nickname-hint"></p>
              <form id="nickname-form" class="account-nickname-form" autocomplete="off">
                <div class="field"><label for="nickname-input">\u6635\u79f0</label><input id="nickname-input" type="text" maxlength="16" placeholder="\u5982\uff1a\u7a0e\u52a1\u5c0f\u674e" /></div>
                <button type="submit" class="btn login-primary" id="nickname-save-btn">\u4fdd\u5b58\u6635\u79f0</button>
                <p class="login-status" id="nickname-status" role="status"></p>
              </form>
            </div>
            <div class="account-profile-card">
              <h3 class="watch-subheading">\u8d26\u53f7\u4fe1\u606f</h3>
              <dl class="account-profile-facts">
                <div><dt>\u5f53\u524d\u6635\u79f0</dt><dd id="setting-nickname">\u2014</dd></div>
                <div><dt>\u767b\u5f55\u8d26\u53f7</dt><dd id="setting-account">\u2014</dd></div>
                <div><dt>\u767b\u5f55\u65b9\u5f0f</dt><dd id="setting-method">\u2014</dd></div>
                <div><dt>\u6ce8\u518c\u65f6\u95f4</dt><dd id="setting-created">\u2014</dd></div>
                <div><dt>\u4e0a\u6b21\u767b\u5f55</dt><dd id="setting-last-login">\u2014</dd></div>
                <div><dt>\u4fdd\u6301\u767b\u5f55\u81f3</dt><dd id="setting-session">\u2014</dd></div>
              </dl>
            </div>
            <div class="account-profile-card">
              <h3 class="watch-subheading">\u901a\u77e5\u8bf4\u660e</h3>
              <ul class="account-help-list compact">
                <li>\u5173\u6ce8\u540e\u4ec5\u5bf9\u65b0\u4efb\u514d\u751f\u6548\u3002</li>
                <li>\u624b\u673a\u767b\u5f55\u8bf7\u5728\u672c\u9875\u67e5\u770b\u901a\u77e5\uff1b\u90ae\u7bb1\u767b\u5f55\u53ef\u540c\u65f6\u6536\u90ae\u4ef6\u3002</li>
              </ul>
            </div>
            <div class="account-profile-card account-danger-card">
              <button type="button" class="btn secondary" id="account-rebind">\u9000\u51fa\u5e76\u6362\u7ed1</button>
              <button type="button" class="btn danger" id="account-logout">\u9000\u51fa\u767b\u5f55</button>
            </div>
          </section>
          <section class="account-section panel-card" id="section-help">
            <header class="account-section-head">
              <h2 class="account-section-title">\u4f7f\u7528\u5e2e\u52a9</h2>
              <p class="account-section-lead">\u5e38\u89c1\u64cd\u4f5c\u4e0e\u95ee\u9898\u89e3\u7b54\u3002</p>
            </header>
            <div class="account-help-grid">
              <section class="account-help-block">
                <h3 class="watch-subheading">\u5982\u4f55\u5173\u6ce8</h3>
                <ol class="account-help-steps">
                  <li>\u5728\u300c\u6211\u7684\u5173\u6ce8\u300d\u641c\u7d22\u59d3\u540d\uff0c\u6216\u6309\u5c42\u7ea7\u9009\u62e9\u5355\u4f4d\u540e\u70b9\u51fb\u5173\u6ce8\u3002</li>
                  <li>\u5728 <a href="/">\u4eba\u5458\u67e5\u8be2</a> \u6253\u5f00\u67d0\u4eba\u5c65\u5386\uff0c\u70b9\u300c\u5173\u6ce8\u6b64\u4eba\u300d\u3002</li>
                  <li>\u5728\u68c0\u7d22\u7ed3\u679c\u6309\u5730\u533a\u7b5b\u9009\u540e\uff0c\u53ef\u5173\u6ce8\u5f53\u524d\u5355\u4f4d\u6216\u79d1\u5ba4\u3002</li>
                </ol>
              </section>
              <section class="account-help-block">
                <h3 class="watch-subheading">\u53d8\u52a8\u901a\u77e5\u600e\u4e48\u7528</h3>
                <ol class="account-help-steps">
                  <li>\u5173\u6ce8\u540e\uff0c\u53ea\u5bf9<strong>\u65b0\u51fa\u73b0\u7684\u4efb\u514d</strong>\u63d0\u9192\uff0c\u5386\u53f2\u8bb0\u5f55\u4e0d\u4f1a\u8865\u53d1\u3002</li>
                  <li>\u6240\u6709\u63d0\u9192\u90fd\u4fdd\u7559\u5728\u300c\u53d8\u52a8\u901a\u77e5\u300d\uff1b\u70b9\u5f00\u53ef\u67e5\u770b\u6458\u8981\u5168\u6587\u3002</li>
                  <li>\u7528\u90ae\u7bb1\u767b\u5f55\u65f6\uff0c\u7cfb\u7edf\u8fd8\u4f1a\u5c1d\u8bd5\u628a\u6458\u8981\u53d1\u5230\u90ae\u7bb1\uff1b\u624b\u673a\u767b\u5f55\u8bf7\u5728\u672c\u9875\u67e5\u770b\u3002</li>
                  <li>\u300c\u7b49\u5f85\u53d1\u90ae\u4ef6\u300d\u300c\u90ae\u4ef6\u5df2\u53d1\u51fa\u300d\u300c\u90ae\u4ef6\u672a\u53d1\u51fa\u300d\u8868\u793a\u90ae\u4ef6\u6295\u9012\u72b6\u6001\uff0c\u4e0d\u5f71\u54cd\u672c\u9875\u67e5\u770b\u3002</li>
                </ol>
              </section>
              <section class="account-help-block">
                <h3 class="watch-subheading">\u8d26\u53f7\u4e0e\u6635\u79f0</h3>
                <ul class="account-help-list compact">
                  <li>\u672c\u7ad9\u4f7f\u7528\u624b\u673a\u53f7\u6216\u90ae\u7bb1\u9a8c\u8bc1\u7801\u767b\u5f55\uff0c\u65e0\u9700\u8bb0\u5bc6\u7801\u3002</li>
                  <li>\u672a\u8bbe\u7f6e\u6635\u79f0\u65f6\uff0c\u7cfb\u7edf\u81ea\u52a8\u79f0\u547c\u60a8\u4e3a\u300c\u7528\u6237xxxx\u300d\u6216\u300c\u7a0e\u52a1\u7528\u6237xxxx\u300d\u3002</li>
                  <li>\u5728\u300c\u8d26\u53f7\u8bbe\u7f6e\u300d\u53ef\u4fee\u6539\u6635\u79f0\uff1b\u7559\u7a7a\u4fdd\u5b58\u5219\u6062\u590d\u7cfb\u7edf\u9ed8\u8ba4\u3002</li>
                  <li>\u6362\u7ed1\u624b\u673a\u53f7\u6216\u90ae\u7bb1\u9700\u9000\u51fa\u540e\u91cd\u65b0\u9a8c\u8bc1\uff1b\u539f\u8d26\u53f7\u7684\u5173\u6ce8\u8bb0\u5f55\u4e0d\u4f1a\u8fc1\u79fb\u3002</li>
                </ul>
              </section>
              <section class="account-help-block">
                <h3 class="watch-subheading">\u5e38\u89c1\u95ee\u9898</h3>
                <dl class="account-faq">
                  <div><dt>\u516c\u5f00\u68c0\u7d22\u9700\u8981\u767b\u5f55\u5417\uff1f</dt><dd>\u4e0d\u9700\u8981\u3002\u767b\u5f55\u4ec5\u7528\u4e8e\u5173\u6ce8\u4e0e\u53d8\u52a8\u901a\u77e5\u3002</dd></div>
                  <div><dt>\u4e3a\u4ec0\u4e48\u6ca1\u6536\u5230\u90ae\u4ef6\uff1f</dt><dd>\u8bf7\u5148\u5728\u300c\u53d8\u52a8\u901a\u77e5\u300d\u67e5\u770b\u662f\u5426\u6709\u8bb0\u5f55\uff1b\u624b\u673a\u767b\u5f55\u672c\u5c31\u4e0d\u53d1\u90ae\u4ef6\u3002</dd></div>
                  <div><dt>\u5173\u6ce8\u540e\u600e\u4e48\u6ca1\u53cd\u5e94\uff1f</dt><dd>\u53ea\u5bf9\u5173\u6ce8\u4e4b\u540e\u7684\u65b0\u4efb\u514d\u751f\u6548\uff0c\u4e14\u6570\u636e\u66f4\u65b0\u53ef\u80fd\u6709\u77ed\u6682\u5ef6\u8fdf\u3002</dd></div>
                  <div><dt>\u600e\u4e48\u53d6\u6d88\u5173\u6ce8\uff1f</dt><dd>\u5728\u300c\u6211\u7684\u5173\u6ce8\u300d\u5217\u8868\u4e2d\u70b9\u300c\u53d6\u6d88\u5173\u6ce8\u300d\u5373\u53ef\u3002</dd></div>
                </dl>
              </section>
            </div>
          </section>
        </div>
      </div>
    </div>
  </main>
  <script src="/static/js/common.js?v=22"></script>
  <script src="/static/js/account.js?v=7"></script>
</body>
</html>
""",
]
# fmt: on

html = "".join(PARTS).encode("utf-8").decode("unicode_escape")
OUT.write_text(html, encoding="utf-8", newline="\n")
for needle in ("个人中心", "当前昵称", "用户xxxx"):
    assert needle in html, needle
print("OK", OUT, len(html))
