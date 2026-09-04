# 税局人事检索平台

自动归集各级税务机关官网公开的人事任免与领导介绍，入库后提供检索、变动追踪、科室穿透与岗位档案查询。

> 数据来源均为官网已公开发布的信息；本系统仅做归集与检索，不替代组织人事正式档案。

## 功能概览

| 模块 | 路由 | 说明 |
| --- | --- | --- |
| 人员检索 | `/` | 按姓名、职务、科室、地区筛选；分页与现任统计；Excel/CSV **全量导出**（每人最新一条任免） |
| 最新公告 | `/notices` | 任免公告列表，按地区/时间/关键词筛选 |
| 公告归档 | `/notices/view` | 单条公告正文与本条任免人员（地区更新可跳转「最新公告」） |
| 变动流 | `/changes` | 任免事件时间线，可按科室、变动类型、关注项筛选 |
| 科室穿透 | `/departments` | 科室 → 分管领导 → 任职人员；支持领导反查分管 |
| 岗位历任 | `/posts` | 按科室/职务检索岗位，查看现任与历任档案 |
| 地区更新 | `/regions` | 按层级列出各地区最近任免数据日期 |
| 人员履历 | `/people/{id}` | 单人任职历史、领导简介链接 |
| 账号与关注 | `/login`、`/account` | 邮箱或密码登录；关注在 `/account#watches`（`/watches` 会跳转过来） |
| 数据异常 | `/anomalies` | 扫描与人工修正（需登录） |
| 管理 | `/admin/users` | 邀请链接、对外访问地址配置（需管理员） |

检索页面向手机做了紧凑列表布局；桌面仍为多列卡片。API 文档：启动服务后访问 `/docs`（OpenAPI）。

## 技术栈

- **语言**：Python 3.10+
- **Web**：FastAPI + 静态 HTML/JS（无前端构建）
- **存储**：SQLite（默认 `output/tax_hr.db`，路径锚定项目根，可用环境变量覆盖）
- **抓取**：`requests` + BeautifulSoup；部分站点需 Playwright/CDP 绕过 WAF
- **导出**：`openpyxl`（Excel）

## 快速开始

### 1. 环境

```powershell
cd crawler_image
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 需要浏览器回退抓取时（可选）
playwright install chromium
```

### 2. 数据

首次使用需有 SQLite 库。可从已有备份放入 `output/tax_hr.db`，或自行抓取入库：

```powershell
# 单站示例
python scripts/crawl_appointments.py --site shanghai --db output/tax_hr.db --output output/shanghai_appointments.json
python scripts/crawl_leaders.py --site shanghai --output output/shanghai_leaders.json
python scripts/ingest_crawl.py
python scripts/recompute_tenure.py
```

### 3. 启动 Web 服务

```powershell
python scripts/run_api.py
```

| 访问方式 | 地址 |
| --- | --- |
| 本机 | http://127.0.0.1:8000/ |
| 局域网 | http://\<本机 IPv4\>:8000/（默认监听 `0.0.0.0`） |
| 仅本机 | `python scripts/run_api.py --host 127.0.0.1` |

默认管理员（**部署后务必修改**）：

- 用户名：`admin`（环境变量 `TAX_HR_ADMIN_USER`）
- 密码：`TaxHR-Admin-ChangeMe`（环境变量 `TAX_HR_ADMIN_PASSWORD`）

### 阿里云部署

上云（ECS + systemd + 可选 Nginx + Linux 定时抓取）见 **[docs/deploy-aliyun.md](docs/deploy-aliyun.md)**。  
交给别人部署时先打交付包：`python scripts/pack_handoff.py`，说明见 **[docs/handoff-package.md](docs/handoff-package.md)**。

## 每周增量更新

各站点默认 **7 天** 到期重爬一次（`tax_platform/config/schedule.py`，单站可设 `refresh_days`）。到期窗口记在 `output/crawl_state.json`；未到期的站会跳过，已入库的公告 URL 也会跳过。

日常入口是 `crawl_due_appointments.py`：只爬到期任免列表并**直接入库**，不必再跑 `ingest_crawl.py`。

### 推荐：装成 Windows 计划任务

装一次后不用手跑。每天 03:30 检查一遍；电脑当时关机的话，下次开机后会补跑。

```powershell
python scripts/install_windows_crawl_task.py
```

| 项 | 值 |
| --- | --- |
| 任务名 | `TaxHR_AppointmentsDueCrawl` |
| 默认时间 | 每天 03:30（`--hour` / `--minute` 可改） |
| 执行 | `scripts/crawl_due_appointments.py --db output/tax_hr.db` |
| 日志 | `output/crawl_due_appointments.log` |

