"""深度爬取业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.boundary_preview import BoundaryPreviewService
from app.store import store

MODULE = "deepcrawl"
REQUIRED_FIELDS = ["配置编号", "种子URL", "最大深度"]
RULE_FIELDS = ["种子URL", "最大深度", "同域约束", "路径包含规则", "路径排除规则", "断点续爬"]
STATUS_ORDER = ["草稿", "策略评审中", "已启用", "已暂停"]
ACTION_RULES = {"提交策略评审": "策略评审中", "启用爬取": "已启用", "暂停爬取": "已暂停"}
NEGATIVE_ACTIONS = []


class DeepcrawlService:
    def __init__(self) -> None:
        self.previews = BoundaryPreviewService()

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("配置编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["规则版本"] = 1
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def update_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str, int]:
        """修改爬虫配置；边界判定依据变化时把旧预演转历史。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"爬虫配置 {entry_id} 不存在或已归档", 0
        changed_rules = [
            field
            for field in RULE_FIELDS
            if field in values and str(values.get(field) or "") != str(entry.get(field) or "")
        ]
        for field in RULE_FIELDS + ["并发线程数"]:
            if field in values:
                entry[field] = values.get(field)
        if not changed_rules:
            return entry, "爬虫配置已更新，边界判定依据未变化", 0

        entry["规则版本"] = int(entry.get("规则版本", 1)) + 1
        entry["最近规则变更"] = "、".join(changed_rules)
        invalidated = self.previews.config_rules_changed(
            entry, reason=f"爬虫配置更新：{'、'.join(changed_rules)}"
        )
        if invalidated:
            message = f"爬虫配置已更新，{invalidated} 条旧预演转为历史"
        else:
            message = "爬虫配置已更新，边界判定依据已变化，无有效旧预演"
        return entry, message, invalidated

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"爬虫配置 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于深度爬取可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        if action == "启用爬取":
            enabled, message = self._enable_guard(entry)
            if not enabled:
                return None, message
            entry["status"] = target
            entry["pending"] = False
            entry["abnormal"] = False
            return entry, message
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"爬虫配置已{action}"

    def _enable_guard(self, entry: dict[str, Any]) -> tuple[bool, str]:
        """启用前必须有一次与当前规则一致、结论为可启用的边界预演。"""
        previews = [
            row
            for row in store.rows("boundarypreview")
            if int(row.get("配置id", 0)) == int(entry.get("id", 0))
        ]
        if not previews:
            return False, "启用前需先完成边界预演：请选择爬虫配置与站点描述执行预演"
        current = max(previews, key=lambda row: int(row.get("排序序号", 0)))
        if current.get("status") == "历史":
            return False, "最近一次边界预演已转为历史，请基于更新后的规则重新预演后再启用"
        if current.get("status") != "已启用":
            return False, f"边界预演 {current.get('预演编号')} 尚未确认，请先在预演面板确认启用"
        if current.get("爬取资格") != "可启用":
            return False, f"边界预演结论为不可启用：{current.get('资格判定', {}).get('说明', '')}"
        return True, f"爬虫配置已启用爬取（依据边界预演 {current.get('预演编号')}）"
