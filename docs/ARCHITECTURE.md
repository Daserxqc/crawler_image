# 整体设计

公开检索与登录拆成两套平面。账号平面在 `tax_platform/accounts/`，由 `web.app` 通过 `mount_accounts` 挂路由。**crawler / normalize / store / search 不 import accounts**。

## 两套平面

```
公开平面（无登录）                      账号平面
-------------------------              -------------------------
采集 / 清洗 / 入库 / 检索               tax_platform/accounts/
公告 / 变动流 / 岗位 / 科室             邮箱或密码登录、关注、邮件队列
任何人打开即用                         只读订阅 appointment_events；SMTP 可选
```

账号路由挂在同一 FastAPI 进程（`/login`、`/account`、`/api/auth/*`、`/api/watches/*`），业务边界单向：

```
官网 → crawler → normalize → store → search / web
                                      ↑
                               accounts 只读订阅 + 写自己的表
```

`/watches` 仅作兼容跳转，实际页面是 `/account#watches`。

## 目录

```
tax_platform/
  config/          站点清单、调度、市局 registry、URL 发现与归一
  models/          实体契约
  crawler/         HTTP、页面解析、按站点 crawl job、站点适配
  normalize/       职务、科室、人名、变动类型
  store/           SQLite 入库、现任、岗位档案、异常
  search/          检索、变动流、公告、导出
  web/             FastAPI + static/ 前端页
  accounts/        登录、关注、邀请、邮件
scripts/           CLI：抓取、入库、修复、运维
tests/             单元测试
docs/              架构、抓取踩坑、云同步
output/            运行时数据（gitignore）：tax_hr.db、registry
```

`crawler/` 内部分层：

```
http_client.py           请求 / URL / META REFRESH / WAF 回退
appointment_*.py         任免列表、详情、条款解析
leader_intro.py          领导介绍解析
appointment_job.py       任免抓取编排（按站点）
leader_job.py            领导介绍抓取编排（按站点）
list_heads_job.py        列表头监控（新公告探测）
crawl_state.py           上次成功时间与到期判断
job_io.py                站点选择、JSON 落盘
notice_url.py            公告 URL 归一（去重）
站点适配                 sta_chinatax / shanghai_xxgk / fujian_was5 /
                         shanxi_son_list / xxgk_list / jpage
```

业务代码应调用 `crawl_appointments` / `crawl_leaders`，不要把抓取逻辑写在 `scripts/` 里。

## 覆盖与刷新

站点由代码内置清单与 `output/city_sites_registry.json` 合并（见 `config/sites.py`）。数量以本机 registry 为准，README「站点规模」一节有说明。

默认刷新周期：**各级均为 7 天**（`config/schedule.py`）。单站可用 `Site.refresh_days` 覆盖。到期抓取入口：

```powershell
python scripts/crawl_due_appointments.py          # 到期任免 + 入库（定时任务用这个）
python scripts/crawl_leaders.py --site all --due-only
python scripts/crawl_list_heads.py --site all
python scripts/crawl_due.py --kind leaders        # 仅列出到期站点，不抓
```

Windows 定时任务：`python scripts/install_windows_crawl_task.py`。

## 公开页怎么排

检索工具，不是介绍站。第一屏就是检索，不要仪表盘、卡片墙、统计条。

```
税局人事检索     人员查询  最新公告  变动流  科室穿透  岗位历任          [登录]
```

| 路由 | 这一屏只做一件事 |
| --- | --- |
| `/` | 检索：层级 / 科室 / 职务 / 姓名 / 时间；右侧出人与任职 |
| `/notices` | 任免公告列表 |
| `/changes` | 全站变动流 |
| `/departments` | 科室 → 分管领导 → 任职人员 |
| `/posts` | 按科室/职务检索岗位 |
| `/posts/view` | 某岗位现任与历任档案 |
| `/people/{id}` | 履历倒序，带来源链接 |
| `/login` | 登录 |
| `/account` | 个人中心与关注（需登录） |
| `/anomalies` | 数据异常扫描与修正（需登录） |
| `/admin/users` | 邀请与对外地址（管理员） |

首页结构：

```
[顶栏]
单位层级 ▾   科室 ▾   职务 ▾   姓名          时间从 ▾ 到 ▾    [检索]  [导出]
────────────────────────────────────────────────────────────────
结果列表：姓名 · 单位 · 科室 · 职务 · 现任/已免 · 生效日 · 来源
```

点姓名进履历，点来源打开官网原文。科室页和岗位页不进首页。
