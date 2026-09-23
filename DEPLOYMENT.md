# 远程部署：GitHub Pages + Linux Docker

## 架构与边界

浏览器从 `https://<account>.github.io/Classmates-Explorer/` 加载静态文件，直接调用 `https://<api-domain>`。GitHub Pages 不代理 API。API 和监控容器由 Compose 管理，1Panel 的 OpenResty 统一管理域名、证书和 HTTPS 反向代理。SQLite 使用独立持久卷；API 仅发布宿主回环地址 `127.0.0.1:18765`，不占用 80/443，不对公网开放后端端口。默认 Docker 网段为 `172.30.87.0/24`。本配置要求 OpenResty 使用宿主机 host 网络。

远程模式只接受用户 Token：浏览器 sessionStorage 保存 GitHub Token，HTTPS `POST /api/sessions` 验证后取得随机应用会话凭据。服务器只在内存保留这两种凭据，不写入数据库或日志。服务器管理者仍能访问进程内存，因此用户必须信任部署者。数据库仅保存带持久密钥的 HMAC 指纹及任务数据；相同 Token 可找回任务，不同 Token 即使属于同一账号也相互隔离。

浏览器每 30 秒发送心跳。最后一个会话超过 180 秒无心跳时暂停任务、取消在途请求并释放凭据；后台标签页休眠也可能触发。退出按钮撤销当前会话并清除当前标签页的存储。刷新页面会建立新会话，旧会话最多保留到租约到期；复制标签页、浏览器恢复会话等行为不能保证 Token 即时消失。服务器重启后不继续 GitHub 查询，重新认证后必须手动点击“恢复任务”。恢复不重置预算和冷却，普通“重试”仍启动新的预算周期。

本地模式使用 `APP_MODE=local`，保留旧的服务端 Token 和无限模式，只应绑定 `127.0.0.1`。远程模式默认启用，拒绝非空 `GITHUB_TOKEN` 和无限模式，并不注册旧的凭据配置接口。不得将本地 SQLite 或 `.env` 复制到远程服务；迁移结果使用 JSON 快照导入。

## 服务器首次部署

前提：Linux VPS 已安装 Docker Engine 与 Compose 插件，拥有一个 API 域名，A/AAAA 记录指向服务器；只发布已可达的地址。开放 TCP 80/443（证书申请及 HTTPS）和受限的管理 SSH 端口。API 必须能连接 `api.github.com:443`。API 容器限制 512 MiB 内存、不使用额外 swap，并禁用 core dump；主机同样不应启用进程内存转储。建议使用专用部署用户和目录。

在服务器部署目录准备项目代码与以下文件，**不要复制本地 `.env`、SQLite、日志或虚拟环境**：

```sh
cp .env.remote.example .env.remote
chmod 600 .env.remote
python3 -c "import secrets; print(secrets.token_hex(32))"
```

将生成值填入 `.env.remote` 的 `SESSION_SECRET`。配置：

| 参数 | 示例与说明 |
|---|---|
| `APP_IMAGE` | `classmates-explorer:2026-09-22-01`，每次发布使用新的版本标签 |
| `ALLOWED_ORIGINS` | `["https://YOUR-ACCOUNT.github.io"]`，origin 不含仓库路径或末尾斜线 |
| `SESSION_SECRET` | 至少 32 字符的随机值，备份并跨重启保留；不是 GitHub Token |

切勿公开 `SESSION_SECRET`，更换它会使旧任务的凭据归属无法匹配。一个 `github.io` 账号下的不同项目共享 origin；CORS 无法按仓库路径区分，请只在可信账号的 Pages origin 发布此应用。

```sh
docker compose --env-file .env.remote config --quiet
docker compose --env-file .env.remote build api
docker compose --env-file .env.remote up -d
curl --fail http://127.0.0.1:18765/healthz
```

Compose 默认禁用应用访问日志，提供的 OpenResty 配置片段关闭该站点访问日志，API 错误不包含底层凭据或请求对象。容器日志设定大小上限。不要启用 HTTP 调试、记录 Authorization 或打印会话响应。

