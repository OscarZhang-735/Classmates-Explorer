# Classmates Explorer

Enter the URL of a public GitHub repository to explore its direct forks, their owners' public profiles, and their site-wide contributions over the past 365 days. Results can be filtered and sorted by Explorer Score, contribution activity, or fork creation time.

## 1. Usage

### 1.1 Web / Remote Deployment

You can access the web version through [Classmates-Explorer](https://oscarzhang-735.github.io/Classmates-Explorer/).

#### Prepare a GitHub Token

1. Open the [GitHub Personal Access Token settings page](https://github.com/settings/personal-access-tokens) and click **Generate new token**.
2. Set the token name to `Classmates Explorer`, choose an expiration date, and select your own account as the **Resource owner**.
3. Under **Repository access**, select **Public repositories**. Leave permissions at their default values and do not grant write or administrative permissions.
4. Generate the token and copy it immediately. GitHub will not display the full token again after you leave the page, so store it securely.

For more information, see the [GitHub documentation](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

Only use your token on websites you trust. Do not include it in source code, screenshots, or chat messages.

#### Start Exploring

1. Open the website and expand **GitHub API Configuration**.
2. Paste your token and click **Use Token for Current Session**. Do not add the `Bearer ` prefix.
3. After **Token Configured** is displayed, enter a repository URL such as `https://github.com/owner/repo`, then click **Explore**.
4. In the results, choose a sorting method, filter by account or fork creation time, or search by username.
5. When the task is complete, you can export the results as JSON or CSV. Exported JSON files can later be imported for viewing, but imported tasks cannot continue making requests to GitHub.
6. When finished, click **Clear Credentials and Exit**.

The token is stored in the browser's `sessionStorage`. The server receives it over HTTPS and keeps it temporarily in memory only. It is not written to server files, databases, or logs.

Some browsers may restore previously closed sessions. On shared or public devices, always use the logout function when you are finished.

Logging out of the website does not revoke the GitHub token. If you no longer need the token or suspect that it has been exposed, revoke it from your GitHub settings.

#### Self-Hosted Remote Deployment

The default architecture is:

**GitHub Pages → HTTPS API → GitHub / SQLite**

The server requires Linux, Docker, and OpenResty. Each user provides their own GitHub token.

### 1.2 Local Deployment

Install **Python 3.12 or later**, download the project, and enter the project directory.

For GitHub token creation instructions, see Section 1.1.

#### Windows Quick Start

Double-click `start.bat`.

The script will automatically prepare the environment, install dependencies, and open the web interface. Save your own GitHub token on the page, then enter a repository URL to begin exploring.

#### Manual Startup (PowerShell)

For the first installation:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Set your token in the `GITHUB_TOKEN` field inside `.env`, or save it later through the local web interface.

Then run:

```powershell
$env:APP_MODE="local"
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --workers 1
```

Open:

http://127.0.0.1:8765

Then follow the same workflow as the web version.

Press `Ctrl+C` in the terminal to stop the service.

In local mode, the token is stored in an environment variable or in `.env`. Do not commit `.env` to version control.

Tasks and results are stored in `explorer.sqlite3` by default, allowing active tasks to continue after restarting the application.

Run only one worker and do not allow multiple application instances to share the same database.

If you change the GitHub token, existing active tasks must be recreated.

Local mode is intended for use on the same machine only. If you want to expose the service remotely, use the remote deployment configuration described in Section 1.1.

### 1.3 JSON API

The local API endpoint is:

`http://127.0.0.1:8765`

Interactive API documentation is available at:

`/docs`

The OpenAPI specification is available at:

`/openapi.json`

For remote API access, use the HTTPS API endpoint configured for your deployment.

## 2. Explorer Score

The Explorer Score ranges from 0 to 100 and is intended to help compare publicly visible professional signals and activity levels.

| Dimension                        | Components                                                                                         |
| -------------------------------- | -------------------------------------------------------------------------------------------------- |
| Professional Signals (50 points) | Featured Repository Stars: 25, Collaboration: 10, Account Age: 10, Non-Fork Public Repositories: 5 |
| Activity (50 points)             | Estimated Public Contributions: 28, Total Contributions: 7, Active Weeks: 10, Recent Activity: 5   |

## 3. Limits and Caching

By default, the application only retrieves direct forks of a public repository. Forks are not traversed recursively.

A maximum of 1,000 forks are collected per task. If this limit is reached, the result is marked as `truncated`, which does not mean that the complete fork network has been retrieved.

The contribution window covers the 365 days before the task creation date at 00:00 UTC. The task creation day itself is excluded.

Tasks using the same repository, token, statistics date, and fork limit may be reused:

* Active tasks are reused directly.
* Completed results are cached for 1 hour by default.

Data and caches associated with different GitHub tokens are isolated from each other.

Common configuration options are listed below. See [.env.example](.env.example) for the full configuration list. Remote deployments should use [.env.remote.example](.env.remote.example).

| Configuration                           |   Default | Purpose                                                                        |
| --------------------------------------- | --------: | ------------------------------------------------------------------------------ |
| `MAX_FORKS`                             |      1000 | Maximum number of forks collected per task                                     |
| `MAX_IMPORT_BYTES`                      |  10485760 | Maximum JSON import size, 10 MB                                                |
| `MAX_QUEUED_TASKS`                      |         5 | Maximum number of queued tasks, in addition to one running task                |
| `SUBMISSIONS_PER_MINUTE`                |         5 | Maximum task submissions per minute                                            |
| `GITHUB_REQUEST_INTERVAL`               |         1 | Delay between upstream GitHub requests, in seconds                             |
| `MAX_TASK_REQUESTS` / `MAX_TASK_POINTS` | 300 / 500 | Per-task request count and point budget                                        |
| `RESULT_CACHE_SECONDS`                  |      3600 | Cache duration for completed results                                           |
| `OWNER_CACHE_SECONDS`                   |     86400 | Cache duration for GitHub user profile data                                    |
| `CONTRIBUTION_CACHE_SECONDS`            |     21600 | Cache duration for contribution data                                           |
| `UNLIMITED_MODE`                        |     false | Local-mode-only option to disable application-level budgets and the fork limit |

When upstream timeouts or resource constraints occur, the application automatically retries requests and reduces query batch sizes while preserving already submitted progress.

Each retry attempt still consumes the task's request budget.

If GitHub rate limiting is encountered, the application waits for the cooldown period before continuing. Restarting the application does not clear the cooldown state.

When a remote task is re-authenticated and resumed, its original request and point budgets are preserved.

--- 
# Classmates Explorer
  
输入 GitHub 公开仓库地址，查看其直接 Fork、所有者公开资料和近 365 天的全站贡献，按探索评分、贡献量或创建时间筛选和排序。  
  
## 1. 使用方式  
  
### 1.1 网页端/远程部署  
  
可通过 [Classmates-Explorer](https://oscarzhang-735.github.io/Classmates-Explorer/) 进入网页端并使用。 
  
#### 准备 GitHub Token  

1. 打开 [GitHub Token 设置页](https://github.com/settings/personal-access-tokens)，点击 **Generate new token**。  
2. 名称填写 `Classmates Explorer`，设置有效期，**Resource owner** 选择自己的账号。  
3. **Repository access** 选择 **Public repositories**；权限保持默认，不添加写入或管理权限。  
4. 生成后复制完整 Token。离开页面后无法再次查看，请妥善保存。  
  
详细说明见 [GitHub 官方文档](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)。仅在信任的站点使用 Token，不要放入代码、截图或聊天中。  
  
#### 开始查询  
  
1. 进入网页，展开 **GitHub API 配置**。  
2. 粘贴 Token，点击 **在当前会话使用 Token**，无需添加 `Bearer ` 前缀。  
3. 显示 **Token 已配置** 后，输入 `https://github.com/owner/repo`，点击 **探索**。  
4. 在结果中选择排序方式、账号或 Fork 创建时间范围，再搜索用户名。
5. 任务完成后，可导出 JSON 或 CSV。JSON 可重新导入查看，导入任务不能继续向 GitHub 查询。
6. 使用完请点击 **清除凭据并退出**。
  
Token 保存在浏览器 `sessionStorage`，服务器通过 HTTPS 接收并在内存中临时使用，不写入服务器文件、数据库或日志。浏览器可能恢复已关闭的会话，对于公用设备请务必退出。  
  
网页退出不会撤销 GitHub Token。不再使用或发生泄露时，请到 GitHub 设置页撤销。  
  
#### 自行远程部署  
  
默认采用 **GitHub Pages → HTTPS API → GitHub / SQLite** 的服务架构。服务器需要 Linux、Docker 和 OpenResty，每位使用者自行提供 Token。  
  
### 1.2 本地部署  
  
安装 **Python 3.12 或以上版本**，下载项目并进入项目目录。Token 创建方式见 1.1。  
  
#### Windows 快速启动  
  
双击 `start.bat`，脚本会自动准备环境、安装依赖并打开网页。在页面中保存自己的 Token，再输入仓库地址开始查询。  
  
#### 手动启动（PowerShell）  
  
首次安装：  
  
```powershell  
python -m venv .venv  
.\.venv\Scripts\python -m pip install -e .  
if (!(Test-Path .env)) { Copy-Item .env.example .env }  
```  
  
在 `.env` 的 `GITHUB_TOKEN` 中填写 Token，或启动后在本地网页中保存。然后运行：  
  
```powershell  
$env:APP_MODE="local"  
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --workers 1  
```  
  
打开 <http://127.0.0.1:8765>，按网页端流程查询。终端按 `Ctrl+C` 停止服务。  
  
本地模式的 Token 存在环境变量或 `.env` 中；不要提交 `.env`。任务和结果默认保存在 `explorer.sqlite3`，重启后继续活动任务。只运行一个 worker，不要让多个实例共用数据库。更换 Token 后，旧活动任务需重新创建。  
  
本地模式只供本机使用；对外提供服务请按 1.1 配置远程模式。  
  
### 1.3 JSON API  
  
本地接口地址为 `http://127.0.0.1:8765`，交互文档在 `/docs`，接口定义在 `/openapi.json`。远程调用使用部署好的 HTTPS API 地址。  
  
  
## 2. 探索评分  
  
探索评分为 0–100 分，帮助比较公开资料中的专业信号和活跃度：  
  
| 维度 | 构成 |  
|---|---|  
| 专业信号（50 分） | 代表作 Stars 25、协作 10、账号年限 10、非 Fork 公开仓库 5 |  
| 活跃度（50 分） | 公开贡献估计值 28、贡献总量 7、活跃周数 10、最近活跃 5 |  

  
## 3. 限制与缓存  
  
默认只查询公开仓库的直接 Fork，不递归，最多采集 1,000 个；达到上限会标记 `truncated`，不代表完整 Fork 网络。贡献窗口为任务创建日 UTC 零点之前的 365 天，不含当天。  
  
相同仓库、Token、统计日期和 Fork 上限的任务可复用：活动任务直接复用，完成结果默认缓存 1 小时。不同 Token 的数据与缓存相互隔离。  
  
常用配置如下，完整列表见 [.env.example](.env.example)；远程部署使用 [.env.remote.example](.env.remote.example)。  
  
| 配置 | 默认值 | 用途 |  
|---|---:|---|  
| `MAX_FORKS` | 1000 | 单任务 Fork 上限 |  
| `MAX_IMPORT_BYTES` | 10485760 | JSON 导入上限（10 MB） |  
| `MAX_QUEUED_TASKS` | 5 | 排队任务上限，另有 1 个执行中任务 |  
| `SUBMISSIONS_PER_MINUTE` | 5 | 每分钟提交限制 |  
| `GITHUB_REQUEST_INTERVAL` | 1 | 上游请求间隔（秒） |  
| `MAX_TASK_REQUESTS` / `MAX_TASK_POINTS` | 300 / 500 | 请求次数与 points 预算 |  
| `RESULT_CACHE_SECONDS` | 3600 | 完成结果复用时间 |  
| `OWNER_CACHE_SECONDS` | 86400 | 用户资料缓存时间 |  
| `CONTRIBUTION_CACHE_SECONDS` | 21600 | 贡献缓存时间 |  
| `UNLIMITED_MODE` | false | 仅本地模式可解除应用级预算和 Fork 上限 |  
  
遇到上游超时或资源限制时，程序自动重试并缩小查询批次，保留已提交的进度；每次尝试仍消耗请求预算。遇到 GitHub 限流则等待冷却，重启不会清除冷却时间。远程任务重新认证并恢复时保留原预算。