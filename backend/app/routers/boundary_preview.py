"""边界预演接口：选择爬虫配置与站点描述，先预演边界再确认启用。

预演只读站点描述的离线链接图，不创建抓取任务；确认后才建立检查点并启用爬虫配置。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, SortedPageResult
from app.services.boundary_preview import BoundaryPreviewService, PREVIEW_STATUSES, SORT_FIELDS
from app.store import store

router = APIRouter(prefix="/api/boundary-preview", tags=["边界预演"])

service = BoundaryPreviewService()


@router.get("/options")
def preview_options() -> dict[str, Any]:
    """可选的爬虫配置与站点描述，以及当前站点占用情况。"""
    configs = [
        {
            "id": row.get("id"),
            "配置编号": row.get("配置编号"),
            "种子URL": row.get("种子URL"),
            "最大深度": row.get("最大深度"),
            "同域约束": row.get("同域约束"),
            "路径包含规则": row.get("路径包含规则"),
            "路径排除规则": row.get("路径排除规则"),
            "status": row.get("status"),
        }
        for row in store.rows("deepcrawl")
    ]
    sites = [
        {
            "id": row.get("id"),
            "站点编号": row.get("站点编号"),
            "站点域名": row.get("站点域名"),
            "robots规则": row.get("robots规则"),
            "sitemap地址": row.get("sitemap地址"),
            "页面模板路径": row.get("页面模板路径"),
            "status": row.get("status"),
            "规则版本": row.get("规则版本", 1),
            "有一个有效预演": any(
                int(item.get("站点id", 0)) == int(row.get("id", 0))
                and item.get("status") in ("待启用", "已启用")
                for item in store.rows("boundarypreview")
            ),
        }
        for row in store.rows("sitemap")
    ]
    return {
        "configs": configs,
        "sites": sites,
        "occupancy": service.occupancy(),
        "rule_priority": ["同域约束", "深度边界", "robots规则", "路径排除规则", "路径包含规则"],
        "statuses": PREVIEW_STATUSES,
        "sort_fields": list(SORT_FIELDS),
    }


@router.post("/previews", response_model=ActionResult)
def create_preview(payload: EntryPayload) -> ActionResult:
    """执行一次边界预演：不落抓取任务，重复预演复用同一站点占用。"""
    values = payload.values
    try:
        config_id = int(values.get("config_id"))
        site_id = int(values.get("site_id"))
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="请选择爬虫配置与站点描述后再预演")
    operator = str(values.get("operator") or "值班管理员")
    record, message = service.create_preview(
        config_id, site_id, operator=operator, remark=payload.remark
    )
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.get("/previews", response_model=SortedPageResult)
def list_previews(
    config_keyword: str | None = Query(default=None, description="按爬虫配置编号筛选"),
    site_keyword: str | None = Query(default=None, description="按站点编号筛选"),
    exclude_rule: str | None = Query(default=None, description="按路径排除规则筛选"),
    status: str | None = Query(default=None, description="待启用、已启用、历史"),
    eligibility: str | None = Query(default=None, description="可启用、不可启用"),
    fingerprint: str | None = Query(default=None, description="按深度边界判定指纹找回同一判定"),
    sort_by: str = Query(default="排序序号", description="排序字段"),
    order: str = Query(default="asc", description="asc 或 desc"),
    page: int = 1,
    size: int = 20,
) -> SortedPageResult:
    """按爬虫配置、站点描述、路径排除规则筛选预演记录，排序后仍以排列序号兜底。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if sort_by not in SORT_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"排序字段「{sort_by}」不支持，可选：{'、'.join(SORT_FIELDS)}",
        )
    if str(order).lower() not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="排序方向只能是 asc 或 desc")
    items, total, meta = service.list_previews(
        config_keyword=config_keyword,
        site_keyword=site_keyword,
        exclude_rule=exclude_rule,
        status=status,
        eligibility=eligibility,
        decision_fingerprint=fingerprint,
        sort_by=sort_by,
        order=order,
        page=page,
        size=size,
    )
    return SortedPageResult(items=items, total=total, page=page, size=size, sort=meta)


@router.get("/previews/{preview_id}")
def get_preview(preview_id: int) -> dict[str, Any]:
    """预演明细：边界判定、站点识别、爬取资格、检查点与站点占用一并向出。"""
    preview = service.get_preview(preview_id)
    if preview is None:
        raise HTTPException(status_code=404, detail=f"边界预演 {preview_id} 不存在或已归档")
    return preview


@router.post("/previews/{preview_id}/confirm", response_model=ActionResult)
def confirm_preview(preview_id: int, payload: EntryPayload) -> ActionResult:
    """确认预演：建立检查点并启用爬虫配置；规则已变化时拒绝确认。"""
    operator = str(payload.values.get("operator") or "值班管理员")
    preview, message = service.confirm_preview(preview_id, operator=operator)
    if preview is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=preview)


@router.get("/checkpoints")
def list_checkpoints(site_id: int | None = Query(default=None, description="按站点 id 过滤")) -> dict[str, Any]:
    """检查点台账：预演转历史后仍保留原判定指纹与规则快照。"""
    rows = service.list_checkpoints(site_id=site_id)
    return {"items": rows, "total": len(rows)}


@router.get("/occupancy")
def list_occupancy() -> dict[str, Any]:
    """站点占用台账：同一站点只有一条占用记录。"""
    rows = service.occupancy()
    return {"items": rows, "total": len(rows)}


@router.get("/export")
def export_previews() -> dict[str, Any]:
    """导出预演清单：返回全部预演记录与判定依据。"""
    items, total, _meta = service.list_previews(page=1, size=10000)
    return {"module": "boundarypreview", "total": total, "items": items}
