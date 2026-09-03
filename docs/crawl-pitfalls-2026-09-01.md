# 爬虫踩坑记录 · 2026-09-01

> **开工前必读。** 做列表头监控、WAF/412、站点 URL、xxgk/信息公开、浏览器兜底相关任务时，先把本文件翻一遍，想清楚失败属于哪一类，再动手。  
> 不要一上来对整表失败站点盲重试、也不要对「看起来像 412」的站点无脑套北京 CDP。

相关代码：`tax_platform/crawler/http_client.py`、`xxgk_list.py`、`list_heads_job.py`、`appointment_job.py`；配置在 `tax_platform/config/`；失败表在 `output/list_heads.db`。

---

## 总原则（今天浪费时间的根因）

1. **先分类，再动手**  
   HTTP 状态相同 ≠ 同一类问题。412 / 403 / 404 / SSL / 死链 / AJAX 壳页，解法完全不同。

2. **盯死一个代表站点，验证通了再按「同一失败类」扩展**  
   禁止：对全部 soft-fail / 全部 open failures 开一小时盲重试。

3. **先查已存 URL 和已存 HTML，再开浏览器硬刚**  
   地市 `appointment_list_url` 往往已经在站点注册表里。用户浏览器能打开的页，优先用**已存链接**验证；不要先死磕省级坏链 + Playwright/CDP 空转。

4. **浏览器兜底是最后手段，且要盯结果**  
   系统 Chrome CDP 对北京有效，不代表对吉林/湖北有效。CDP 空白、跳到无关站（如增值税发票综合服务平台）时立刻停，换思路，不要堆重试。

5. **「半秒钟看有没有更新」的正确路径**  
   打开**该局已存的人事任免列表 URL** →（若是 xxgk）走 AJAX `search.jsp` 拿列表 → 比列表头。  
   不是：省级坏链 → WAF → 全套 headless → 再 CDP → 再换省。

---

## 坑 1：软失败被当成「空列表成功」

- **现象**：全量扫显示硬错误很少，实际大量站点 `list fetch failed` 后返回空 HTML，统计成成功空站。
- **处理**：软失败要记进 `appointment_list_scan_failures`，并让扫描路径在 fetch 失败时真正报错；重试要**按类**，不要整表盲跑。
- **教训**：看 summary 数字前先问「空成功是真的没公告，还是抓取失败被吞了」。

---

## 坑 2：58 站盲重试（约 71 分钟，0 恢复）

- **现象**：把失败表整表 `--retry-only` 跑很久，几乎全挂（SSL / WAF / Playwright 空转）。
- **教训**：**禁止**再做「失败多少就重试多少」的马拉松。先抽样 1 个代表，确认失败类与修复手段，再扩同类。

---

## 坑 3：北京 412 ≠ 所有 412

| 桶 | 例子 | 真实问题 | 不该做什么 |
|----|------|----------|------------|
| 真 WAF 412，列表 URL 仍可能有效 | `beijing`（已用系统 Chrome CDP 打通） | 人机/瑞数；详情页有时能过 | 假设所有 412 套同一 CDP 就通 |
| 表面上像 412 | 湖北市局 `hubei_hbsw_*` | 412 + 浏览器也过不去；CDP 曾跳到发票平台 | 湖北先停；勿长时间硬刷 |
| 412 → 死链 | 河北市局 `hebei_*` 的 `hbswxxgk/.../right.html` | URL 过时，常跟 404/`err404` | 别再 CDP；应 **URL 重发现** |
| 省级正常、市局坏链 | `hebei` 省局列表 OK | 市局注册表 URL 垃圾 | 以省站为线索重找市局任免栏 |
| 真 412 + HTTPS 烂证书 | 省级 `jilin` 的 `col/col8211` | HTTP 412；HTTPS legacy SSL；CDP 升 HTTPS 后空白 | 别在省级坏链上耗一天 |
| 403 | 山西部分、上海稽查/分局等 | 权限/另一套拦法 | 当 412 打 |
| 404 / 502 | `henan`、`guangdong_shenzhen` 等 | 死链 | 当 WAF 打 |
| SSL / 连接 | `sichuan*`、部分吉林路径 | 证书/握手 | 当 412 打 |

---

## 坑 4：吉林——有现成市局链接却去啃省级坏链

### 已存、该优先用的（示例）

- 长春 `jilin_col822`：  
  `https://jilin.chinatax.gov.cn/col/col13008/index.html?vc_xxgkarea=11220100MB1507524C&number=`
- 用户浏览器里能看的同类页（人事任免）：  
  `.../col/col13007/index.html?vc_xxgkarea=11220100MB1507524C`
