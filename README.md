# 税务机关人事搜索平台

自动归集各级税局官网公开的人事任免与领导介绍，支持检索与变动追踪。

## 当前进度

- [x] PR1：平台骨架、上海数据源清单、实体契约、HTTP/URL 工具
- [ ] PR2+：采集、清洗、检索、监测（见 `docs/TASK_BREAKDOWN.md`）

## 快速检查（PR1）

```powershell
python scripts/smoke_foundation.py
```

## 目录

```
tax_platform/
  config/          # 数据源清单
  models/          # 实体契约
  crawler/         # 抓取与 URL 工具（后续拆列表/正文解析）
docs/
  TASK_BREAKDOWN.md
```

爬取结果默认写入 `output/`（已 gitignore）。
