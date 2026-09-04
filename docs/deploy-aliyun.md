# 阿里云部署：一步一步怎么做

按顺序做即可。目标：在一台阿里云 ECS 上跑起「税局人事检索」网站，并配置**每天检查、约每周增量抓取**到期站点。

**约定（可改，全文统一即可）：**

| 项 | 本文取值 |
| --- | --- |
| 系统 | Ubuntu 22.04 LTS（Alibaba Cloud Linux 也行，装包命令见附录） |
| 代码目录 | `/opt/crawler_image` |
| 登录用户 | 下文用 `ecs-user`，请换成你自己的 Linux 用户名 |
| 网站端口 | 先用 `8000` 验证，再加 Nginx 用 `80` |

> 作者侧怎么打交付包、还要额外给哪些文件：看 [handoff-package.md](./handoff-package.md)。  
> 只要云上抓、网站仍在自己电脑：看 [cloud-crawl-sync.md](./cloud-crawl-sync.md)。

---

## 总览（你要完成的事）

```text
① 买 ECS、开安全组
② SSH 登录云主机
③ 装 Python / 系统工具
④ 准备代码目录
⑤（第 6 步）代码 + tax_hr.db + registry 拷到云上
⑥ 建虚拟环境并 pip 安装依赖
⑦ 写好密码等环境变量
⑧ 先手动启动试一次
⑨ 配成开机自启（systemd）
⑩ （推荐）Nginx 反代到 80 端口
⑪ 安装 Linux 定时抓取（每天检查到期站）
⑫ 浏览器验收
```

---

## 第 1 步：买一台 ECS

