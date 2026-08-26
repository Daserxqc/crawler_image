# 税务机关人事搜索平台

自动归集各级税局官网公开的人事任免与领导介绍，支持检索与变动追踪。

## 当前进度

- [x] PR1–3：骨架、任免/领导解析、crawl job
- [x] PR4：职务/科室基础归一（`tax_platform/normalize/`）
- [x] PR5：SQLite 入库、公告级增量抓取、入库后现任落库（`persons.is_current`）
- [x] 全国站点清单：总局 + 31 省 + 上海 16 区（共 48 个内置站点）
- [x] 地市/区县发现骨架：`scripts/discover_city_sites.py` → `output/city_sites_registry.json`
- [x] PR6–9：异常修正 / 检索 / 穿透导出 / 变动流 — **API + CLI 已完成**
- [x] 公开前端页（检索 / 履历 / 变动 / 科室穿透）；岗位页未做
- [ ] PR10：登录/关注（`accounts/`）

## 站点规模

| 层级 | 数量 | 说明 |
| --- | --- | --- |
| 总局 | 1 | `sta` |
| 省局 | 31 | 含直辖市 |
| 上海区县 | 16 | 已手工配置路径前缀 |
| 地市/其他区县 | 0+ | 由 `city_sites_registry.json` 并入 |
| **内置合计** | **48** | `--site all` 会遍历内置 + registry |

## 常用命令

```powershell
# 检查
python scripts/smoke_foundation.py
python -m unittest discover -s tests -v

# 抓取（增量：跳过库中已有公告 URL；--full 全量重抓）
python scripts/crawl_leaders.py --site shanghai --output output/shanghai_leaders.json
python scripts/crawl_appointments.py --site shanghai --db output/tax_hr.db --output output/shanghai_appointments.json
python scripts/crawl_appointments.py --site all --full --output output/national_appointments.json
python scripts/crawl_leaders.py --site all --due-only --output output/national_leaders.json

# 入库 + 现任重算
python scripts/ingest_crawl.py
python scripts/recompute_tenure.py
python scripts/list_persons.py

# 地市发现 / 空分管摸底
python scripts/discover_city_sites.py --offline-check
python scripts/discover_city_sites.py --parent shanghai --dry-run
python scripts/report_empty_oversight.py --db output/tax_hr.db

# 检索 UI + API（PR7–PR9）
pip install -r requirements.txt
python scripts/run_api.py
# 前端: http://127.0.0.1:8000/
# 文档: http://127.0.0.1:8000/docs

# 变动流 / 岗位 CLI
python scripts/changes_hr.py feed --bureau shanghai --limit 10
python scripts/changes_hr.py post --bureau shanghai --department 政策法规处

# 异常扫描 / 人工修正（PR6）
python scripts/anomalies_hr.py scan
python scripts/anomalies_hr.py list --limit 20
```

## 目录

```
tax_platform/
  config/          站点清单 + 省/地市发现
  normalize/       职务、科室归一
  store/           SQLite 入库、现任、异常
  crawler/         抓取与解析（任免支持增量 skip）
  search/          检索 / 变动 / 导出
  web/             FastAPI + static/ 简易检索页
  accounts/        登录与关注（预留）
scripts/           CLI
docs/
  TASK_BREAKDOWN.md
  ARCHITECTURE.md
```

数据写入 `output/`（gitignore），人员库默认 `output/tax_hr.db`。
