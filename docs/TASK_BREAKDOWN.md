# 税务机关人事搜索平台 — 需求拆解与任务清单

## 功能三是否需要登录？

结论：**关注 + 邮件推送必须登录；公开检索与全站变动流不必登录。**

| 能力 | 是否需要用户身份 | 说明 |
| --- | --- | --- |
| 一键检索、履历、溯源、导出 | 否 | 公开政务数据聚合 |
| 全站变动动态流 | 否 | 全员可见的公开流 |
| 岗位现任/历任汇总 | 否 | 可由任职记录聚合 |
| 按单位/人员设置关注 | 是 | 属于个人数据 |
| 变动邮件推送 | 是 | 需要邮箱与订阅偏好 |

实施建议：
1. 先做无登录的采集 + 检索 + 公开动态流（覆盖需求一、二及功能三的公开部分）。
2. 关注/邮件作为独立模块，后续加轻量账号（邮箱登录或 OAuth）再接。

---

## 示例链接实测结论（上海）

### 人事任免
- 市局列表可用：`/xxgk/rsxx/`（父栏目「人事信息」，内含任免列表）
- 区局列表可用：`/pdtax/xxgk/rsrm/`、`/hptax/xxgk/rsrm/` 等
- **详情 URL 必须保留列表路径尾斜杠**：相对链接 `./202606/t480619.html` 应解析为  
  `/pdtax/xxgk/rsrm/202606/t480619.html`  
  若解析成 `/pdtax/xxgk/202606/...` 会 404
- 正文格式与图1一致：文号、发文单位、发文日期 + `XX任…；任职试用期为一年。` / `免去XX的…职务。`

### 领导介绍
- `/xxgk/ldjj/`、`/pdtax/xxgk/ldjj/` 本身是 **META REFRESH 跳转页**，不是正文
- 浦东实际正文：`/pdtax/xxgk/ldjj/201811/t442819.html`
- 市局实际栏目：`/xxgk/ldjj/ld_29881/`
- 正文格式与图2一致：`姓名，性别，民族，职务。` + `主持全面工作` / `分管工作` + 科室列表

### 采集侧必须处理的坑
1. 列表基址统一补尾 `/`，再用 `urljoin`
2. 领导介绍入口跟随 `REFRESH` 到真实正文
3. 市局「人事信息」页混有招录等非任免条目，需标题过滤
4. 区局路径前缀不同（`pdtax`/`hptax`/…），用站点清单配置，不强行猜

---

## PR 拆分任务清单（约 22 个小点）

### PR1 — 平台骨架与数据契约（本 PR）
1. 项目目录与模块边界
2. 上海省市局 + 区局数据源清单（任免/领导 URL）
3. 核心实体与字段契约（公告、任职、人员、科室、变动）
4. HTTP 客户端与 URL 规范化（尾斜杠、meta refresh）

### PR2 — 人事任免采集（本 PR）
5. 任免列表页解析（标题、日期、详情链接）
6. 任免详情正文提取（文号、发文单位、发文日期、正文）
7. 任免语句字段抽取（姓名、单位、科室、职务、试用期、任/免）

### PR3 — 领导介绍采集（本 PR）
8. 领导介绍入口跳转解析
9. 领导简介块解析（姓名、性别、民族、现任职务）
10. 分管科室列表解析与领导-科室关联

### PR4 — 清洗与标准化（本 PR）
11. 职务归一化（去掉主持工作、副司长级等修饰）
12. 科室层级归一化（处/科/司/局/所/分局）
13. 层级-科室映射表（市局处 ≠ 区局科）

### PR5 — 变动识别与增量
14. 变动类型判定（新任职/调任/晋升/免职/退休/试用期满转正等）— `normalize/change.py`
15. 增量入库与现任状态重算 — 公告级 skip（`appointment_job` + `--db`）、`persons.is_current` 落库（`tenure.recompute_*`）
16. 履历时间线更新触发 — ingest / `scripts/recompute_tenure.py` / 重抽任免后重算

### PR9 — 公开变动监测（API 完成；前端未做）
23. 全站变动动态流 — `GET /api/changes`、`scripts/changes_hr.py feed`
24. 岗位现任/历任档案 — `GET /api/posts`、`scripts/changes_hr.py post`

### 全国站点扩展
- 总局 + 31 省级税务局 URL 模板（`config/sites_provinces.py`）
- 地市/区县发现骨架：`config/city_discovery.py`、`scripts/discover_city_sites.py` → `output/city_sites_registry.json`（并入 `sites.ALL_SITES`）
- 空分管摸底：`scripts/report_empty_oversight.py`
- 地市/区县：registry 待持续填充；上海 16 区仍为手工样板

### PR6 — 异常与人工修正（API 完成；管理 UI 未做）
17. 异常识别（同名、缺字段、日期异常、冲突、解析失败）— `store/anomalies.py`、`POST /api/anomalies/scan`
18. 人工修正入口与修正回写钩子 — `POST /api/corrections`、`scripts/anomalies_hr.py`

### PR7 — 检索核心（API 完成；前端未做）
19. 多维组合筛选（层级/科室/职务/姓名/时间）— `GET /api/search`、`scripts/search_hr.py`
20. 层级-科室联动下拉数据接口 — `GET /api/departments/suggest`、`/api/titles/suggest`

### PR8 — 关联穿透与展示（API 完成；前端未做）
21. 科室↔分管领导联动、向上穿透 — `/api/departments/lookup`、`/api/departments/penetrate`
22. 履历倒序、公告溯源链接、列表/详情、Excel 导出 — `/api/people/{id}`、`/api/export/*`

启动：`python scripts/run_api.py`（文档见 `/docs`）

### PR10 — 个人关注与邮件（需登录）
25. 用户账号与关注设置 — 邮箱 OTP 登录（`accounts/auth.py`）、关注人员/单位（`/watches`、人员页「关注」）
26. 变动邮件推送（前后对比 + 原文链接）— `accounts/notify.py`、`scripts/notify_watches.py`；未配 SMTP 时写入 `email_outbox`（dry_run）

异常管理页：`/anomalies`（扫描 / 忽略 / 姓名修正）

---

## 建议技术边界（首期）

- 语言：Python
- 存储：先 SQLite（本地可跑），表结构按实体拆分，后续可换 Postgres
- Web：后续 PR 再加 FastAPI + 简单前端；本 PR 只定契约与采集底座
- 爬取范围首期：上海市局 + 全市区局（你给的清单）
