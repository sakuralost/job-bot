# Telegram API 与首次登录教程

## 1. 获取 API_ID 与 API_HASH

1. 用浏览器打开 <https://my.telegram.org>，使用自己的 Telegram 手机号登录。
2. 输入 Telegram 发到应用内的登录验证码。
3. 打开 **API development tools**。
4. 填写应用名称（例如 `job-bot`）；平台选择 `Desktop`；提交后页面会显示 `api_id` 和 `api_hash`。
5. `api_hash` 相当于应用私钥：不要截图发群、不要提交到 Git、不要写入任何备份/同步目录。

## 2. 写入服务器配置

在部署服务器上运行：

```bash
cd job-bot
cp .env.example /path/to/job-bot.env   # 首次部署时创建，权限建议 600
./scripts/configure_telegram_credentials.sh
```

按提示输入 `API_ID`、`API_HASH` 和国际格式手机号（如 `+86138...`）。凭据只写入该 env 文件，不进入 Git。

## 3. 完成首次授权

先在 Telegram 客户端手动加入所有需要读取的私有群/频道；本项目不会自动加入群组。然后运行：

```bash
docker compose run --rm --no-deps app python -m worker.login
```

终端会要求输入 Telegram 验证码；若账号启用了两步验证，还会要求输入两步验证密码。成功后，登录会话只保存在本地 `data/telegram.session`，不会进入 Git 或任何同步目录。

## 4. 导入监控来源并启用定时扫描

1. 复制 `config/monitored_chats.yaml.example` 为 `config/monitored_chats.yaml`，填入要监控的群组/频道的 `telegram_chat_id`（可在已授权的会话里现场解析获得，不要依赖会变化的显示名称）。
2. 运行 `docker compose run --rm app python -m worker.seed_chats` 导入数据库。
3. 首次手动跑一遍确认无误后，定时任务会按 `app/config.py` 里 `scan_times` 配置的时间自动运行（默认每天一次，因为本地 LLM 推理较慢，一天一次足够，也把 GPU 资源留给其他用途）；定时任务只读取新增消息，不会发送 Telegram 消息。
