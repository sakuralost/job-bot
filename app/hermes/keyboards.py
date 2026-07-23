from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def job_keyboard(job_id: int, source_url: str | None, username: str | None) -> InlineKeyboardMarkup:
    buttons = []
    if source_url:
        buttons.append([InlineKeyboardButton("打开原消息", url=source_url)])
    if username:
        buttons.append([InlineKeyboardButton("联系对方", url=f"https://t.me/{username}")])
    buttons.append([
        InlineKeyboardButton("标记已联系", callback_data=f"contact:{job_id}"),
        InlineKeyboardButton("忽略岗位", callback_data=f"ignore:{job_id}"),
        InlineKeyboardButton("永久屏蔽联系人", callback_data=f"block:{job_id}"),
    ])
    return InlineKeyboardMarkup(buttons)
