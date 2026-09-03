# 能力边界与实现现状

> 早期按 PR 分期落地，下列能力均已实现。抓取细节见 [crawl-pitfalls-2026-09-01.md](crawl-pitfalls-2026-09-01.md)；架构见 [ARCHITECTURE.md](ARCHITECTURE.md)。

## 哪些要登录

**关注 + 邮件推送必须登录；公开检索与全站变动流不必登录。**

| 能力 | 是否需要身份 | 说明 |
| --- | --- | --- |
| 检索、履历、溯源、导出 | 否 | 公开政务数据聚合 |
| 公告列表、全站变动流 | 否 | 全员可见 |
| 岗位现任/历任 | 否 | 由任职记录聚合 |
| 关注单位/科室/岗位/人员 | 是 | `/account#watches` |
| 变动邮件推送 | 是 | 需邮箱；未配 SMTP 只写 `email_outbox` |
| 异常扫描与人工修正 | 是 | `/anomalies` |
| 邀请与对外地址 | 管理员 | `/admin/users` |

## 已落地

| 模块 | 入口 |
| --- | --- |
| 任免列表 / 详情 / 条款 | `crawler/appointment_*.py`，`scripts/crawl_appointments.py` |
| 领导介绍 | `crawler/leader_intro.py`，`scripts/crawl_leaders.py` |
| 职务/科室/人名/变动类型归一 | `normalize/` |
| 入库、现任、岗位档案 | `store/`，`scripts/ingest_crawl.py`、`recompute_tenure.py`、`rebuild_org_posts.py` |
| 增量到期调度 | `config/schedule.py`（默认 7 天），`scripts/crawl_due_appointments.py` |
| 检索 / 科室穿透 / 导出 | `GET /api/search`、`/api/departments/*`、`/api/export/*` |
| 变动流 / 岗位档案 | `GET /api/changes`、`/api/posts`、`/posts/view` |
| 最新公告 | `GET /api/notices`，`/notices` |
| 数据异常 | `store/anomalies.py`，`/anomalies`，`scripts/anomalies_hr.py` |
| 登录与关注 | `accounts/`：邮箱 OTP / 密码；`scripts/notify_watches.py` |
| 全国站点 | 总局 + 31 省局写死；地市/区县来自 `city_sites_registry.json` |

一次性手工入库、URL 补丁脚本（`ingest_*_manual.py`、`patch_*.py`）不是日常入口，日常抓取用上面的 crawl / ingest。

## 上海站点备忘（采集约定仍有效）

### 人事任免

- 市局列表：`/xxgk/rsxx/`（父栏目「人事信息」，内含任免）
- 区局列表：`/pdtax/xxgk/rsrm/`、`/hptax/xxgk/rsrm/` 等
- **详情 URL 必须保留列表路径尾斜杠**：相对链接 `./202606/t480619.html` 应解析为 `/pdtax/xxgk/rsrm/202606/t480619.html`
- 正文常见句式：文号、发文单位、发文日期 + `XX任…；任职试用期为一年。` / `免去XX的…职务。`

另有 7 个市局直属（稽查局、税务分局），配置在 `sites_shanghai.py` 的 `_SPECIALS`。

### 领导介绍

- `/xxgk/ldjj/`、`/pdtax/xxgk/ldjj/` 本身是 **META REFRESH 跳转页**
- 正文格式：`姓名，性别，民族，职务。` + `主持全面工作` / `分管工作` + 科室列表

### 采集侧必须处理

1. 列表基址统一补尾 `/`，再用 `urljoin`
2. 领导介绍入口跟随 `REFRESH` 到真实正文
3. 市局「人事信息」页混有招录等非任免条目，需标题过滤
4. 区局路径前缀不同（`pdtax`/`hptax`/…），用站点清单配置，不强行猜
