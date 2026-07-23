# job-bot

一个自托管的 Telegram 招聘信息筛选机器人：用你自己已授权的 Telegram 账号增量扫描指定群组/频道，先用规则做便宜的粗筛，再交给一个本地/自托管的 LLM（大语言模型）做语义复核，最后在一个轻量网页工作台里让你逐条查看、去重、标记状态。

## 为什么不只靠关键词规则

招聘信息经常是"一贴多岗"：同一条消息里混着 Flutter 工程师、运维、产品经理好几个不相关的岗位。纯关键词规则只能判断"消息里出现了产品经理这个词"，却分不清这条消息到底是不是真的在招产品经理——很容易把"顺带提了一句产品经理"的无关消息当成命中。

job-bot 的做法是：规则层先做**便宜的粗筛**（同时命中"目标岗位词"和"招聘意图词"、且不含硬性排除词才算候选），把候选消息交给 LLM 做**按段落级别的语义判断**——LLM 会识别出消息里是否真的存在一段明确的目标岗位描述，不会因为同一条消息里提到了其他岗位就被带偏。

## 核心能力

- **增量扫描，不重不漏**：每个来源独立维护游标（`last_scanned_message_id`），只读取新增消息；任务失败不会推进游标。
- **规则粗筛 + LLM 语义复核**：LLM 只处理规则筛出的候选消息，控制推理成本；LLM 判断结果包含岗位、薪资、地点、是否远程、排除原因、置信度、判断理由的完整结构化字段。
- **岗位指纹去重**：按公司名、职位、联系方式、职位描述计算指纹，同一机会即使被转发到多个群也只保留一条。
- **远程/坐班过滤**：可按需只保留明确写明远程的岗位，排除明确要求到岗坐班的信息。
- **联系方式抽取**：从消息正文里抽取 Telegram 用户名、邮箱、电话、微信等，正则兜底 + LLM 抽取双重来源合并去重。
- **网页工作台**：按状态（待处理/保留/已联系/忽略）筛选、搜索、标记，原始消息链接一键跳转回 Telegram。
- **对本地 LLM 友好**：不依赖 OpenAI 专属的严格 JSON 模式，对本地模型常见的输出偏差（markdown 代码块包裹、非精确枚举值）做了防御性解析，兼容任何 OpenAI 兼容接口（Ollama、vLLM、sglang、云端 API 均可）。
- **调度可控**：默认每天定时运行一次（本地推理模型速度有限，没必要频繁扫描，也把算力留给其他用途），可在 `app/config.py` 里调整。

## 架构

```text
Telegram（已授权账号）
   │  增量读取（游标去重）
   ▼
规则粗筛（config/filters.yaml：目标岗位词 + 招聘意图词 + 硬性排除词）
   │  候选消息
   ▼
LLM 语义复核（OpenAI 兼容接口，任意模型）
   │  结构化 JSON：岗位/薪资/地点/远程与否/排除原因/置信度/联系方式
   ▼
去重（岗位指纹）─→ 非远程排除 ─→ 写入 Postgres
   │
   ▼
FastAPI 网页工作台（HTTP Basic 认证）
```

技术栈：FastAPI + SQLAlchemy 2.x + PostgreSQL + Alembic（数据库迁移）+ Telethon（Telegram 客户端库）+ APScheduler（定时任务）+ 任意 OpenAI 兼容 LLM 接口。

## 快速开始

```bash
git clone <this-repo-url> job-bot && cd job-bot
cp .env.example job-bot.env   # 按需修改，权限建议 chmod 600
cp config/monitored_chats.yaml.example config/monitored_chats.yaml
cp config/filters.yaml.example config/filters.yaml
```

编辑 `job-bot.env`：

| 变量 | 说明 |
| --- | --- |
| `JOB_BOT_ADMIN_PASSWORD` | 网页工作台的管理员密码（HTTP Basic，用户名固定为 `admin`） |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | 从 <https://my.telegram.org> 获取，见 [docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md) |
| `TELEGRAM_PHONE` | 登录用 Telegram 手机号（国际格式） |
| `DATABASE_URL` | PostgreSQL 连接串 |
| `OPENAI_API_KEY` / `OPENAI_MODEL` / `OPENAI_BASE_URL` | 任意 OpenAI 兼容 LLM 接口；本地部署可指向自己的 Ollama/vLLM/sglang 网关 |
| `TELEGRAM_PROXY_HOST` / `TELEGRAM_PROXY_PORT` | 如果你的网络环境需要经代理才能连上 Telegram，这里配 SOCKS5 代理；不需要则留空 |

然后：

```bash
docker compose up -d postgres
docker compose run --rm app alembic upgrade head        # 建表
docker compose run --rm app python -m worker.seed_chats  # 导入监控来源
```

完成 Telegram 首次登录授权（详见 [docs/TELEGRAM_SETUP.md](docs/TELEGRAM_SETUP.md)），然后启动主服务：

```bash
docker compose up -d app
```

访问 `http://<你的服务器地址>:8888`，用户名 `admin`，密码为 `JOB_BOT_ADMIN_PASSWORD`。

## 目录结构

```text
job-bot/
├── app/
│   ├── classifier/       # 规则粗筛 + LLM 客户端 + 结构化输出 schema
│   ├── dedup/             # 岗位指纹计算
│   ├── extractor/         # 联系方式抽取与归一化
│   ├── database/          # SQLAlchemy 数据模型
│   ├── services/          # Telegram 扫描服务
│   ├── scheduler/         # 定时任务
│   ├── api/               # 健康检查、管理端 API
│   └── dashboard.py        # 网页工作台路由
├── worker/
│   ├── daily_job.py        # 每日任务编排（扫描 + LLM 复核）
│   ├── classify_pending.py # LLM 分类主流程
│   ├── login.py            # Telegram 首次登录
│   └── seed_chats.py       # 导入监控来源
├── config/                # 规则/来源配置模板（真实配置不进 Git）
├── alembic/                # 数据库迁移
├── docs/                   # Telegram 授权教程
└── tests/                  # 单元测试
```

## 隐私与安全边界

- 只以你本人明确授权的 Telegram 会话读取指定的公开或已加入群组/频道；不发送消息、不邀请成员、不自动回复。
- 不保存 Telegram 密码、验证码、两步验证密码；`.env`、登录会话文件、数据库均被 `.gitignore` 排除，不会进入版本控制。
- 网页工作台默认只做 HTTP Basic 认证，建议只在受信任的局域网内暴露，或自行加一层反向代理认证。
- `config/monitored_chats.yaml`、`config/filters.yaml` 是你的真实监控来源和筛选策略，同样被 `.gitignore` 排除——仓库里只有 `.example` 模板。

## License

[MIT](LICENSE)
