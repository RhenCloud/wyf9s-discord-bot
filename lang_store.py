from pathlib import Path

from loguru import logger as l
from yaml import safe_dump, safe_load

import utils as u

LangCode = str

_LANG_FILE_NAME = "lang_settings.yaml"


class LangStore:
    def __init__(self):
        self._user_lang: dict[str, LangCode] = {}  # str(user_id) -> lang
        self._guild_lang: dict[int, LangCode] = {}  # guild_id -> lang
        self._load()

    def _load(self):
        read_path = u.get_data_path(_LANG_FILE_NAME, for_read=True)
        if Path(read_path).exists():
            try:
                with open(read_path, "r", encoding="utf-8") as f:
                    data = safe_load(f) or {}
                raw_users = data.get("users", {})
                self._user_lang = {str(k): v for k, v in raw_users.items()}
                raw_guilds = data.get("guilds", {})
                self._guild_lang = {int(k): v for k, v in raw_guilds.items()}
                l.debug(
                    f"[lang] Loaded {len(self._user_lang)} user + {len(self._guild_lang)} guild lang settings"
                )
            except Exception as e:
                l.warning(f"[lang] Failed to load lang settings: {e}")
                self._user_lang = {}
                self._guild_lang = {}

    def _save(self):
        write_path = u.get_data_path(_LANG_FILE_NAME)
        try:
            data = {
                "users": dict(self._user_lang),
                "guilds": {str(k): v for k, v in self._guild_lang.items()},
            }
            with open(write_path, "w", encoding="utf-8") as f:
                safe_dump(data, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            raise RuntimeError(f"Cannot write to {write_path}: {e}")

    def set_user(self, user_id: int | str, lang: LangCode):
        self._user_lang[str(user_id)] = lang
        self._save()

    def set_guild(self, guild_id: int, lang: LangCode):
        self._guild_lang[guild_id] = lang
        self._save()

    def get_user(self, user_id: int | str) -> LangCode | None:
        return self._user_lang.get(str(user_id))

    def get_guild(self, guild_id: int) -> LangCode | None:
        return self._guild_lang.get(guild_id)

    def resolve(self, user_id: int | str, guild_id: int | None = None) -> LangCode:
        if lang := self._user_lang.get(str(user_id)):
            return lang
        if guild_id is not None and (lang := self._guild_lang.get(guild_id)):
            return lang
        return "zh"
