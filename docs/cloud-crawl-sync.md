# 云上只爬、本机入库（方案 3）

网站、账号、SQLite 主库仍在本机；云主机只负责定期打开税局任免列表页、抓新公告，把 JSON 送回本机入库。

## 流程

```
本机 export known_urls  ──上传──►  云主机
云主机 crawl（增量，不写库）──下载──►  本机 ingest
```

| 步骤 | 在哪 | 命令 |
|------|------|------|
| 1. 导出已知公告 URL | 本机 | `python scripts/cloud_sync_export.py` |
| 2. 上传 `to_cloud/` | 本机→云 | 见下方 scp |
| 3. 云上增量爬取 | 云 | `python scripts/cloud_crawl_export.py` |
| 4. 下载 `to_local/` | 云→本机 | 见下方 scp |
| 5. 本机入库 | 本机 | `python scripts/local_pull_ingest.py` |

目录约定：

- 本机导出：`output/cloud_sync/to_cloud/`
- 云上接收：`output/cloud_sync/from_local/`（内容与 to_cloud 相同）
- 云上产出：`output/cloud_sync/to_local/appointments.json`
- 本机接收：`output/cloud_sync/from_cloud/`（内容与 to_local 相同）

## scp 示例

把 `user@vps`、路径换成你的云主机：

```bash
# 本机 → 云
scp -r output/cloud_sync/to_cloud user@vps:/opt/crawler_image/output/cloud_sync/from_local

# 云上爬完后：云 → 本机
scp -r user@vps:/opt/crawler_image/output/cloud_sync/to_local output/cloud_sync/from_cloud
```

也可用网盘 / rsync / MinIO，只要文件落到上述目录即可。

## 云主机 cron（每天 3:30）

仓库与依赖需已部署在云上；`from_local/known_urls.json` 至少要有一份（可每周从本机更新一次）。

```cron
30 3 * * * cd /opt/crawler_image && /usr/bin/python3 scripts/cloud_crawl_export.py >> output/cloud_crawl_export.log 2>&1
```

本机可另建计划任务，开机或每天拉取并入库：

```text
python scripts/local_pull_ingest.py --db output/tax_hr.db
```

（需先完成 scp/同步；也可写一个小脚本：scp + local_pull_ingest。）

## 为何不把整个 DB 放云上

本机还有账号、关注等表；云上只回传任免 JSON，用现有 `ingest_appointment_results` 合并，避免覆盖本机数据。

## 注意

- 优先选**国内**云主机，税局站更稳。
- 第一次可在云上加 `--force` 扫一轮：`python scripts/cloud_crawl_export.py --force`
- 长期关机的本机：只要云在跑，新公告会积在 `to_local/`；你开机后拉一次再 ingest 即可。