```powershell
python scripts/install_windows_crawl_task.py --hour 6 --minute 0   # 改到 06:00
python scripts/install_windows_crawl_task.py --remove              # 卸载
```

需本机已有 `output/tax_hr.db`，且计划任务用的 Python 能 import 本仓库（建议用当前 venv 的解释器执行安装脚本）。

### 云主机（Linux）：装 cron

```bash
python scripts/install_linux_crawl_cron.py --install
```

不要在云上使用 Windows 计划任务脚本。详见 [docs/deploy-aliyun.md](docs/deploy-aliyun.md) 第 12 步。

### 手动跑一次

```powershell
# 只爬到期站点并入库（最常用）
python scripts/crawl_due_appointments.py --db output/tax_hr.db

# 先看哪些站到期，不抓
python scripts/crawl_due.py --kind appointments
python scripts/crawl_due.py --kind leaders

# 顺带刷新到期的领导简介
python scripts/crawl_due_appointments.py --db output/tax_hr.db --with-leaders

# 忽略 7 天窗口，全量走一遍（仍跳过库中已有 URL）
python scripts/crawl_due_appointments.py --db output/tax_hr.db --force

# 只看不写库
python scripts/crawl_due_appointments.py --dry-run
```

入库后 Web 上即可看到新公告（`/notices`）。岗位档案默认不重建；需要时加 `--rebuild-posts`（较慢）。

云主机只爬、本机入库见下方「云主机增量抓取」。

## 项目结构

```
crawler_image/
├── tax_platform/           # 核心业务包
│   ├── config/             # 站点清单、调度、URL 发现与归一
│   ├── crawler/            # HTTP 客户端、任免/领导解析、抓取任务
│   ├── normalize/          # 职务、科室、人名、变动类型归一
│   ├── store/              # SQLite 入库、现任计算、岗位档案、异常修正
│   ├── search/             # 检索、变动流、公告、导出
│   ├── accounts/           # 登录、关注、邀请、邮件通知
│   └── web/                # FastAPI 应用与 static/ 前端页
├── scripts/                # CLI：抓取、入库、修复、运维
├── tests/                  # 单元测试
├── docs/                   # 架构说明、抓取踩坑、云同步方案
└── output/                 # 运行时数据（gitignore）
    ├── tax_hr.db           # 主库（默认路径）
    ├── list_heads.db       # 列表头监控库（可选）
    └── city_sites_registry.json
```

### 数据流

```
官网 HTML
  → crawler（列表 / 详情 / 条款解析）
  → normalize（职务、科室、人名校验）
  → store（notices / appointment_events / leader_duties / persons）
  → search + web（检索 API 与静态页）
```

账号模块（`accounts/`）只读订阅业务数据，写入独立的用户/关注/邮件表，不与 crawler 耦合。详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 常用命令

### 检查与测试

```powershell
python -m unittest discover -s tests -v
```

### 抓取

```powershell
# 任免（增量：跳过库中已有 URL；--full 全量）
python scripts/crawl_appointments.py --site shanghai --db output/tax_hr.db
python scripts/crawl_appointments.py --site all --due-only

# 领导简介
python scripts/crawl_leaders.py --site shanghai
python scripts/crawl_leaders.py --site all --due-only

# 列表头监控（新公告探测，不入库）
python scripts/crawl_list_heads.py --site all
```

到期巡检、计划任务见上方「每周增量更新」。

### 入库与重算

```powershell
python scripts/ingest_crawl.py          # JSON → SQLite
python scripts/recompute_tenure.py       # 重算现任 / persons
python scripts/rebuild_org_posts.py      # 重建岗位档案表
```

### 检索 CLI

```powershell
python scripts/search_hr.py --name 张三 --bureau shanghai
python scripts/changes_hr.py feed --bureau shanghai --limit 20
python scripts/changes_hr.py post --bureau shanghai --department 政策法规处
```

### 数据质量扫描（人工处理）

扫描库内可疑记录，写入 `data_anomalies` 表，供逐条修正或忽略（不自动改数据）：

```powershell
python scripts/anomalies_hr.py scan              # 全库扫描，刷新 open 异常
python scripts/anomalies_hr.py list --limit 20   # 列出异常
python scripts/anomalies_hr.py fix --type appointment_event --id 123 --set person_name=张三
python scripts/anomalies_hr.py ignore --anomaly-id 42
python scripts/anomalies_hr.py history           # 修正审计日志
python scripts/audit_data_integrity.py           # 外键/脏码/近重复等完整性报告 → output/_data_integrity_audit.json
```

常见检测项：人名可疑、职务/科室缺失、日期格式异常、科室字段解析噪声、同名跨多单位、同日任免冲突等。

### 批量修复（规则化）

按固定规则一次性修复已知结构问题，并可选重算下游表：

