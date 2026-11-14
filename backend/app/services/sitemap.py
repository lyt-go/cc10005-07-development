"""站点地图业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services.boundary_preview import BoundaryPreviewService
from app.store import store

MODULE = "sitemap"
REQUIRED_FIELDS = ["站点编号", "站点域名", "robots规则"]
STATUS_ORDER = ["草稿", "探测中", "已识别", "已屏蔽"]
ACTION_RULES = {"发起探测": "探测中", "确认识别": "已识别", "标记屏蔽": "已屏蔽"}
NEGATIVE_ACTIONS = []

# 会改变边界判定依据的字段；这些字段一旦变化，旧预演立即转历史。
RULE_FIELDS = ["站点域名", "robots规则", "sitemap地址", "页面模板路径", "翻页参数名"]
EDITABLE_FIELDS = RULE_FIELDS + ["站点字符编码", "最近探测时间"]


class SitemapService:
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
            rows = [row for row in rows if keyword in str(row.get("站点编号", ""))]
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
        """修改站点描述；判定依据变化时把该站点的旧预演转历史。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"站点描述 {entry_id} 不存在或已归档", 0
        changed_rules = [
            field
            for field in RULE_FIELDS
            if field in values and str(values.get(field) or "") != str(entry.get(field) or "")
        ]
        for field in EDITABLE_FIELDS:
            if field in values:
                entry[field] = values.get(field)
        if not changed_rules:
            return entry, "站点描述已更新，边界判定依据未变化", 0

        entry["规则版本"] = int(entry.get("规则版本", 1)) + 1
        entry["最近规则变更"] = "、".join(changed_rules)
        invalidated = self.previews.site_rules_changed(
            entry, reason=f"站点规则更新：{'、'.join(changed_rules)}"
        )
        message = f"站点描述已更新，{invalidated} 条旧预演转为历史"
        if invalidated == 0:
            message = "站点描述已更新，边界判定依据已变化，无有效旧预演"
        return entry, message, invalidated

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"站点描述 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于站点地图可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"站点描述已{action}"
