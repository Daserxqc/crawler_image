# 整体设计

公开检索与登录拆成两套平面。公开平面已落地；账号平面（登录 / 关注 / 邮件）在 `tax_platform/accounts/`，由 `web.app` 通过 `mount_accounts` 挂路由，**crawler / normalize / store / search 不 import accounts**。

## 两套平面

```
公开平面（无登录）                      账号平面（PR10）
-------------------------              -------------------------
采集 / 清洗 / 入库 / 检索               tax_platform/accounts/
公开变动流 / 岗位现任历任 / 导出        邮箱 OTP 登录、关注单位或人、邮件队列
任何人打开即用                         只读订阅 appointment_events；SMTP 可选
```

账号路由挂在同一 FastAPI 进程（`/login`、`/watches`、`/api/auth/*`、`/api/watches/*`），但业务代码边界仍单向：

```
官网 → crawler → normalize → store → search / web
                                      ↑
                               accounts 只读订阅 + 写自己的表
```

## 目录

```
tax_platform/
  config/          数据源清单
  models/          实体契约
  crawler/         抓取：HTTP、页面解析、按站点 crawl job
  normalize/       职务、科室归一（后续）
  store/           SQLite（后续）
  search/          组合筛选（后续）
  web/             公开页（后续）
  accounts/        登录与关注（预留）
scripts/           薄 CLI，只调 crawler job 并写 output/
```

`crawler/` 内部分层：

```
http_client.py           请求 / URL / META REFRESH
appointment_*.py         任免列表、详情、条款解析
leader_intro.py          领导介绍解析
appointment_job.py       任免抓取编排（按站点）
leader_job.py            领导介绍抓取编排（按站点）
crawl_state.py           上次成功时间与到期判断
job_io.py                站点选择、JSON 落盘
```

业务代码应调用 `crawl_appointments` / `crawl_leaders`，不要把抓取逻辑写在 `scripts/` 里。

## 覆盖范围（当前 vs 目标）

| 层级 | 更新频率 | 当前状态 |
| --- | --- | --- |
| 总局 headquarters | 90 天 | 未入库（页面结构与地方局不同，需单独适配） |
| 省局 / 直辖市局 province | 30 天 | 仅上海 |
| 地市局 city | 14 天 | 未入库 |
| 区县局 district | 7 天 | 仅上海 16 区 |

全国领导不是一次 `--site all` 就能抓全的：要先按省扩站点清单，再按站点模板适配解析。到期调度已按上表层级生效。

```powershell
python scripts/crawl_due.py --kind leaders
python scripts/crawl_leaders.py --site all --due-only
python scripts/crawl_appointments.py --site all --due-only --level district
```

## 公开页怎么排

这是检索工具，不是介绍站。第一屏就是检索，不要仪表盘、卡片墙、统计条。

顶栏固定四个入口，登录（以后）靠右、不进主流程：

```
税局人事检索          检索   变动   科室   岗位          [登录]
```

| 路由 | 这一屏只做一件事 |
| --- | --- |
| `/` | 检索：左侧筛单位层级→科室（联动）、职务、姓名、时间；右侧出人/任职列表 |
| `/people/{id}` | 履历倒序，每条带公告原文链接 |
| `/changes` | 全站变动流，按时间 / 地区 / 类型筛 |
| `/departments` | 当前层级下的科室；点科室看出分管领导 |
| `/posts/{id}` | 某岗位现任与历任 |

首页结构（自上而下一条线）：

```
[顶栏]
单位层级 ▾   科室 ▾   职务 ▾   姓名          时间从 ▾ 到 ▾    [检索]  [导出]
────────────────────────────────────────────────────────────────
结果列表：姓名 · 单位 · 科室 · 职务 · 现任/已免 · 生效日 · 来源
```

点姓名进履历，点来源打开官网原文。科室页和岗位页不进首页。

## 本阶段实现顺序

1. 任免列表 + 详情 + 任/免条款
2. 领导介绍解析（入口 META REFRESH + 简介块 + 分管科室）（本批）
3. 清洗入库、检索与上面这些公开页
4. 最后才接 `accounts/`