API 仅信任回环地址和 Docker 网关 `172.30.87.1` 传来的代理头，适配 host 网络 OpenResty 经宿主回环端口转发的流量。OpenResty 必须覆盖客户端传入的 `X-Forwarded-For`，且不能将任意来源配置为可信 real-IP 代理。若部署主机已有同网段，须同步修改 Compose 的网段、固定地址、Dockerfile 中的可信网关和监控允许地址。不要改成信任所有代理。

远程默认最大 1000 个 Fork、全局最多 5 个排队任务，每个凭据最多 1 个活动任务。提交按 IP 和凭据分别每分钟 5 次；会话创建按 IP 每分钟 5 次，最多 100 个会话、同凭据最多 10 个会话。只有一个 worker；凭据冷却会让出执行队列，共享出口二级限流会暂停所有凭据的下一次查询。不要横向扩容或让多个实例共享同一 SQLite。

## 在 1Panel 配置 OpenResty

1. 在域名服务商添加 API 子域名的 A 记录指向服务器公网 IP。Lightsail 与启用的系统防火墙放行 80/443；无需放行 18765/8765。面板管理端口继续限制来源。
2. 在 1Panel 应用商店安装或使用已有 OpenResty。在“容器”确认其网络模式为 `host`；也可运行 `docker ps --format 'table {{.Names}}\t{{.Image}}'` 找到名称，再执行 `docker inspect --format '{{.HostConfig.NetworkMode}}' <实际OpenResty容器名>`。如果不是 host，暂不要使用回环代理地址，也不要贸然修改现有 OpenResty 网络；先按已有网站情况适配网络。
3. 在“网站 → 创建网站”选择“反向代理”，主域名填 `api.example.com`，代理地址填 `http://127.0.0.1:18765`。保存后在该网站申请/选择 SSL 证书，启用 HTTPS 和 HTTP 跳转 HTTPS。
4. 在网站配置中按 `deploy/openresty.conf` 配置：替换生成的 `location /` 代理块，增加 `/internal` 精确匹配和 `/internal/` 前缀拒绝规则；关闭该站点代理缓存，将上传上限设为 11m，覆盖 X-Forwarded-For 并透传 Authorization。已有同名指令应修改，不重复添加。保留 1Panel 生成的域名、监听、证书和 ACME 配置；不要把片段替换成整个站点文件。不要额外添加 CORS 响应头，CORS 由应用统一处理。
5. 使用 1Panel 的配置检查，成功后重载 OpenResty，再验证：

```sh
curl --fail https://api.example.com/healthz
curl --fail -H 'Origin: https://YOUR-ACCOUNT.github.io' https://api.example.com/api/config
curl -o /dev/null -s -w '%{http_code}\n' https://api.example.com/internal/metrics
```

前两项应成功，最后一项必须返回 404。若返回 502，先检查本机 `http://127.0.0.1:18765/healthz`，再检查 OpenResty 的网络模式和代理地址。API 无需在 1Panel 中另外创建 Python 运行环境。

如果此前已按旧配置启动 Caddy：先用 `docker ps` 确认旧 Caddy 容器的实际名称，在确定无其他业务依赖后手动 `docker stop <实际旧Caddy容器名>` 释放 80/443，再启动 OpenResty。不要执行 `down -v` 或 `--remove-orphans`；旧证书卷和容器保留。仓库中的 `deploy/Caddyfile` 仅保留作旧部署参考，当前 Compose 不会启动 Caddy。

## 发布 Pages

1. 在仓库 Settings → Pages 中选择 GitHub Actions。
2. 手动运行 **Publish Pages manually**，填写完整 API origin，例如 `https://api.example.com`。
3. 工作流运行回归测试、JS 语法检查和静态构建，然后发布 `dist/pages`。
4. 打开项目站点，验证配置加载、输入自己的测试 Token、查询、导出和退出。

构建仅复制 `index.html`、前端 JS、CSS、公开 API 地址和 `.nojekyll`，不导入 Python 应用配置，不读取 `.env`。站点设置 CSP 限制脚本为同源、连接仅允许配置的 API。静态资源使用相对路径，兼容项目子目录。API 的 CORS 允许 Authorization、Content-Type，并暴露 Content-Disposition、Retry-After；会话与业务响应均为 `Cache-Control: no-store`。

本地检查静态构建：

```sh
python scripts/build_pages.py --api-base https://api.example.com --output dist/pages-release-01
```