```powershell
python scripts/repair_appointment_events.py      # 补 bureau_name、去语义重复事件，重算身份/岗位/现任
python scripts/repair_notice_duplicates.py       # 合并重复公告 URL、补解析零事件公告
python scripts/reparse_appointments.py           # 从 notices.raw_text 重解析任免事件
python scripts/clean_integrity_noise.py          # 清未注册局码、STA→任职局归并、去重并重建岗位
```

### 站点发现

```powershell
python scripts/discover_city_sites.py --offline-check
python scripts/discover_city_sites.py --parent jiangsu --output output/city_sites_registry.json
python scripts/rediscover_list_urls.py   # 列表 URL 重发现（见 docs/crawl-pitfalls）
```

## 环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `TAX_HR_ADMIN_USER` | 管理员用户名 | `admin` |
| `TAX_HR_ADMIN_PASSWORD` | 管理员密码 | `TaxHR-Admin-ChangeMe` |
| `TAX_HR_SECRET` | Session 签名密钥 | 开发用固定值（**生产必改**） |
| `TAX_HR_PUBLIC_BASE` | 对外访问根 URL（邀请链接） | 自动推断；局域网/公网部署请显式设置 |
| `TAX_HR_DB` | 主库绝对路径 | `<项目根>/output/tax_hr.db` |
| `TAX_HR_OUTPUT_DIR` | output 目录 | `<项目根>/output` |
| `TAX_HR_CHROME` | 系统 Chrome/Chromium（CDP 回退） | 自动探测 |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM` | 邮件通知（可选） | 未配置则仅站内关注，不发信 |

模板见仓库根目录 `env.example`。

## 站点规模

代码里**写死**的站点与**运行时 registry** 合并后才是 `--site all` 的完整列表（见 `tax_platform/config/sites.py`）。

| 层级 | 数量 | 来源 |
| --- | --- | --- |
| 总局 | 1 | `sites_headquarters.py` |
| 省局 | 31 | `sites_provinces.py`（含直辖市） |
| 上海区县 | 16 区 + 7 直属 | `sites_shanghai.py`（区县路径前缀；稽查局/分局在 `_SPECIALS`） |
| 地市 / 区县 | **见 registry** | `output/city_sites_registry.json`（`gitignore`，不随仓库分发） |

地市/区县**没有固定数量**：克隆后 registry 为空则为 0；运行 `discover_city_sites.py` 逐省发现后会持续增长（开发机示例约 470+，市局与区县比例因发现进度而异）。

```powershell
# 查看本机 registry 条目数
python -c "import json; from pathlib import Path; p=Path('output/city_sites_registry.json'); print(len(json.loads(p.read_text())) if p.exists() else 0)"
```

`--site all`：内置站点 + registry；`--site cities`：仅市/区级（registry 中 `level=city|district`）。

## 云主机增量抓取（可选）

网站与主库留在本机，云主机只负责定期抓取新公告 JSON，回本机入库。流程见 [docs/cloud-crawl-sync.md](docs/cloud-crawl-sync.md)。

```powershell
# 本机
python scripts/cloud_sync_export.py
# 上传 output/cloud_sync/to_cloud → 云上 crawl → 下载 to_local
python scripts/local_pull_ingest.py
```

本机定时巡检仍用「每周增量更新」里的计划任务；云上对应跑 `cloud_crawl_export.py`。

## 抓取注意事项

各省站模板差异大（WAF 412、xxgk 壳页、TRS 栏目等）。修抓取或列表 URL 前请先阅读：

- [docs/crawl-pitfalls-2026-09-01.md](docs/crawl-pitfalls-2026-09-01.md)
- [.cursor/rules/crawl-pitfalls.mdc](.cursor/rules/crawl-pitfalls.mdc)（Cursor 规则摘要）

## 相关文档

| 文档 | 内容 |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 双平面架构、目录分层、公开页信息架构 |
| [docs/TASK_BREAKDOWN.md](docs/TASK_BREAKDOWN.md) | 登录边界、已落地能力、上海采集约定 |
| [docs/deploy-aliyun.md](docs/deploy-aliyun.md) | **阿里云 ECS 部署**：依赖、环境变量、systemd、Nginx |
| [docs/handoff-package.md](docs/handoff-package.md) | **交给别人部署时额外要给的文件**（db / registry 等） |
| [docs/cloud-crawl-sync.md](docs/cloud-crawl-sync.md) | 云爬本机入库 |
| [docs/crawl-pitfalls-2026-09-01.md](docs/crawl-pitfalls-2026-09-01.md) | 抓取踩坑记录 |

## 许可与免责

本项目用于学习与内部研究。使用抓取功能时请遵守各站点 robots 与服务条款，控制访问频率；公开数据仅供参考，人事以组织部门正式文件为准。
