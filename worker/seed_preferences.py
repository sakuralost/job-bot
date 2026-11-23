from __future__ import annotations

import json
from pathlib import Path

from app.db import init_db, save_preferences


def main() -> None:
    profile = json.loads(Path("/app/config/preferences.json").read_text(encoding="utf-8"))
    profile["required_any_groups"] = [profile["role_keywords"], profile["recruitment_keywords"]]
    save_preferences(profile)
    print(f"已预置 {len(profile['role_keywords'])} 个岗位词、{len(profile['recruitment_keywords'])} 个招聘信号词和 {len(profile['excluded_keywords'])} 个排除词。")


if __name__ == "__main__":
    init_db()
    main()