- 其他市：`jilin_col823`、`jilin_col842`… 同为 `col… + vc_xxgkarea=…`

### 不该优先死磕的

- 省级 `jilin` → `col/col8211/index.html`（真 412 / SSL / CDP 空白）

### 页面形态（关键）

- 静态抓到的常是**信息公开壳**（标题像「政府信息公开指南」），列表由 `/module/xxgk/script/dynamic.js` 动态加载。
- `parse_appointment_list` 直接对壳 HTML → **0 条**，不代表栏目没更新。
- 正确路径：`vc_xxgkarea` + tree/`search.jsp`（见 `xxgk_list.py`）。2026-09-01 已补：从 URL 读 `vc_xxgkarea`、`&amp;` iframe、`standardXxgk` 的 `loadDynamic` 等。
- **离线**：`output/manual/jilin` 等已存 HTML，解析可出列表头（长春曾解析出含「绿园区任免…」等）。
- **线上**：`requests` 打 shell/tree/search 仍常 **412**；用户本机 Chrome 能开。  
  → **不是**「不能更新」。已对长春打通：Drission + 系统 Chrome（勿优先 CDP）→ 页内 `search.jsp`（详见文末「已打通」）。勿再盲重试省级 col8211。

### 用语澄清（曾让用户误解）

| 说法 | 实际意思 |
|------|----------|
| 离线能读 | 以前保存的 HTML / 手工页，解析逻辑 OK |
| 线上不行 | **此刻**用 requests/自动化打直播被 412 等拦住 |
| 不是 | 「业务上永远更新不了」 |

---

## 坑 5：CDP / 浏览器副作用

- 北京：系统 Chrome + CDP 可作为 requests/Playwright 之后的兜底。
- 湖北尝试时：Chrome 曾落到**增值税发票综合服务平台**（证书登录）——与任免列表无关。出现无关站立刻停。
- Playwright 空白页（几十字节）≠ 再多开几种 headless 就能好；先确认 URL 是否仍有效、是否 xxgk 壳、是否该用已存链接。

---

## 坑 6：列表头监控产品预期

用户预期：**每个地市任免链接已保存 → 点进去看一眼有没有更新 → 很快。**

实现应对齐：

1. 读该 `bureau` 的 `appointment_list_url`（不要临时猜省级 URL）。
2. `_load_appointment_list_html` / `fetch_xxgk_list_html` 拿真实列表 HTML。
3. 比 newest 标题/日期；有变更再告警或入库。
4. 仅当直播被 WAF 拦、且已确认 URL 有效时，再上**有头系统 Chrome**，并验证 DOM 里真有任免链接。

---

## 建议操作清单（下次相关任务）

- [ ] 打开本文件，对照失败站点属于哪一桶  
- [ ] `list_sites()` / 库里是否**已有**该局 `appointment_list_url`  
- [ ] 先对**一个**代表 URL：普通 fetch → 是否壳页 → xxgk AJAX → 必要时浏览器  
- [ ] 成功标准：解析出 ≥1 条任免标题，且 `scan_bureau_list_heads(code)` 无 error  
- [ ] 再扩展**同一桶**的兄弟站点；换桶换策略  
- [ ] 禁止：整表失败重试超过「抽样验证」所需时间  

---

## URL 重发现（可复用链路 · 2026-09-02）

**原则：只信已入库来源，找不到就停下来问用户，不瞎猜。**

```bash
python scripts/rediscover_list_urls.py henan guangdong_shenzhen
python scripts/rediscover_list_urls.py --from-failures          # 仅 open failures
python scripts/rediscover_list_urls.py --apply CODE ...         # 写回 registry
```

候选来源优先级（`tax_platform/config/url_rediscovery.py`）：
1. `output/city_sites_registry.json`
2. `org_units` 表
3. `sites_provinces.py` 省级 override
4. 手工 ingest 脚本里的 `appt_fallback`（如 `ingest_manual_batch.py`）
5. 河南地市 `henan_path_*` → `/{slug}/xxgk/zfxxgk/fdzdgknr/rsgl/rsrm/`

验证：quick requests + `parse_appointment_list`；全部失败 → **NEEDS_USER**。

### 已修复示例

| 局 | 旧 URL（死链/错） | 正确来源 | 新 URL |
|----|-------------------|----------|--------|
| **henan** 省局 | `/xxgk/rsxx/`（模板默认） | `ingest_manual_batch.py` | `/xxgk/rsgl/rsrm/` |
| **guangdong_shenzhen** | `.shtml/` 多斜杠 | `ingest_plan_cities_manual.py` + 规范化 | `.shtml` 无尾斜杠 |
| **henan_path_zhengzhou** | 已在 registry | 用户截图一致 | `/zhengzhou/xxgk/.../rsrm/` |

