"""边界预演的判定与台账测试：只跑离线引擎与服务层，不访问网络。"""
from __future__ import annotations

import unittest

from app.services.boundary import evaluate_boundary, parse_robots, pattern_matches, normalize_url
from app.services.boundary_preview import BoundaryPreviewService
from app.store import store


def reset_store() -> None:
    store.reset()


class BoundaryEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.config = store.find("deepcrawl", 2)
        self.site = store.find("sitemap", 2)
        self.edges = [
            {"来源页": "https://news.example.org/sitemap.xml",
             "目标URL": "https://news.example.org/news/index.html", "来源类型": "sitemap"},
            {"来源页": "https://news.example.org/news/index.html",
             "目标URL": "https://news.example.org/news/tag/hot.html", "来源类型": "页面链接"},
            {"来源页": "https://news.example.org/news/index.html",
             "目标URL": "https://news.example.org/news/tech/index.html", "来源类型": "页面链接"},
            {"来源页": "https://news.example.org/news/index.html",
             "目标URL": "https://news.example.org/news/admin/console.html", "来源类型": "页面链接"},
            {"来源页": "https://news.example.org/sitemap.xml",
             "目标URL": "https://partner.example.com/news/feed.html", "来源类型": "sitemap"},
            {"来源页": "https://news.example.org/news/tech/index.html",
             "目标URL": "https://news.example.org/news/tech/2026/story-01.html", "来源类型": "页面链接"},
            {"来源页": "https://news.example.org/news/tech/2026/story-01.html",
             "目标URL": "https://news.example.org/news/archive/2026.html", "来源类型": "页面链接"},
        ]

    def test_normalize_url(self):
        self.assertEqual(
            normalize_url("HTTPS://News.Example.org/news/index.html"),
            "https://news.example.org/news/index.html",
        )

    def test_pattern_prefix_and_glob(self):
        self.assertTrue(pattern_matches("/news/tag/", "/news/tag/hot.html"))
        self.assertFalse(pattern_matches("/news/tag/", "/news/tech/index.html"))
        self.assertTrue(pattern_matches("/news/*.html$", "/news/archive/2026.html"))

    def test_robots_longest_match_and_priority(self):
        robots = parse_robots("User-agent: *\nAllow: /news/\nDisallow: /news/admin/")
        self.assertTrue(robots["规则"])

    def test_deep_page_lists_source_page(self):
        result = evaluate_boundary(self.config, self.site, self.edges)
        deep = result["超深路径"]
        self.assertEqual(len(deep), 1)
        self.assertEqual(deep[0]["url"], "https://news.example.org/news/archive/2026.html")
        self.assertEqual(deep[0]["来源页"], "https://news.example.org/news/tech/2026/story-01.html")
        self.assertEqual(deep[0]["深度"], 3)
        self.assertIn(self.config["种子URL"], deep[0]["深度路径"])

    def test_excluded_path_records_source(self):
        result = evaluate_boundary(self.config, self.site, self.edges)
        excluded = {item["url"]: item for item in result["被排除路径"]}
        tag = excluded["https://news.example.org/news/tag/hot.html"]
        self.assertEqual(tag["排除来源"], "路径排除规则")
        self.assertEqual(tag["排除规则"], "/news/tag/")

    def test_robots_hit_and_same_domain_conflict(self):
        result = evaluate_boundary(self.config, self.site, self.edges)
        hits = {item["路径"]: item for item in result["robots规则命中"]}
        admin = hits.get("/news/admin/console.html")
        self.assertIsNotNone(admin, "robots 禁止的路径必须出现在命中清单里")
        self.assertEqual(admin["robots命中"]["结果"], "禁止")
        conflict = [
            item for item in result["被排除路径"]
            if item["排除来源"] == "同域约束" and item["来源类型"] == "sitemap跳转"
        ]
        self.assertTrue(conflict)
        self.assertIn("同域约束优先于 sitemap 跳转", conflict[0]["排除说明"])

    def test_actual_depth_is_within_boundary(self):
        result = evaluate_boundary(self.config, self.site, self.edges)
        self.assertEqual(result["实际可达深度"], 2)
        self.assertEqual(result["最大深度"], 2)


class PreviewLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.service = BoundaryPreviewService()

    def test_preview_does_not_create_crawl_task(self):
        before = len(store.rows("crawlcheckpoint"))
        record, message = self.service.create_preview(2, 2)
        self.assertIsNotNone(record)
        self.assertIn("未产生任何抓取任务", message)
        self.assertEqual(len(store.rows("crawlcheckpoint")), before)
        self.assertEqual(record["检查点id"], None)
        self.assertEqual(store.find("deepcrawl", 2)["status"], "策略评审中")

    def test_repeated_preview_reuses_site_occupancy(self):
        first, _ = self.service.create_preview(2, 2)
        second, _ = self.service.create_preview(2, 2)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(store.rows("siteoccupancy")), 1)
        occupancy = store.rows("siteoccupancy")[0]
        self.assertEqual(occupancy["预演次数"], 2)
        self.assertEqual(first["站点占用"]["占用编号"], second["站点占用"]["占用编号"])

    def test_confirm_creates_checkpoint_and_enables_config(self):
        record, _ = self.service.create_preview(2, 2)
        preview, message = self.service.confirm_preview(record["id"])
        self.assertIsNotNone(preview)
        self.assertEqual(preview["status"], "已启用")
        checkpoint = store.find("crawlcheckpoint", preview["检查点id"])
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint["判定指纹"], record["判定指纹"])
        self.assertEqual(checkpoint["规则快照"]["配置规则"]["最大深度"], "2")
        self.assertEqual(store.find("deepcrawl", 2)["status"], "已启用")

    def test_site_rule_update_turns_old_preview_history_and_keeps_checkpoint(self):
        record, _ = self.service.create_preview(2, 2)
        self.service.confirm_preview(record["id"])
        site = store.find("sitemap", 2)
        store.rows("sitemap")  # 保持同一对象
        changed = self.service.site_rules_changed(site, reason="站点规则更新：robots规则")
        self.assertEqual(changed, 1)
        self.assertEqual(record["status"], "历史")
        self.assertIn("robots规则", record["历史原因"])
        checkpoint = store.find("crawlcheckpoint", record["检查点id"])
        self.assertEqual(checkpoint["判定指纹"], record["判定指纹"])
        self.assertTrue(checkpoint["规则已更新"])

    def test_confirm_after_rule_change_is_rejected(self):
        record, _ = self.service.create_preview(2, 2)
        store.find("sitemap", 2)["robots规则"] = "User-agent: *\nDisallow: /"
        preview, message = self.service.confirm_preview(record["id"])
        self.assertIsNone(preview)
        self.assertIn("规则已更新", message)

    def test_ineligible_site_cannot_confirm(self):
        store.find("sitemap", 2)["status"] = "已屏蔽"
        record, _ = self.service.create_preview(2, 2)
        self.assertEqual(record["爬取资格"], "不可启用")
        preview, message = self.service.confirm_preview(record["id"])
        self.assertIsNone(preview)
        self.assertIn("不可启用", message)


if __name__ == "__main__":
    unittest.main()


class PreviewLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        self.service = BoundaryPreviewService()
        self.a, _ = self.service.create_preview(2, 2)
        self.b, _ = self.service.create_preview(2, 2)
        self.c, _ = self.service.create_preview(1, 1)

    def test_filter_by_config_site_and_exclude_rule(self):
        rows, total, _ = self.service.list_previews(
            config_keyword="DEEP-0002", site_keyword="SITE-0002", exclude_rule="/news/tag/"
        )
        self.assertEqual(total, 2)
        self.assertEqual({row["预演编号"] for row in rows}, {self.a["预演编号"], self.b["预演编号"]})
        rows, total, _ = self.service.list_previews(exclude_rule="不存在的规则")
        self.assertEqual(total, 0)

    def test_sort_order_is_stable_and_reversible(self):
        asc, _, meta = self.service.list_previews(sort_by="实际可达深度", order="asc")
        desc, _, _ = self.service.list_previews(sort_by="实际可达深度", order="desc")
        self.assertEqual(meta["稳定兜底"], "排序序号, id")
        self.assertEqual([row["预演编号"] for row in asc], list(reversed([row["预演编号"] for row in desc])))
        # 同一深度边界判定可用判定指纹原样找回
        same, total, _ = self.service.list_previews(decision_fingerprint=self.a["判定指纹"])
        self.assertEqual(total, 2)
        self.assertTrue(all(row["判定指纹"] == self.a["判定指纹"] for row in same))

    def test_invalid_sort_field_falls_back_to_sequence(self):
        rows, total, meta = self.service.list_previews(sort_by="不存在的字段")
        self.assertEqual(meta["排序字段"], "排序序号")
        self.assertEqual([row["排序序号"] for row in rows], sorted(row["排序序号"] for row in rows))

    def test_store_reset_clears_ledgers(self):
        reset_store()
        self.assertEqual(store.rows("boundarypreview"), [])
        self.assertEqual(store.rows("siteoccupancy"), [])
        self.assertEqual(store.rows("crawlcheckpoint"), [])


class EnableGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        reset_store()
        from app.services.deepcrawl import DeepcrawlService
        self.deep = DeepcrawlService()
        self.boundary = BoundaryPreviewService()

    def test_enable_requires_preview(self):
        entry, message = self.deep.run_action(2, "启用爬取")
        self.assertIsNone(entry)
        self.assertIn("边界预演", message)

    def test_enable_after_confirm(self):
        preview, _ = self.boundary.create_preview(2, 2)
        self.boundary.confirm_preview(preview["id"])
        entry, message = self.deep.run_action(2, "启用爬取")
        self.assertIsNotNone(entry, message)
        self.assertEqual(entry["status"], "已启用")

    def test_config_rule_change_invalidates_preview(self):
        preview, _ = self.boundary.create_preview(2, 2)
        entry, message, invalidated = self.deep.update_entry(2, {"最大深度": "1"})
        self.assertEqual(invalidated, 1)
        self.assertEqual(preview["status"], "历史")
        preview2, message2 = self.boundary.confirm_preview(preview["id"])
        self.assertIsNone(preview2)
        self.assertIn("历史", message2)

    def test_site_rule_change_via_service_api(self):
        from app.services.sitemap import SitemapService
        site_service = SitemapService()
        preview, _ = self.boundary.create_preview(2, 2)
        entry, message, invalidated = site_service.update_entry(2, {"robots规则": "User-agent: *\nDisallow: /"})
        self.assertEqual(invalidated, 1)
        self.assertIn("旧预演转为历史", message)
        self.assertEqual(entry["规则版本"], 2)
