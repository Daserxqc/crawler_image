# 税务机关人事搜索平台

自动归集各级税局官网公开的人事任免与领导介绍，支持检索与变动追踪。

公开检索不需要登录。关注与邮件推送以后单独放在 `tax_platform/accounts/`，公开代码不依赖它。设计说明见 `docs/ARCHITECTURE.md`。

## 当前进度

- [x] PR1：平台骨架、上海数据源清单、实体契约、HTTP/URL 工具
- [x] PR2：人事任免列表 / 详情 / 条款抽取
- [x] PR3：领导介绍解析

## 检查

```powershell
python scripts/smoke_foundation.py
python tests/test_appointment_parsers.py
python tests/test_leader_intro.py
python tests/test_crawl_jobs.py
python scripts/crawl_appointments.py --site pdtax --limit 3
python scripts/crawl_leaders.py --site pdtax
```

## 目录

```
tax_platform/
  config/          数据源清单
  models/          实体契约
  crawler/         HTTP、解析、按站点 crawl job
  accounts/        登录与关注（预留，后做）
scripts/           薄 CLI（调 job，写 output/）
docs/
  ARCHITECTURE.md
  TASK_BREAKDOWN.md
```

爬取结果默认写入 `output/`（已 gitignore）。程序内请用：

```python
from tax_platform.crawler import crawl_appointments, crawl_leaders
```