1. 打开 [阿里云 ECS 控制台](https://ecs.console.aliyun.com/) → **创建实例**。
2. 建议选项：
   - 地域：离你近的即可  
   - 实例：至少 **2 核 4G**（导出全库更稳）  
   - 镜像：**Ubuntu 22.04 64 位**  
   - 系统盘：≥ 40 GB  
   - 公网 IP：**分配**（或稍后绑弹性公网 IP）  
3. 设置 **root/ecs-user 登录密码** 或绑定 SSH 密钥，记下来。  
4. 创建完成后，在实例列表记下：
   - **公网 IP**（下文写成 `YOUR_IP`）
   - **登录用户名**（Ubuntu 常见 `root` 或自定义用户）

---

## 第 2 步：放行安全组端口

1. 点进该实例 → **安全组** → **配置规则** → **入方向** → **手动添加**。
2. 先加这两条（方便你先测通）：

| 协议 | 端口 | 授权对象 | 用途 |
| --- | --- | --- | --- |
| TCP | 22 | 建议写成你自己的办公网 IP/32；临时可 `0.0.0.0/0` | SSH |
| TCP | 8000 | `0.0.0.0/0` | 先直接访问网站（测完可删） |

3. 后面上 Nginx 时再加 **80**（和需要时的 **443**）。  
4. 保存。

---

## 第 3 步：SSH 登录云主机

在你自己电脑的终端（PowerShell / macOS Terminal）执行：

```bash
ssh ecs-user@YOUR_IP
```

- 把 `ecs-user`、`YOUR_IP` 换成真实值。  
- 第一次会问是否信任主机，输入 `yes`。  
- 输入实例密码（或用密钥）。

登录成功后，后面凡是「在云上执行」的命令，都在这个 SSH 窗口里做。

---

## 第 4 步：安装系统依赖（云上）

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git build-essential \
  libffi-dev libssl-dev curl
python3 --version
```

确认输出是 **Python 3.10 或更高**。若低于 3.10，换 Ubuntu 22.04 镜像或自行安装更高版本 Python。

（只要跑网站，**先不要**装 Playwright / Chrome。）

---

## 第 5 步：准备代码目录（云上）

```bash
sudo mkdir -p /opt/crawler_image
sudo chown "$USER":"$USER" /opt/crawler_image
cd /opt/crawler_image
```

---

## 第 6 步：把代码和数据放到云上

**推荐：作者本机先打交付包**（含源码精简版 + db + registry）：

```powershell
# 在作者电脑项目根目录
python scripts/pack_handoff.py
# 得到 dist/tax-hr-handoff-日期.zip
```

上传 zip 到云主机后：

```bash
cd /opt
sudo apt install -y unzip   # 若尚未安装
unzip tax-hr-handoff-*.zip
# 若解压出 tax-hr-handoff-日期/source ，则：
sudo mv tax-hr-handoff-*/source /opt/crawler_image
sudo mkdir -p /opt/crawler_image/output
sudo cp tax-hr-handoff-*/data/* /opt/crawler_image/output/
sudo chown -R "$USER":"$USER" /opt/crawler_image
```

### 方式 A：Git clone + 单独拷数据

```bash
cd /opt/crawler_image
git clone 你的仓库地址 .
mkdir -p output
# 把交付包 data/ 或本机 output 里这些文件拷进来：
#   tax_hr.db
#   city_sites_registry.json   （每周自动抓市县必需要）
#   crawl_state.json           （建议）
```

本机上传示例：

```powershell
scp E:\crawler_image\output\tax_hr.db ecs-user@YOUR_IP:/opt/crawler_image/output/
scp E:\crawler_image\output\city_sites_registry.json ecs-user@YOUR_IP:/opt/crawler_image/output/
scp E:\crawler_image\output\crawl_state.json ecs-user@YOUR_IP:/opt/crawler_image/output/
```

上传后 **云上** 检查：

```bash
ls /opt/crawler_image/tax_platform /opt/crawler_image/scripts/run_api.py
ls -lh /opt/crawler_image/output/tax_hr.db
ls /opt/crawler_image/output/city_sites_registry.json
```

路径说明：默认库文件解析为**项目根下的** `output/tax_hr.db`（不依赖你从哪个目录启动命令）。也可用环境变量 `TAX_HR_DB` / `TAX_HR_OUTPUT_DIR` 覆盖。

---

## 第 7 步：安装 Python 依赖（云上）

```bash
cd /opt/crawler_image
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

装完后提示符前面一般有 `(.venv)`。  
这一步装的是：FastAPI、uvicorn、openpyxl、requests 等（见 `requirements.txt`）。

---

## 第 8 步：配置生产环境变量（云上）

1. 生成一段随机密钥：

```bash
openssl rand -hex 32
```

把输出复制下来（下文 `你的密钥`）。

2. 写入环境文件（**把密码、IP 改成你的**）：

```bash
sudo tee /etc/tax-hr.env >/dev/null <<EOF
TAX_HR_SECRET=你的密钥
TAX_HR_ADMIN_USER=admin
TAX_HR_ADMIN_PASSWORD=请改成你自己的强密码
TAX_HR_PUBLIC_BASE=http://YOUR_IP:8000
# 可选：TAX_HR_DB=/opt/crawler_image/output/tax_hr.db
# 可选：TAX_HR_CHROME=/usr/bin/google-chrome-stable
EOF
sudo chmod 600 /etc/tax-hr.env
```

仓库根目录也有 `env.example` 可对照。

说明：

- `TAX_HR_PUBLIC_BASE`：现在先写成 `http://公网IP:8000`；以后上了域名/HTTPS 再改。  
- 管理员账号第一次启动时会按这里创建（若库里已有用户则不会覆盖密码逻辑以代码为准；新库一般会创建）。

---

## 第 9 步：手动试跑（云上）

```bash
cd /opt/crawler_image
source .venv/bin/activate
set -a
source /etc/tax-hr.env
set +a
python scripts/run_api.py --host 0.0.0.0 --port 8000
```

看到类似 `Uvicorn running` / `Application startup complete` 即成功。

**在你自己电脑浏览器打开：**

```text
http://YOUR_IP:8000/
```

不要打开 `http://0.0.0.0:8000/`。

能进首页、能检索，再在云上按 **Ctrl+C** 停掉前台进程，进入下一步做「开机自启」。

若打不开：回到第 2 步检查安全组 8000；在云上执行 `curl -I http://127.0.0.1:8000/` 看本机是否通。

---

## 第 10 步：做成系统服务（开机自启）

把 `ecs-user` 换成第 3 步登录用的用户名：

```bash
sudo tee /etc/systemd/system/tax-hr.service >/dev/null <<'EOF'
[Unit]
Description=Tax HR Search API
After=network.target

[Service]
Type=simple
User=ecs-user
WorkingDirectory=/opt/crawler_image
EnvironmentFile=/etc/tax-hr.env
Environment=PYTHONPATH=/opt/crawler_image
ExecStart=/opt/crawler_image/.venv/bin/python /opt/crawler_image/scripts/run_api.py --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now tax-hr
sudo systemctl status tax-hr
```

`status` 里应看到 `active (running)`。再浏览器访问一次 `http://YOUR_IP:8000/`。

常用命令：

```bash
sudo systemctl restart tax-hr    # 改代码/改环境变量后重启
sudo systemctl stop tax-hr
journalctl -u tax-hr -f          # 看实时日志
```

改了 `/etc/tax-hr.env` 后必须 `sudo systemctl restart tax-hr`。

---

## 第 11 步：（推荐）用 Nginx 走 80 端口

这样地址变成 `http://YOUR_IP/`，不用带 `:8000`。

### 11.1 安全组再放行 80

入方向加：TCP **80**，`0.0.0.0/0`。

### 11.2 安装并配置 Nginx（云上）

```bash
sudo apt install -y nginx

sudo tee /etc/nginx/conf.d/tax-hr.conf >/dev/null <<'EOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
EOF

sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

### 11.3 让应用只监听本机（更安全）

改服务文件里的启动参数为只监听本机，由 Nginx 对外：

```bash
sudo systemctl edit --full tax-hr
```

把 `ExecStart=` 那一行改成：

```text
ExecStart=/opt/crawler_image/.venv/bin/python /opt/crawler_image/scripts/run_api.py --host 127.0.0.1 --port 8000
```

保存后：

```bash
sudo systemctl daemon-reload
sudo systemctl restart tax-hr
```

安全组里可以删掉 **8000** 的公网规则（保留 22、80）。

### 11.4 更新对外地址

```bash
sudo nano /etc/tax-hr.env
# 把 TAX_HR_PUBLIC_BASE 改成：
# TAX_HR_PUBLIC_BASE=http://YOUR_IP
sudo systemctl restart tax-hr
```

浏览器打开：`http://YOUR_IP/`。

有域名、要 HTTPS 时：在阿里云申请证书或使用 certbot，再把 `TAX_HR_PUBLIC_BASE` 改成 `https://你的域名`。

---

## 第 12 步：安装每周增量抓取（每天检查）

站点默认约 **7 天**到期重爬一次；建议 **每天**跑检查脚本，到期站才会被抓到。

**不要**在云上用 Windows 的 `install_windows_crawl_task.py`。

```bash
cd /opt/crawler_image
source .venv/bin/activate
# 先手动跑一次，确认能出网访问税局站、能写库
python scripts/crawl_due_appointments.py --db output/tax_hr.db
# 安装 crontab（默认每天 03:30）
python scripts/install_linux_crawl_cron.py --install
crontab -l | grep TaxHR
```

日志：`output/crawl_due_appointments.log`。

若大量站 WAF/412 失败，再按需安装浏览器回退（可选）：

```bash
sudo apt install -y chromium-browser
# 或: playwright install chromium && playwright install-deps chromium
```

可用 `TAX_HR_CHROME=/usr/bin/chromium-browser` 指明路径。

---

## 第 13 步：验收

在浏览器完成：

1. 打开首页，能看到检索页。  
2. 用第 8 步设的管理员账号登录。  
3. 随便搜一个省或姓名，有结果。  
4. （可选）导出一小范围 Excel，能下载。  
5. `crontab -l` 能看到 TaxHR 抓取行；日志文件会随每日任务增长。  

都通过即部署完成。

---

## 以后怎么更新

### 只更新数据（换新的 db）

在本机生成/拷好新库后：

```powershell
# 本机
scp E:\crawler_image\output\tax_hr.db ecs-user@YOUR_IP:/opt/crawler_image/output/tax_hr.db.new
```

云上：

```bash
sudo systemctl stop tax-hr
mv /opt/crawler_image/output/tax_hr.db.new /opt/crawler_image/output/tax_hr.db
sudo systemctl start tax-hr
```

### 只更新代码

上传新代码覆盖后：

```bash
cd /opt/crawler_image
source .venv/bin/activate
pip install -r requirements.txt   # 依赖有变才需要
sudo systemctl restart tax-hr
```

---

## 卡住了怎么办

| 现象 | 怎么查 |
| --- | --- |
| SSH 连不上 | 安全组 22、公网 IP、密码/密钥 |
| 浏览器打不开网站 | 安全组 8000/80；`systemctl status tax-hr`；`journalctl -u tax-hr -n 50` |
| 502 | Nginx 在、应用没在：先看 `tax-hr` 是否 running |
| 打开 `0.0.0.0` 异常 | 改用公网 IP 或域名 |
| 导出一直转圈 | 正常会较久；Nginx 已设 600s 超时 |
| 权限错误 / database locked | 确认 `tax-hr.service` 的 `User=` 能写 `output/`；抓取与写库不要多进程死抢 |
| 定时任务没跑 | `crontab -l`；看 `output/crawl_due_appointments.log`；确认用的是 venv 里的 python |
| 市县抓不到 | 是否缺少 `output/city_sites_registry.json` |

---

## 附录 A：Alibaba Cloud Linux 装包

把第 4 步的 `apt` 换成：

```bash
sudo dnf install -y python3 python3-devel python3-pip git gcc \
  libffi-devel openssl-devel
python3 --version
```

Nginx：`sudo dnf install -y nginx`。

---

## 附录 B：装了哪些东西（对照）

| 层级 | 内容 |
| --- | --- |
| 系统 | `python3`、`venv`、`pip`、`git`、编译相关库 |
| Python 包 | `requirements.txt`（fastapi、uvicorn、openpyxl…） |
| 数据 | `output/tax_hr.db` + `city_sites_registry.json` + `crawl_state.json` |
| 配置 | `/etc/tax-hr.env`（见仓库 `env.example`） |
| 进程 | `systemd` 服务名 `tax-hr` |
| 定时 | `install_linux_crawl_cron.py` → 用户 crontab |
| 可选 | Nginx、Playwright/Chromium（WAF 回退） |

## 附录 C：路径与环境变量（换机器）

| 变量 | 作用 |
| --- | --- |
| （默认） | 库与状态文件在「代码包根目录」下的 `output/` |
| `TAX_HR_DB` | 主库绝对路径 |
| `TAX_HR_OUTPUT_DIR` | output 目录绝对路径 |
| `TAX_HR_CHROME` | 系统 Chrome/Chromium 路径（CDP 回退） |

入口脚本会切到项目根再跑，避免 cron 工作目录不对导致「空库」。

本地开发命令仍见仓库 [README.md](../README.md)。