`.shtml` 尾斜杠 bug：`is_html_file_path()` 覆盖 `.html/.htm/.shtml`。

---

### 吉林市局 `jilin_col*`（8/8，含延边）
- 路径：requests 412 → Drission + 系统 Chrome → 页内 `search.jsp`（`infotypeId=rsglrsrm`）
- **URL 规范化**：注册表 HTTPS → 加载时改 HTTP（避免 legacy SSL）
- 省级 `jilin` / `col8211` 仍跳过，用市局 URL

### 河北市局 `hebei_*`（14/14）
- **根因不是死链**：`right.html/` 尾部多一个 `/` → 412→404；去掉斜杠后 **plain requests 即可**，无需浏览器
- 修复：`tax_platform/config/list_url_normalize.py` + `city_sites_io.py` 加载时规范化

### 自动化加固
- `list_heads_buckets.py`：默认跳过 `hubei` / `hubei_hbsw_*`（`--include-deferred` 可强制包含）
- `fix_list_failures_sequential.py` 同样默认跳过 deferred，勿再把湖北排进顺序补失败
- 测试：`tests/test_list_url_normalize.py`

---

### 福建市局 `fujian_*`（jgsz 壳页）
- **现象**：库里已有 `…/zfxxgkml/jgsz/`（用户浏览器能看到人事任免列表），爬虫却 `items=0` 或长时间卡在浏览器点树。
- **根因**：`jgsz/` 只是主动公开目录壳；点 zTree「人事任免」后 Avalon 调 `/was5/web/search?channelid=…&classsql=…*chnlid=…` 才出列表。静态 HTML / 盲开浏览器点树都不可靠。
- **处理**：`tax_platform/crawler/fujian_was5.py` — 从壳页抽 `channelid` + 人事任免 `chnlid`，直接打 WAS5，合成列表 HTML（福州约 0.8s / 20 条）。勿再优先 Drission/Playwright。

### 重庆 qxtax 区县 `chongqing_qxtax_*`
- **现象**：`/api/queryGwxxQx` 无 Ruishu cookie 时返回 HTML → JSON 解析失败；扫描 `items=0` 但未记 failure。
- **处理**：`_warm_qxtax_session` 先 `fetch_html_browser` 拿 cookie；API 仍失败时用浏览器页 HTML 兜底解析。

### 总局 `sta` / 山西·黑龙江省级 / 北京密云·延庆
- **sta**：列表走 `getFileListByCodeId`（`sta_chinatax.py`），勿解析静态壳页。
- **山西省**：`http://shanxi.chinatax.gov.cn/son/list/sx-11400-4187` + Drission 等 `#wzList`/`son/detail`（`shanxi_son_list.py` 可点「人事任免」）。
- **黑龙江省**：栏目是 **col17418**（非 col11190），须 **http** 才有 dataproxy 列表。
- **北京密云/延庆**：`c106486/xxgk_nr_fjmy.shtml`、`c106488/xxgk_nr_fjyq.shtml`；其余区县可从 manual `appt_list.html` 的 saved-from URL 切回 live（`scripts/patch_gap_list_urls.py`）。

---
- **正确列表**：`http://qinghai.chinatax.gov.cn/{slug}/rsrm/xxgk_fdzd_list.shtml`（不是 `commonlistmore.shtml?channelId=`）。
- **注意**：URL 对了以后 plain HTTP 可解析；若整批「卡住」，先查是否被福建浏览器兜底或 `list_heads.db` locked 拖死，不要怀疑西宁链接本身。

---

## 仍未收尾（勿重复踩坑后再从头摸）

| 桶 | 约数 | 策略 |
|----|------|------|
| 湖北 `hubei` / `hubei_hbsw_*` | 7 | **停**：官网侧异常；失败保留，勿硬刷/CDP，等站点恢复再开 |
| 省级 `jilin` col8211 | 0 | 已修（dataproxy 412 不拖垮首页） |
| 山西 `shanxi_son_*` | 0 | 已修：HTTP + Drission 等 JS 列表 |
| 上海稽查/分局 `*jcj`/`*fj` | 0 | 已修：`xxgk.xml`（HTML 403） |
| 四川 `sichuan*` | 0 | 已修：SSL → 浏览器兜底 |

当前 open failures 仅剩 **湖北 deferred**（保留待恢复）。
