"""边界预演台账：预演记录、站点占用与检查点的状态流转都收在这里。

边界预演只读站点描述的离线链接图，不创建抓取任务；同一站点被重复预演时复用同一条
占用记录，不会重复占用。站点规则一旦更新，旧预演的判定依据失效并转为历史，
已投用的检查点保留原判定指纹与规则快照，继续可追溯。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.seed import SITE_EDGES
from app.services.boundary import (
    decision_fingerprint,
    evaluate_boundary,
    host_of,
    normalize_url,
    rules_snapshot,
)
from app.store import store

MODULE = "boundarypreview"
CHECKPOINT_MODULE = "crawlcheckpoint"
OCCUPANCY_MODULE = "siteoccupancy"
CONFIG_MODULE = "deepcrawl"
SITE_MODULE = "sitemap"

PREVIEW_ACTIVE = "待启用"
PREVIEW_ENABLED = "已启用"
PREVIEW_HISTORY = "历史"
PREVIEW_STATUSES = [PREVIEW_ACTIVE, PREVIEW_ENABLED, PREVIEW_HISTORY]

SORT_FIELDS = {
    "排序序号": lambda row: int(row.get("排序序号", 0)),
    "创建时间": lambda row: str(row.get("创建时间", "")),
    "实际可达深度": lambda row: int(row.get("实际可达深度") or -1),
    "最大深度": lambda row: int(row.get("最大深度") or 0),
    "允许路径数": lambda row: len(row.get("边界判定", {}).get("允许进入的路径", [])),
    "排除路径数": lambda row: len(row.get("边界判定", {}).get("被排除路径", [])),
    "超深页数": lambda row: int(row.get("边界判定", {}).get("深度边界判定", {}).get("超深页数", 0)),
    "站点编号": lambda row: str(row.get("站点编号", "")),
    "配置编号": lambda row: str(row.get("配置编号", "")),
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_code(rows: list[dict[str, Any]], field: str, prefix: str, width: int = 4) -> str:
    """按已有编号的最大序号生成下一个编号，删改记录后也不会撞号。"""
    numbers = []
    for row in rows:
        value = str(row.get(field, ""))
        if value.startswith(f"{prefix}-") and value[len(prefix) + 1:].isdigit():
            numbers.append(int(value[len(prefix) + 1:]))
    seq = max(numbers, default=0) + 1
    return f"{prefix}-{seq:0{width}d}"


def _site_version(site: dict[str, Any]) -> int:
    try:
        return int(site.get("规则版本", 1))
    except (TypeError, ValueError):
        return 1


class BoundaryPreviewService:
    # ------------------------------------------------------------------ 预演
    def create_preview(
        self,
        config_id: int,
        site_id: int,
        *,
        operator: str = "值班管理员",
        remark: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        config = store.find(CONFIG_MODULE, config_id)
        if config is None:
            return None, f"爬虫配置 {config_id} 不存在或已归档"
        site = store.find(SITE_MODULE, site_id)
        if site is None:
            return None, f"站点描述 {site_id} 不存在或已归档"

        edges = [dict(edge) for edge in SITE_EDGES.get(str(site.get("站点编号", "")), [])]
        boundary = evaluate_boundary(config, site, edges)
        site_identity = self._identify_site(config, site)
        eligibility = self._eligibility(config, site, boundary, site_identity)
        snapshot = rules_snapshot(config, site, edges)
        fingerprint = decision_fingerprint(snapshot)

        rows = store.rows(MODULE)
        occupancy = self._occupy_site(site, operator=operator)
        record: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "排序序号": max((int(row.get("排序序号", 0)) for row in rows), default=0) + 1,
            "预演编号": _next_code(rows, "预演编号", "PREV"),
            "status": PREVIEW_ACTIVE,
            "pending": True,
            "abnormal": eligibility["爬取资格"] != "可启用",
            "配置id": config_id,
            "配置编号": config.get("配置编号"),
            "站点id": site_id,
            "站点编号": site.get("站点编号"),
            "站点域名": site.get("站点域名"),
            "最大深度": boundary["最大深度"],
            "实际可达深度": boundary["实际可达深度"],
            "站点识别结果": site_identity,
            "爬取资格": eligibility["爬取资格"],
            "资格判定": eligibility,
            "边界判定": boundary,
            "路径排除规则": config.get("路径排除规则"),
            "路径排除来源": boundary["路径排除来源"],
            "规则快照": snapshot,
            "规则版本": {"站点": _site_version(site), "爬虫配置": int(config.get("规则版本", 1))},
            "判定指纹": fingerprint,
            "站点占用": {
                "占用编号": occupancy["占用编号"],
                "站点域名": occupancy["站点域名"],
                "预演次数": occupancy["预演次数"],
            },
            "检查点id": None,
            "检查点编号": None,
            "历史原因": None,
            "替代预演编号": None,
            "创建人": operator,
            "创建时间": _now(),
            "备注": remark or "",
        }
        rows.append(record)
        occupancy["最近预演id"] = record["id"]
        occupancy["最近预演编号"] = record["预演编号"]
        return record, "边界预演已完成，未产生任何抓取任务"

    def _identify_site(self, config: dict[str, Any], site: dict[str, Any]) -> dict[str, Any]:
        seed_url = normalize_url(str(config.get("种子URL", "")))
        seed_host = host_of(seed_url)
        site_host = host_of(str(site.get("站点域名", "")))
        if not seed_host:
            result, note = "无法识别", "种子URL缺少可解析的域名"
        elif not site_host:
            result, note = "无法识别", "站点描述缺少站点域名"
        elif seed_host == site_host:
            result, note = "一致", f"种子URL域名与站点描述一致：{seed_host}"
        else:
            result, note = "不一致", f"种子URL域名 {seed_host} 与站点域名 {site_host} 不一致"
        return {
            "结果": result,
            "种子URL": config.get("种子URL"),
            "种子域名": seed_host,
            "站点编号": site.get("站点编号"),
            "站点域名": site_host,
            "说明": note,
        }

    def _eligibility(
        self,
        config: dict[str, Any],
        site: dict[str, Any],
        boundary: dict[str, Any],
        site_identity: dict[str, Any],
    ) -> dict[str, Any]:
        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        if int(boundary["最大深度"]) <= 0:
            blockers.append({"原因": "最大深度未配置或不是正整数", "来源": "爬虫配置"})
        site_status = str(site.get("status", ""))
        if site_status == "已屏蔽":
            blockers.append({"原因": "站点已被标记屏蔽", "来源": "站点描述"})
        elif site_status != "已识别":
            blockers.append({
                "原因": f"站点尚未确认识别（当前状态：{site_status or '未知'}）",
                "来源": "站点描述",
            })
        if site_identity["结果"] != "一致":
            blockers.append({"原因": site_identity["说明"], "来源": "站点识别"})

        seed_entry = next(
            (item for item in boundary["允许进入的路径"] if item["深度"] == 0),
            None,
        )
        if seed_entry is None:
            seed_blocker = next(
                (item for item in boundary["被排除路径"] if item["深度"] == 0),
                None,
            )
            reason = seed_blocker["排除说明"] if seed_blocker else "种子URL无法进入"
            blockers.append({"原因": f"种子URL不可进入：{reason}", "来源": "边界判定"})
        if not boundary["允许进入的路径"]:
            blockers.append({"原因": "没有允许进入的路径，预演范围内无可爬页面", "来源": "边界判定"})

        for item in boundary["超深路径"]:
            warnings.append({
                "原因": f"{item['url']} 深度 {item['深度']} 超出最大深度 {boundary['最大深度']}，已由深度边界拦截",
                "来源": "深度边界",
            })
        for item in boundary["被排除路径"]:
            if item["排除来源"] == "同域约束" and item["来源类型"] == "sitemap跳转":
                warnings.append({
                    "原因": f"sitemap 指向站外地址 {item['url']}，按同域约束优先排除",
                    "来源": "同域约束",
                })
        robots_denied = [
            hit for hit in boundary["robots规则命中"] if hit["robots命中"].get("结果") == "禁止"
        ]
        for hit in robots_denied:
            warnings.append({
                "原因": f"{hit['路径']} 命中 robots 规则 {hit['robots命中'].get('规则')}",
                "来源": "robots规则",
            })

        return {
            "爬取资格": "不可启用" if blockers else "可启用",
            "阻断项": blockers,
            "提示项": warnings,
            "说明": (
                "；".join(item["原因"] for item in blockers)
                if blockers
                else "站点识别一致、种子可进入，可确认启用"
            ),
        }

    def _occupy_site(self, site: dict[str, Any], *, operator: str) -> dict[str, Any]:
        """同一站点只保留一条占用记录，重复预演只累加次数。"""
        rows = store.rows(OCCUPANCY_MODULE)
        domain = host_of(str(site.get("站点域名", "")))
        for row in rows:
            same_id = int(row.get("站点id", 0)) == int(site.get("id", 0))
            same_domain = bool(domain) and row.get("站点域名") == domain
            if same_id or same_domain:
                row["预演次数"] = int(row.get("预演次数", 0)) + 1
                row["最近预演时间"] = _now()
                row["最近操作人"] = operator
                return row
        occupancy = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "占用编号": _next_code(rows, "占用编号", "OCCP", width=3),
            "站点id": site.get("id"),
            "站点编号": site.get("站点编号"),
            "站点域名": domain,
            "占用状态": "预演占用",
            "预演次数": 1,
            "首次预演时间": _now(),
            "最近预演时间": _now(),
            "最近预演id": None,
            "最近预演编号": None,
            "最近操作人": operator,
        }
        rows.append(occupancy)
        return occupancy

    # ------------------------------------------------------------------ 确认
    def confirm_preview(self, preview_id: int, *, operator: str = "值班管理员") -> tuple[dict[str, Any] | None, str]:
        preview = store.find(MODULE, preview_id)
        if preview is None:
            return None, f"边界预演 {preview_id} 不存在或已归档"
        if preview.get("status") == PREVIEW_HISTORY:
            return None, "该预演已转为历史，请基于最新站点规则重新预演"
        if preview.get("status") == PREVIEW_ENABLED:
            return None, "该预演已确认启用，无需重复确认"
        if preview.get("爬取资格") != "可启用":
            return None, f"预演结论为不可启用：{preview.get('资格判定', {}).get('说明', '')}"

        config = store.find(CONFIG_MODULE, int(preview.get("配置id", 0)))
        site = store.find(SITE_MODULE, int(preview.get("站点id", 0)))
        if config is None or site is None:
            return None, "预演关联的爬虫配置或站点描述已不存在"
        # 确认前再次比对判定依据，避免预演之后规则被改过。
        edges = [dict(edge) for edge in SITE_EDGES.get(str(site.get("站点编号", "")), [])]
        snapshot = rules_snapshot(config, site, edges)
        if decision_fingerprint(snapshot) != preview.get("判定指纹"):
            return None, "站点或爬虫配置规则已更新，请重新预演后再确认"

        checkpoint = self._create_checkpoint(preview, config, site, operator=operator)
        config["status"] = "已启用"
        config["pending"] = False
        config["abnormal"] = False
        config["规则版本"] = int(config.get("规则版本", 1))
        preview["status"] = PREVIEW_ENABLED
        preview["pending"] = False
        preview["abnormal"] = False
        preview["检查点id"] = checkpoint["id"]
        preview["检查点编号"] = checkpoint["检查点编号"]
        preview["确认人"] = operator
        preview["确认时间"] = _now()

        occupancy = next(
            (row for row in store.rows(OCCUPANCY_MODULE) if row.get("站点域名") == host_of(str(site.get("站点域名", "")))),
            None,
        )
        if occupancy is not None:
            occupancy["占用状态"] = "使用中"
            occupancy["检查点编号"] = checkpoint["检查点编号"]
        return preview, f"预演已确认，爬虫配置 {config.get('配置编号')} 已启用并建立检查点 {checkpoint['检查点编号']}"

    def _create_checkpoint(
        self,
        preview: dict[str, Any],
        config: dict[str, Any],
        site: dict[str, Any],
        *,
        operator: str,
    ) -> dict[str, Any]:
        rows = store.rows(CHECKPOINT_MODULE)
        checkpoint = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "检查点编号": _next_code(rows, "检查点编号", "CP", width=3),
            "status": "使用中",
            "预演id": preview["id"],
            "预演编号": preview["预演编号"],
            "配置id": config.get("id"),
            "配置编号": config.get("配置编号"),
            "站点id": site.get("id"),
            "站点编号": site.get("站点编号"),
            "判定指纹": preview["判定指纹"],
            "规则快照": preview["规则快照"],
            "入队路径": [item["url"] for item in preview["边界判定"]["允许进入的路径"]],
            "实际可达深度": preview["实际可达深度"],
            "规则已更新": False,
            "可追溯说明": "检查点保留创建时的判定指纹与规则快照",
            "创建人": operator,
            "创建时间": _now(),
        }
        rows.append(checkpoint)
        return checkpoint

    # ------------------------------------------------------------------ 失效
    def site_rules_changed(self, site: dict[str, Any], *, reason: str = "站点规则更新") -> int:
        """站点规则更新后，该站点所有有效预演转为历史；检查点原样保留。"""
        changed = 0
        rows = store.rows(MODULE)
        for row in rows:
            if int(row.get("站点id", 0)) != int(site.get("id", 0)):
                continue
            if row.get("status") not in (PREVIEW_ACTIVE, PREVIEW_ENABLED):
                continue
            self._mark_history(row, reason=f"{reason}（{site.get('站点编号')}）")
            changed += 1
        return changed

    def config_rules_changed(self, config: dict[str, Any], *, reason: str = "爬虫配置更新") -> int:
        changed = 0
        for row in store.rows(MODULE):
            if int(row.get("配置id", 0)) != int(config.get("id", 0)):
                continue
            if row.get("status") not in (PREVIEW_ACTIVE, PREVIEW_ENABLED):
                continue
            self._mark_history(row, reason=f"{reason}（{config.get('配置编号')}）")
            changed += 1
        return changed

    def _mark_history(self, row: dict[str, Any], *, reason: str) -> None:
        row["status"] = PREVIEW_HISTORY
        row["pending"] = False
        row["历史原因"] = reason
        row["历史时间"] = _now()
        checkpoint_id = row.get("检查点id")
        if checkpoint_id:
            checkpoint = store.find(CHECKPOINT_MODULE, int(checkpoint_id))
            if checkpoint is not None:
                checkpoint["规则已更新"] = True
                checkpoint["规则更新说明"] = f"{reason}；检查点继续沿用 {row['判定指纹']} 的判定依据"
                config = store.find(CONFIG_MODULE, int(row.get("配置id", 0)))
                if config is not None and str(config.get("status")) == "已启用":
                    # 配置仍按检查点运行，但边界依据已变，标记异常提醒复核。
                    config["abnormal"] = True
                    config["边界复核"] = "站点或配置规则已更新，检查点仍按原判定指纹可追溯"
        row["检查点追溯"] = (
            {
                "检查点编号": row.get("检查点编号"),
                "判定指纹": row.get("判定指纹"),
                "说明": "预演已转历史，使用中的检查点仍按原判定指纹可追溯",
            }
            if checkpoint_id
            else None
        )

    # ------------------------------------------------------------------ 查询
    def list_previews(
        self,
        *,
        config_keyword: str | None = None,
        site_keyword: str | None = None,
        exclude_rule: str | None = None,
        status: str | None = None,
        eligibility: str | None = None,
        decision_fingerprint: str | None = None,
        sort_by: str = "排序序号",
        order: str = "asc",
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        rows = list(store.rows(MODULE))
        if config_keyword:
            rows = [row for row in rows if config_keyword in str(row.get("配置编号", ""))]
        if site_keyword:
            rows = [row for row in rows if site_keyword in str(row.get("站点编号", ""))]
        if exclude_rule:
            rows = [row for row in rows if self._matches_exclude_rule(row, exclude_rule)]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if eligibility:
            rows = [row for row in rows if row.get("爬取资格") == eligibility]
        if decision_fingerprint:
            rows = [row for row in rows if row.get("判定指纹") == decision_fingerprint]

        sort_key = sort_by if sort_by in SORT_FIELDS else "排序序号"
        reverse = str(order).lower() == "desc"
        # 排序主键之外固定用「排序序号 + id」兜底，保证同一深度边界判定在任何排序下都能找回。
        rows.sort(key=lambda row: (SORT_FIELDS[sort_key](row), int(row.get("排序序号", 0)), int(row.get("id", 0))), reverse=reverse)
        total = len(rows)
        start = max(page - 1, 0) * size
        meta = {
            "排序字段": sort_key,
            "排序方向": "desc" if reverse else "asc",
            "稳定兜底": "排序序号, id",
        }
        return rows[start:start + size], total, meta

    @staticmethod
    def _matches_exclude_rule(row: dict[str, Any], keyword: str) -> bool:
        if keyword in str(row.get("路径排除规则", "")):
            return True
        for item in row.get("边界判定", {}).get("被排除路径", []):
            if item.get("排除来源") == "路径排除规则" and keyword in str(item.get("排除规则", "")):
                return True
        return False

    def get_preview(self, preview_id: int) -> dict[str, Any] | None:
        preview = store.find(MODULE, preview_id)
        if preview is None:
            return None
        detail = dict(preview)
        checkpoint = None
        if preview.get("检查点id"):
            checkpoint = store.find(CHECKPOINT_MODULE, int(preview["检查点id"]))
        detail["检查点"] = dict(checkpoint) if checkpoint else None
        occupancy = next(
            (
                row
                for row in store.rows(OCCUPANCY_MODULE)
                if row.get("站点编号") == preview.get("站点编号")
                or row.get("站点域名") == host_of(str(preview.get("站点域名", "")))
            ),
            None,
        )
        detail["站点占用记录"] = dict(occupancy) if occupancy else None
        return detail

    def list_checkpoints(self, *, site_id: int | None = None) -> list[dict[str, Any]]:
        rows = list(store.rows(CHECKPOINT_MODULE))
        if site_id is not None:
            rows = [row for row in rows if int(row.get("站点id", 0)) == site_id]
        return rows

    def occupancy(self) -> list[dict[str, Any]]:
        return list(store.rows(OCCUPANCY_MODULE))
