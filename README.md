# 税务机关人事搜索平台

自动归集各级税局官网公开的人事任免与领导介绍，支持检索与变动追踪。

## 当前进度

- [x] PR1–3：骨架、任免/领导解析、crawl job
- [x] PR4：职务/科室基础归一（`tax_platform/normalize/`）
- [x] PR5（部分）：SQLite 入库、人员履历 + 公告链接（`tax_platform/store/`）
- [x] 全国站点清单：总局 + 31 省 + 上海 16 区（共 48 个站点）
- [ ] 地市/区县：从各省信息公开页自动发现（待做）
- [ ] PR6–9：异常修正、检索页、变动流
- [ ] PR10：登录/关注（`accounts/`）

## 站点规模

| 层级 | 数量 | 说明 |
| --- | --- | --- |
| 总局 | 1 | `sta` |
| 省局 | 31 | 含直辖市；URL 模板 `{省}.chinatax.gov.cn/xxgk/rsxx/` |
| 上海区县 | 16 | 已手工配置路径前缀 |
| **合计** | **48** | `--site all` 会遍历全部 |

## 常用命令

```powershell
# 检查
python scripts/smoke_foundation.py
python tests/test_normalize_and_sites.py
python tests/test_store.py

# 抓取（示例：全省/全国）
python scripts/crawl_leaders.py --site shanghai --output output/shanghai_leaders.json
python scripts/crawl_appointments.py --site all --output output/national_appointments.json
python scripts/crawl_leaders.py --site all --due-only --output output/national_leaders.json

# 入库 + 查看人员
python scripts/ingest_crawl.py
python scripts/list_persons.py                          # 列出库里所有人（摘要）
python scripts/list_persons.py --full --output output/all_profiles.json   # 导出全员完整履历+公告链接
python scripts/list_persons.py --bureau pdtax --full    # 只看某个局

# 仅调试单个人时用（不用一个个跑）
python scripts/person_profile.py --name 郑燕 --bureau pdtax

# 检索 API（PR7–PR9）
pip install -r requirements.txt
python scripts/run_api.py
# 文档: http://127.0.0.1:8000/docs
# 示例: /api/search?department=政策法规处&bureau_code=shanghai
#       /api/departments/penetrate?department=政策法规处
#       /api/people/shanghai:刘洪波
#       /api/changes?bureau_code=shanghai&limit=20
#       /api/posts?bureau_code=shanghai&department=政策法规处
#       /api/export/search?name=刘洪波&fmt=xlsx

# 变动流 / 岗位 CLI
python scripts/changes_hr.py feed --bureau shanghai --limit 10
python scripts/changes_hr.py post --bureau shanghai --department 政策法规处

# 异常扫描 / 人工修正（PR6）
python scripts/anomalies_hr.py scan
python scripts/anomalies_hr.py list --limit 20
python scripts/anomalies_hr.py fix --type appointment_event --id 123 --set person_name=陈双格 --note "修正人名"
```

## 目录

```
tax_platform/
  config/          站点清单（总局/省/上海区）
  normalize/       职务、科室归一
  store/           SQLite 入库与人员履历
  crawler/         抓取与解析
  accounts/        登录与关注（预留）
scripts/           CLI
docs/
  TASK_BREAKDOWN.md
  ARCHITECTURE.md
```

数据写入 `output/`（gitignore），人员库默认 `output/tax_hr.db`。
