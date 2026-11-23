# Telegram API 与首次登录教程

## 获取 API_ID 与 API_HASH

1. 用浏览器打开 <https://my.telegram.org>，使用自己的 Telegram 手机号登录。
2. 输入 Telegram 发到应用内的登录验证码。
3. 打开 **API development tools**。
4. 填写应用名称，例如 `job-bot`；平台选择 `Desktop`；提交后页面会显示 `api_id` 和 `api_hash`。
5. `api_hash` 相当于应用私钥：不要截图发群、不要提交到 Git。

## 保存到本机

在这台腾讯云 134（`/home/ubuntu/job-bot`）上运行：

```bash
cd /home/ubuntu/job-bot
./scripts/configure_telegram_credentials.sh
```

按提示输入 `API_ID`、`API_HASH` 和国际格式手机号。它们只写入本机 `job-bot.env`，文件权限应为仅运行用户可读。不要再 SSH 到 81。

## 完成首次授权

先在 Telegram 客户端手动加入所有需要读取的私有群；本项目不会自动加入群组。然后运行：

```bash
cd /home/ubuntu/job-bot
docker compose run --rm --no-deps --entrypoint python app -m worker.login
```

终端会要求输入 Telegram 验证码；若账号启用了两步验证，也会要求输入两步验证密码。成功后，会话只保存到本机 `data/telegram.session`，不进入 Git。

Telegram 出网走本机 mihomo `127.0.0.1:7897`。

## 启用定时扫描

确认页面中来源都正确、首次手动扫描成功后，再启用后台自动检测。定时任务只读取新增消息，不发送 Telegram 消息。
