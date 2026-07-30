from datetime import datetime, timezone
from pathlib import Path

from loguru import logger as l
from pydantic import BaseModel, Field
from yaml import safe_dump, safe_load

import utils as u


class PermRule(BaseModel):
    id: int
    users: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    module: str | None = None
    command: str | None = None
    global_scope: bool = False
    guild_id: int | None = None
    created_by: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def matches_user(self, user_id: str | int) -> bool:
        sid = str(user_id)
        return sid in self.users

    def matches_role(self, role_id: str | int) -> bool:
        sid = str(role_id)
        return sid in self.roles


class PermStore:
    def __init__(self, path: str = "perm.yaml"):
        self._name = path
        self._path = u.get_data_path(path)
        self.rules: list[PermRule] = []
        self._next_id = 1
        self._load()

    def _load(self):
        read_path = u.get_data_path(self._name, for_read=True)
        if Path(read_path).exists():
            try:
                with open(read_path, "r", encoding="utf-8") as f:
                    data = safe_load(f) or {}
                self.rules = [PermRule.model_validate(r) for r in data.get("rules", [])]
                if self.rules:
                    self._next_id = max(r.id for r in self.rules) + 1
                l.debug(f"[perm] Loaded {len(self.rules)} rules from {read_path}")
            except Exception as e:
                l.warning(f"[perm] Failed to load perm rules: {e}")
                self.rules = []

    def _save(self):
        try:
            data = {"rules": [r.model_dump(mode="json") for r in self.rules]}
            with open(self._path, "w", encoding="utf-8") as f:
                safe_dump(data, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            raise RuntimeError(f"Cannot write to {self._path}: {e}")

    def add(
        self,
        users: list[str],
        roles: list[str],
        module: str | None,
        command: str | None,
        global_scope: bool,
        guild_id: int | None,
        created_by: int,
    ) -> PermRule:
        rule = PermRule(
            id=self._next_id,
            users=users,
            roles=roles,
            module=module,
            command=command,
            global_scope=global_scope,
            guild_id=guild_id if not global_scope else None,
            created_by=created_by,
        )
        self.rules.append(rule)
        self._next_id += 1
        self._save()
        return rule

    def remove(self, rid: int | None = None) -> list[PermRule]:
        removed = []
        remaining = []
        for r in self.rules:
            if rid is not None and r.id == rid:
                removed.append(r)
            else:
                remaining.append(r)
        if rid is not None and not removed:
            return []
        self.rules = remaining
        self._save()
        return removed

    def find(
        self,
        user: str | int | None = None,
        role: str | int | None = None,
        module: str | None = None,
        command: str | None = None,
        scope: str = "server",
        guild_id: int | None = None,
    ) -> list[PermRule]:
        results = []
        for r in self.rules:
            if scope == "global" and not r.global_scope:
                continue
            if scope == "server" and r.global_scope:
                continue
            if user is not None and not r.matches_user(user):
                continue
            if role is not None and not r.matches_role(role):
                continue
            if module is not None and r.module != module:
                continue
            if command is not None and r.command != command:
                continue
            if scope == "server" and guild_id is not None and r.guild_id != guild_id:
                continue
            results.append(r)
        return results

    def check(
        self,
        user_id: str | int,
        guild_id: int | None,
        module: str | None = None,
        command: str | None = None,
    ) -> bool:
        sid = str(user_id)
        role_ids = set()
        if guild_id is not None:
            role_ids = self._get_member_role_ids(user_id, guild_id)
        for r in self.rules:
            if sid not in r.users and not (role_ids and role_ids & set(r.roles)):
                continue
            if r.global_scope:
                pass
            elif (
                guild_id is not None
                and r.guild_id != guild_id
                or guild_id is None
                and not r.global_scope
            ):
                continue
            if r.module is not None and module == r.module:
                return True
            if r.command is not None and command == r.command:
                return True
        return False

    def grants_mod(self, user_id: str | int, guild_id: int | None) -> bool:
        """
        用户是否拥有「mod 授权」规则 (module 与 command 均为空)

        效果等同于配置文件 mods 名单: 授予所有 mod 级指令权限
        """
        sid = str(user_id)
        role_ids = set()
        if guild_id is not None:
            role_ids = self._get_member_role_ids(user_id, guild_id)
        for r in self.rules:
            if sid not in r.users and not (role_ids and role_ids & set(r.roles)):
                continue
            if r.module is not None or r.command is not None:
                continue
            if r.global_scope:
                return True
            if guild_id is not None and r.guild_id == guild_id:
                return True
        return False

    def all_rules_sorted(self) -> list[PermRule]:
        return sorted(self.rules, key=lambda r: r.id)

    def _get_member_role_ids(self, user_id: str | int, guild_id: int) -> set[str]:
        guild = getattr(u, "_bot_guild_lookup", None)
        if guild is None:
            return set()
        member = guild(guild_id, int(user_id) if str(user_id).isdigit() else user_id)
        if member is None:
            return set()
        return {
            str(role.id)
            for role in getattr(member, "roles", [])
            if not role.is_default()
        }

    def __len__(self) -> int:
        return len(self.rules)
