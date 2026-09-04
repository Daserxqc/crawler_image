# 给别人部署时：除了 GitHub，还要额外交什么

仓库代码可以 `git clone`，但 **`output/` 整目录在 `.gitignore` 里**，GitHub **没有**业务数据。别人只拉代码，网站能启动，却没有你现在的几万人数据。

> 云主机怎么装、怎么开机自启、怎么每周自动抓：见 [deploy-aliyun.md](./deploy-aliyun.md)。  
> 本页回答：**你作为作者要额外塞给对方哪些文件**，以及如何一键打交付包。

---

## 推荐：一键打「GitHub 没有的」交付包

在你本机项目根目录：

```powershell
python scripts/pack_handoff.py --no-source
```

或直接用已整理好的目录（可整夹压缩发给对方）：

```text
dist/handoff-extra-not-on-github/
dist/handoff-extra-not-on-github.zip
```

内含 `output/tax_hr.db`、`city_sites_registry.json`、`crawl_state.json`、`env.example` 与部署说明。  
**代码仍让对方从 GitHub 拉取**；解压后把 `output/` 拷进仓库根目录即可。

若还要附带精简源码树：

```powershell
python scripts/pack_handoff.py
```

---

## 一句话结论

| 对方目标 | 你至少额外给 |
| --- | --- |
| **只上线网站、能检索** | `tax_hr.db` + 部署说明 + `env.example` |
| **网站 + 云上每周自动增量抓取**（推荐场景） | 上面 + `city_sites_registry.json` + `crawl_state.json` |
| **只要代码自己从头爬** | 可不给 db，但市县要自己发现，结果不会自动等于你的库 |

---

## 必交清单（网站 + 云上每周抓）

### 1. 代码

- Git 仓库权限，或交付包里的 `source/`  
- 对方：`pip install -r requirements.txt`（见部署文档）

### 2. `data/tax_hr.db`（最重要）

主库：人员、任免、公告、岗位，以及账号相关表。没有它 = 没有你的业务数据。

### 3. `data/city_sites_registry.json`

代码只内置总局 / 省局 / 上海等；**大量地市、区县 URL 在这个文件**。  
云上要跑 `crawl_due_appointments.py` / `--site all` 时**必给**，否则缺一大片站。

### 4. `data/crawl_state.json`（强烈建议）

各站上次抓取时间。不给也能爬，但「约 7 天到期」会按空状态重算，可能一上来大量站被当成到期。

### 5. `env.example` + 文档

- 密钥、管理员密码让对方**自己生成**，不要共用你的生产 `TAX_HR_SECRET`  
- [deploy-aliyun.md](./deploy-aliyun.md) 含 Linux cron：`install_linux_crawl_cron.py`

---

## 不必交

| 内容 | 原因 |
| --- | --- |
| `.venv/` | 对方按系统自建 |
| 各省 `*_appointments.json` / manual JSON | 已入库的中间产物 |
| `output/_*.json`、探针、bak | 调试垃圾 |
| Cookie 文件 | 敏感且易过期 |
| Windows 计划任务脚本本身 | 云上用 Linux cron；脚本可留在仓库供本机用 |

---

## 换环境会不会坏？（已处理要点）

交付前已做可移植性修正，对方 Linux 云主机应注意：

| 点 | 说明 |
| --- | --- |
| 数据路径 | `tax_hr.db` / `crawl_state` / `city_sites_registry` 默认锚定**项目根**下的 `output/`，不再死依赖「当前工作目录」；也可用环境变量 `TAX_HR_DB`、`TAX_HR_OUTPUT_DIR` |
| 启动入口 | `run_api.py`、`crawl_due_appointments.py` 会 `chdir` 到项目根 |
| Chrome 路径 | 不再只有 Windows `Program Files`；会查 Linux 的 `google-chrome` / `chromium`，可用 `TAX_HR_CHROME` |
| 无图形云主机 | Playwright/CDP 在非 Windows 上自动加 `--no-sandbox` 等参数 |
| 定时任务 | **不要**在云上用 `install_windows_crawl_task.py`；用 `install_linux_crawl_cron.py --install` |
| 绝对路径 | 业务代码无写死 `E:\…` / `C:\Users\…`；Windows Chrome 候选仅作本机回退 |

对方仍须：

1. 在项目根放好 `output/` 下数据文件  
2. systemd / cron 的工作目录指向项目根（文档已写）  
3. 需要浏览器绕 WAF 时再 `playwright install chromium`（纯检索可不装）

---

## 安全提醒

1. 交 `tax_hr.db` ≈ 交出库内账号与关注数据；演示库请自行脱敏或重置管理员。  
2. `TAX_HR_PUBLIC_BASE` 写成对方的公网 IP/域名。  
3. 人事归集数据的二次分发范围要说清楚。

---

## 对方解压后最短路径

```bash
# 若用交付包 source/
cd source
mkdir -p output && cp ../data/* output/
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../env.example /tmp/tax-hr.env   # 改密钥后放到 /etc/tax-hr.env
# 然后按 docs/deploy-aliyun.md 第 9 步起继续：试跑 → systemd → cron
python scripts/install_linux_crawl_cron.py --install
```
