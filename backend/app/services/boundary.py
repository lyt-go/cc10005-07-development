"""边界预演判定引擎：用站点描述里的链接图做离线广度遍历，不发起任何真实请求。

判定顺序（发生冲突时排在前面的规则优先）：
    同域约束 > 深度边界 > robots 规则 > 路径排除规则 > 路径包含规则

其中「同域约束」与「sitemap 跳转」冲突时，同域约束优先：sitemap 里指向其它域名的
地址一律记为排除，并保留冲突说明，避免站点地图把爬虫带出种子域。
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from typing import Any
from urllib.parse import urlsplit

RULE_PRIORITY = [
    "同域约束",
    "深度边界",
    "robots规则",
    "路径排除规则",
    "路径包含规则",
]

RULE_PRIORITY_NOTE = (
    "同域约束优先于 sitemap 跳转：站点描述里的 sitemap 地址若指向其它域名，"
    "按同域约束排除，并记录冲突说明。"
)

TRAVERSAL_NOTE = "基于站点描述链接图的离线广度遍历，逐层判定深度边界，不以一次静态访问代替"

TRUE_WORDS = {"开启", "是", "true", "1", "yes", "启用", "on"}


def host_of(url: str) -> str:
    """取域名（小写、不带端口）；站点描述里只写域名时也能解析。"""
    text = str(url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"//{text}"
    try:
        return (urlsplit(text).hostname or "").lower()
    except ValueError:
        return ""


def normalize_url(url: str) -> str:
    """统一 URL 写法，保证同一地址在链接图里只出现一次。"""
    text = str(url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"https://{text}"
    parts = urlsplit(text)
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    port = ""
    if parts.port and parts.port not in (80, 443):
        port = f":{parts.port}"
    path = parts.path or "/"
    query = f"?{parts.query}" if parts.query else ""
    return f"{scheme}://{host}{port}{path}{query}"


def path_of(url: str) -> str:
    try:
        parts = urlsplit(url)
        return parts.path or "/"
    except ValueError:
        return "/"


def parse_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in TRUE_WORDS


def split_rules(value: Any) -> list[str]:
    """把逗号、顿号、分号或换行分隔的规则拆成列表。"""
    text = str(value or "").replace("，", ",").replace("、", ",").replace("；", ";")
    parts: list[str] = []
    for chunk in re.split(r"[,;\n]", text):
        rule = chunk.strip()
        if rule and rule not in parts:
            parts.append(rule)
    return parts


def _glob_regex(pattern: str, *, end_anchor: bool = False) -> re.Pattern[str]:
    pieces: list[str] = []
    for char in pattern:
        pieces.append(".*" if char == "*" else re.escape(char))
    suffix = "$" if end_anchor else ""
    return re.compile("^" + "".join(pieces) + suffix)


def pattern_matches(pattern: str, path: str) -> bool:
    """路径规则匹配：带 * 或 $ 的按通配规则，其余按前缀（/ 开头）或包含匹配。"""
    rule = str(pattern or "").strip()
    if not rule:
        return False
    if "*" in rule or rule.endswith("$"):
        end_anchor = rule.endswith("$")
        body = rule[:-1] if end_anchor else rule
        return _glob_regex(body, end_anchor=end_anchor).match(path) is not None
    if rule.startswith("/"):
        return path.startswith(rule)
    return rule in path


def parse_robots(text: Any) -> dict[str, Any]:
    """解析站点描述里的 robots 规则，返回适用的规则分组。"""
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in str(text or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if current is None or current["规则"]:
                current = {"分组": value, "规则": []}
                groups.append(current)
            else:
                current["分组"] = f"{current['分组']},{value}"
        elif key in ("allow", "disallow") and current is not None:
            current["规则"].append({
                "效果": "允许" if key == "allow" else "禁止",
                "规则文本": f"{key.title()}: {value}",
                "模式": value,
            })
    if groups:
        for group in groups:
            if "*" in group["分组"]:
                return {"分组": group["分组"], "规则": group["规则"]}
        return {"分组": groups[0]["分组"], "规则": groups[0]["规则"]}
    return {"分组": "", "规则": []}


def _robots_pattern_matches(pattern: str, path: str) -> bool:
    """robots 规则按前缀匹配；带 $ 的锚定结尾，带 * 的通配。"""
    if not pattern:
        return False
    if "*" in pattern or pattern.endswith("$"):
        end_anchor = pattern.endswith("$")
        body = pattern[:-1] if end_anchor else pattern
        return _glob_regex(body, end_anchor=end_anchor).match(path) is not None
    return path.startswith(pattern)


def robots_decision(robots: dict[str, Any], path: str) -> dict[str, Any]:
    """按最长匹配优先、同长 Allow 优先的常见口径给出 robots 命中结果。"""
    best: dict[str, Any] | None = None
    best_weight = -1
    for rule in robots.get("规则", []):
        pattern = rule.get("模式", "")
        if not _robots_pattern_matches(pattern, path):
            continue
        weight = len(pattern.replace("*", "").replace("$", ""))
        if weight > best_weight or (weight == best_weight and rule["效果"] == "允许"):
            best = rule
            best_weight = weight
    if best is None:
        return {"命中": False, "结果": "允许", "规则": None, "分组": robots.get("分组", "")}
    return {
        "命中": True,
        "结果": best["效果"],
        "规则": best["规则文本"],
        "分组": robots.get("分组", ""),
    }


def _check(rule: str, result: str, note: str, hit: str | None = None) -> dict[str, Any]:
    return {"规则来源": rule, "结果": result, "说明": note, "命中规则": hit}


def _judge(
    *,
    url: str,
    depth: int,
    source_page: str | None,
    source_type: str,
    depth_path: list[str],
    seed_host: str,
    max_depth: int,
    same_domain: bool,
    robots: dict[str, Any],
    include_rules: list[str],
    exclude_rules: list[str],
) -> dict[str, Any]:
    path = path_of(url)
    host = host_of(url)
    checks: list[dict[str, Any]] = []
    exclusions: list[tuple[str, str, str]] = []

    # 1. 同域约束优先于 sitemap 跳转
    if same_domain and host and host != seed_host:
        conflict = "同域约束优先于 sitemap 跳转" if source_type == "sitemap跳转" else "域名超出种子域"
        detail = f"{conflict}：{host} != {seed_host}"
        checks.append(_check("同域约束", "命中", detail))
        exclusions.append(("同域约束", conflict, detail))
    else:
        checks.append(_check("同域约束", "通过", "未开启同域约束" if not same_domain else "域名与种子域一致"))

    # 2. 深度边界
    if depth > max_depth:
        checks.append(_check("深度边界", "命中", f"深度 {depth} > 最大深度 {max_depth}"))
        exclusions.append(("深度边界", f"最大深度 {max_depth}", f"深度 {depth} 超出最大深度 {max_depth}"))
    else:
        checks.append(_check("深度边界", "通过", f"深度 {depth} ≤ 最大深度 {max_depth}"))

    # 3. robots 规则
    decision = robots_decision(robots, path)
    if decision["结果"] == "禁止":
        checks.append(_check("robots规则", "命中", f"{decision['规则']} 禁止访问 {path}", decision["规则"]))
        exclusions.append(("robots规则", decision["规则"] or "", f"robots 规则禁止访问 {path}"))
    else:
        note = "未命中禁止规则，默认允许"
        if decision["命中"]:
            note = f"命中 {decision['规则']}"
        checks.append(_check("robots规则", "允许", note, decision["规则"]))
    decision = {**decision, "路径": path, "url": url, "深度": depth}

    # 4. 路径排除规则
    exclude_hit = next((rule for rule in exclude_rules if pattern_matches(rule, path)), None)
    if exclude_hit:
        checks.append(_check("路径排除规则", "命中", f"路径 {path} 命中排除规则 {exclude_hit}", exclude_hit))
        exclusions.append(("路径排除规则", exclude_hit, f"路径 {path} 命中排除规则 {exclude_hit}"))
    else:
        checks.append(_check("路径排除规则", "未命中", "没有命中路径排除规则"))

    # 5. 路径包含规则
    if include_rules:
        include_hit = next((rule for rule in include_rules if pattern_matches(rule, path)), None)
        if include_hit is None:
            checks.append(_check("路径包含规则", "命中", f"路径 {path} 不在包含白名单内"))
            exclusions.append(("路径包含规则", "/".join(include_rules), f"路径 {path} 不在路径包含规则内"))
        else:
            checks.append(_check("路径包含规则", "通过", f"路径命中包含规则 {include_hit}", include_hit))
    else:
        checks.append(_check("路径包含规则", "通过", "未配置路径包含规则，默认全部进入"))

    exclusions.sort(key=lambda item: RULE_PRIORITY.index(item[0]))
    primary = exclusions[0] if exclusions else None
    return {
        "url": url,
        "路径": path,
        "深度": depth,
        "深度路径": list(depth_path),
        "来源页": source_page,
        "来源类型": source_type,
        "站点内跳转": source_type == "页面链接",
        "判定": "排除" if primary else "允许",
        "排除来源": primary[0] if primary else None,
        "排除规则": primary[1] if primary else None,
        "排除说明": primary[2] if primary else None,
        "规则命中": checks,
        "robots命中": decision,
        "超深": depth > max_depth,
    }


def rules_snapshot(config: dict[str, Any], site: dict[str, Any], edges: list[dict[str, Any]]) -> dict[str, Any]:
    """冻结判定依据，供检查点与历史预演追溯。"""
    return {
        "配置规则": {
            "配置编号": config.get("配置编号"),
            "种子URL": config.get("种子URL"),
            "最大深度": config.get("最大深度"),
            "同域约束": config.get("同域约束"),
            "路径包含规则": config.get("路径包含规则"),
            "路径排除规则": config.get("路径排除规则"),
            "断点续爬": config.get("断点续爬"),
        },
        "站点规则": {
            "站点编号": site.get("站点编号"),
            "站点域名": site.get("站点域名"),
            "站点状态": site.get("status"),
            "robots规则": site.get("robots规则"),
            "sitemap地址": site.get("sitemap地址"),
            "页面模板路径": site.get("页面模板路径"),
            "翻页参数名": site.get("翻页参数名"),
        },
        "链接图": sorted(
            (
                {
                    "来源页": normalize_url(str(edge.get("来源页", ""))),
                    "目标URL": normalize_url(str(edge.get("目标URL", ""))),
                    "来源类型": edge.get("来源类型", "页面链接"),
                }
                for edge in edges
            ),
            key=lambda item: (item["来源页"], item["目标URL"]),
        ),
    }


def decision_fingerprint(snapshot: dict[str, Any]) -> str:
    """同一份配置 + 站点规则 + 链接图得到同一个边界判定指纹。"""
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "BND-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12].upper()


def evaluate_boundary(
    config: dict[str, Any],
    site: dict[str, Any],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """离线判定：从种子出发逐层遍历，只有允许进入的页面才会展开下一层。"""
    seed_url = normalize_url(str(config.get("种子URL", "")))
    seed_host = host_of(seed_url)
    try:
        max_depth = max(int(str(config.get("最大深度") or "0").strip() or 0), 0)
    except ValueError:
        max_depth = 0
    same_domain = parse_bool(config.get("同域约束"))
    include_rules = split_rules(config.get("路径包含规则"))
    exclude_rules = split_rules(config.get("路径排除规则"))
    robots = parse_robots(site.get("robots规则"))
    sitemap_url = normalize_url(str(site.get("sitemap地址", "")))

    adjacency: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        source = normalize_url(str(edge.get("来源页", "")))
        adjacency.setdefault(source, []).append(edge)

    nodes: dict[str, dict[str, Any]] = {}
    queue: deque[tuple[str, int]] = deque()
    edge_count = 0

    def visit(
        url: str,
        depth: int,
        source_page: str | None,
        source_type: str,
        depth_path: list[str],
    ) -> dict[str, Any]:
        entry = _judge(
            url=url,
            depth=depth,
            source_page=source_page,
            source_type=source_type,
            depth_path=depth_path,
            seed_host=seed_host,
            max_depth=max_depth,
            same_domain=same_domain,
            robots=robots,
            include_rules=include_rules,
            exclude_rules=exclude_rules,
        )
        nodes[url] = entry
        return entry

    if seed_url:
        seed_entry = visit(seed_url, 0, None, "种子URL", [seed_url])
        if seed_entry["判定"] == "允许":
            queue.append((seed_url, 0))

    # sitemap 地址声明的页面按第 1 层注入，来源页记为 sitemap 地址。
    for edge in edges:
        if str(edge.get("来源类型", "页面链接")) != "sitemap":
            continue
        target = normalize_url(str(edge.get("目标URL", "")))
        if not target or target in nodes:
            continue
        edge_count += 1
        source_page = sitemap_url or normalize_url(str(edge.get("来源页", "")))
        entry = visit(target, 1, source_page, "sitemap跳转", [source_page, target])
        if entry["判定"] == "允许" and 1 <= max_depth:
            queue.append((target, 1))

    while queue:
        current, depth = queue.popleft()
        if depth > max_depth:
            continue
        for edge in adjacency.get(current, []):
            target = normalize_url(str(edge.get("目标URL", "")))
            if not target or target in nodes:
                continue
            edge_count += 1
            next_depth = depth + 1
            entry = visit(target, next_depth, current, "页面链接", [*nodes[current]["深度路径"], target])
            if entry["判定"] == "允许" and next_depth <= max_depth:
                queue.append((target, next_depth))

    ordered = list(nodes.values())
    allowed = [entry for entry in ordered if entry["判定"] == "允许"]
    excluded = [entry for entry in ordered if entry["判定"] == "排除"]
    deep = [entry for entry in ordered if entry["超深"]]
    for entry in deep:
        entry["超深说明"] = (
            f"由站点内页面 {entry['来源页']} 的跳转发现，深度 {entry['深度']} 超出最大深度 {max_depth}"
            if entry["来源类型"] == "页面链接"
            else f"由 sitemap 注入，深度 {entry['深度']} 超出最大深度 {max_depth}"
        )

    sources: dict[str, int] = {}
    for entry in excluded:
        source = str(entry["排除来源"])
        sources[source] = sources.get(source, 0) + 1

    robots_hits = [
        entry for entry in ordered if entry["robots命中"].get("命中")
    ]
    actual_depth = max((entry["深度"] for entry in allowed), default=None)
    discovered_depth = max((entry["深度"] for entry in ordered), default=None)

    return {
        "判定方式": TRAVERSAL_NOTE,
        "规则优先级": RULE_PRIORITY,
        "规则优先级说明": RULE_PRIORITY_NOTE,
        "最大深度": max_depth,
        "实际可达深度": actual_depth,
        "允许进入的路径": allowed,
        "被排除路径": excluded,
        "超深路径": deep,
        "路径排除来源": sources,
        "robots规则命中": robots_hits,
        "深度边界判定": {
            "最大深度": max_depth,
            "实际可达深度": actual_depth,
            "最深发现深度": discovered_depth,
            "结论": "存在超深页" if deep else "边界内",
            "超深页数": len(deep),
            "超深页": [
                {
                    "url": entry["url"],
                    "深度": entry["深度"],
                    "来源页": entry["来源页"],
                    "来源类型": entry["来源类型"],
                    "深度路径": entry["深度路径"],
                    "说明": entry["超深说明"],
                }
                for entry in deep
            ],
            "说明": (
                f"实际可达深度 {actual_depth}，最大深度 {max_depth}；"
                f"另有 {len(deep)} 个页面在边界外"
                if deep
                else f"实际可达深度 {actual_depth}，未越过最大深度 {max_depth}"
            ),
        },
        "遍历统计": {
            "判定节点数": len(ordered),
            "判定边数": edge_count,
            "遍历深度上限": max_depth + 1,
        },
    }