必须选择空输出目录。构建不会自动删除旧产物。Pages 工作流仅手动触发，不包含服务器 SSH 部署，也不自动 commit、push 或创建 PR。

## API 使用

远程会话创建：

```http
POST /api/sessions
Authorization: Bearer <用户的 GitHub Token>
```

返回 `201`：`{"session_token":"...","expires_in":180}`。之后访问任务、导入、导出、心跳、退出接口时，使用 `Authorization: Bearer <session_token>`。会话创建不会接收 URL 查询参数中的 Token，也不会提供公共 Token。

| 接口 | 说明 |
|---|---|
| `GET /api/config` | 公开运行模式与限额，不包含凭据 |
| `POST /api/session/heartbeat` | 更新租约；普通轮询不会续租 |
| `POST /api/session/logout` | 撤销当前会话 |
| `GET /api/tasks?page=1&per_page=20` | 当前凭据的任务，最多每页 100 个 |
| `POST /api/tasks/{id}/resume` | 恢复 `paused_credentials` 任务，保留预算及检查点 |
| `GET /healthz` | 进程与数据库就绪状态，不调用 GitHub |

原有任务接口、分页筛选和快照 v1 继续有效。没有会话或会话失效为 `401`，他人的任务为 `404`，操作状态冲突为 `409`，限流为 `429` 并带 `Retry-After`。导入也要求会话，导入数据不会触发上游查询。

## 监控、备份与回滚

`monitor` 容器每 60 秒读取仅内部可访问的健康检查和指标，输出不含凭据的 JSON。OpenResty 屏蔽 `/internal` 和 `/internal/*`，应用只允许回环地址和监控容器读取指标。指标包括：

- 数据库含 WAL 的总大小，默认大于 5 GiB 告警。
- 所在磁盘剩余空间，默认小于 1 GiB 告警。
- 排队任务数，达到 5 告警。
- 最近 5 分钟最多 10000 个 API 响应中，请求至少 20 次且 5xx 比例达到 5% 告警；另记录 429 次数。

通过 Compose 给监控服务配置 `MAX_DATABASE_BYTES`、`MIN_DISK_FREE_BYTES`、`MAX_QUEUE_ALERT` 可调整阈值。日志中的非空 `alerts` 是告警信号；需由部署者接入自己的日志告警渠道，本项目不自动发送外部消息。

```sh
docker compose --env-file .env.remote logs --tail 100 monitor
docker compose --env-file .env.remote exec api python deploy/monitor.py
# 指定新的文件名；存在时拒绝覆盖。
docker compose --env-file .env.remote exec api python deploy/backup.py /data/backups/before-release-01.sqlite3
```

备份采用 SQLite 在线 backup API 并执行完整性检查，不直接复制活动 WAL 文件。备份文件保留在持久卷中；用 `docker compose cp` 另存到安全的异机位置，同时备份 `SESSION_SECRET`。备份失败产生的文件不会自动删除，须检查后手动处理。

发布新版本时先备份，再构建新标签镜像并执行 `up -d`。保留旧镜像、旧静态产物、旧配置和备份。回滚时把 `APP_IMAGE` 改回旧标签并重新启动，Pages 手动发布对应旧版本。若后续版本改变数据库结构，应将备份恢复到**新卷**后切换，不覆盖当前数据卷。本版本远程使用新数据库，不迁移或删除本地数据。

## 验证

```sh
python -m pip install '.[test]'
python -m pytest -q
node --check app/static/app.js
# 可选真实浏览器测试：使用两套本地 HTTPS 服务，GitHub 完全模拟。
python -m pip install '.[test,browser]'
python -m playwright install chromium
RUN_BROWSER_TESTS=1 python -m pytest tests/test_browser.py -q
```

也可通过 `PLAYWRIGHT_CHROMIUM_EXECUTABLE` 指定已安装的 Chromium 可执行文件。浏览器测试忽略测试用自签名证书，仅限测试；生产不得关闭 TLS 校验。

服务器验收应使用两个不同测试 Token，验证互相看不到任务、导入归属、下载成功、关闭页面后暂停，以及 `docker compose restart api` 后需要重新认证和手动恢复。真实 GitHub 冒烟测试仍显式启用且最多 3 个 Fork；不要使用生产 Token 执行默认测试。
