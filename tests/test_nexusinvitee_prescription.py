import importlib
import sys
import types
import unittest


class DummyLogger:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def install_app_stubs():
    apscheduler = types.ModuleType("apscheduler")
    apscheduler.__path__ = []
    sys.modules.setdefault("apscheduler", apscheduler)

    scheduler_pkg = types.ModuleType("apscheduler.schedulers")
    scheduler_pkg.__path__ = []
    sys.modules.setdefault("apscheduler.schedulers", scheduler_pkg)

    scheduler_background = types.ModuleType("apscheduler.schedulers.background")
    scheduler_background.BackgroundScheduler = type("BackgroundScheduler", (), {})
    sys.modules.setdefault("apscheduler.schedulers.background", scheduler_background)

    trigger_pkg = types.ModuleType("apscheduler.triggers")
    trigger_pkg.__path__ = []
    sys.modules.setdefault("apscheduler.triggers", trigger_pkg)

    trigger_cron = types.ModuleType("apscheduler.triggers.cron")
    trigger_cron.CronTrigger = type("CronTrigger", (), {})
    sys.modules.setdefault("apscheduler.triggers.cron", trigger_cron)

    app = types.ModuleType("app")
    app.__path__ = []
    sys.modules.setdefault("app", app)

    core = types.ModuleType("app.core")
    core.__path__ = []
    sys.modules.setdefault("app.core", core)

    config = types.ModuleType("app.core.config")
    config.settings = types.SimpleNamespace(API_TOKEN="test-token")
    sys.modules.setdefault("app.core.config", config)

    event = types.ModuleType("app.core.event")
    event.eventmanager = object()
    sys.modules.setdefault("app.core.event", event)

    plugins = types.ModuleType("app.plugins")
    plugins._PluginBase = type("_PluginBase", (), {})
    sys.modules.setdefault("app.plugins", plugins)

    log = types.ModuleType("app.log")
    log.logger = DummyLogger()
    sys.modules.setdefault("app.log", log)

    schemas = types.ModuleType("app.schemas")
    schemas.Response = type("Response", (), {})
    sys.modules.setdefault("app.schemas", schemas)

    schema_types = types.ModuleType("app.schemas.types")
    schema_types.NotificationType = type("NotificationType", (), {})
    schema_types.EventType = type("EventType", (), {})
    sys.modules.setdefault("app.schemas.types", schema_types)

    db = types.ModuleType("app.db")
    db.__path__ = []
    sys.modules.setdefault("app.db", db)

    site_oper = types.ModuleType("app.db.site_oper")
    site_oper.SiteOper = type("SiteOper", (), {})
    sys.modules.setdefault("app.db.site_oper", site_oper)

    helper = types.ModuleType("app.helper")
    helper.__path__ = []
    sys.modules.setdefault("app.helper", helper)

    sites = types.ModuleType("app.helper.sites")
    sites.SitesHelper = type("SitesHelper", (), {})
    sys.modules.setdefault("app.helper.sites", sites)


class PrescriptionExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_app_stubs()
        cls.module = importlib.import_module("plugins.nexusinvitee")
        cls.Prescription = cls.module.Prescription

    def test_buyable_sites_are_exported_even_when_currently_cannot_invite(self):
        prescription = self.Prescription()
        prescription.setP("有剩余站", 1)
        prescription.setT("有剩余站", 2)
        prescription.setCanInvite("有剩余站", True)

        prescription.setCBP("仅可购买站", 2)
        prescription.setCBT("仅可购买站", 3)
        prescription.setCanInvite("仅可购买站", False)

        exported = prescription._export()
        by_site = {item["site"]: item for item in exported["details"]}

        self.assertEqual(by_site["有剩余站"]["remain"], 3)
        self.assertEqual(by_site["有剩余站"]["can_buy"], 0)
        self.assertEqual(by_site["仅可购买站"]["remain"], 0)
        self.assertEqual(by_site["仅可购买站"]["can_buy"], 5)
        self.assertEqual(exported["total"]["remain"], 3)
        self.assertEqual(exported["total"]["can_buy"], 5)

    def test_failed_sites_are_exported_after_normal_sites(self):
        prescription = self.Prescription()
        prescription.setP("正常站", 1)
        prescription.setCanInvite("正常站", True)
        prescription.setFailed("失败站", "获取站点邀请数据失败: timeout")

        exported = prescription._export()

        self.assertEqual([item["site"] for item in exported["details"]], ["正常站", "失败站"])
        self.assertEqual(exported["details"][-1]["error"], "获取站点邀请数据失败: timeout")
        self.assertEqual(exported["total"]["failed"], 1)
        self.assertEqual(
            prescription.getExportText().splitlines(),
            [
                "站点[正常站]: 剩余[1]个. 可购买[0]个",
                "站点[失败站]: 获取失败[获取站点邀请数据失败: timeout]",
            ],
        )


class NexusPhpInviteStatusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_app_stubs()
        module = importlib.import_module("plugins.nexusinvitee.sites.nexusphp")
        cls.NexusPhpHandler = module.NexusPhpHandler

    def test_no_remaining_invites_keeps_invite_permission(self):
        html = """
        <html>
          <body>
            <table>
              <tr><td>没有剩余邀请名额</td></tr>
            </table>
          </body>
        </html>
        """

        result = self.NexusPhpHandler()._parse_nexusphp_invite_page("测试站", html)

        self.assertTrue(result["invite_status"]["can_invite"])
        self.assertIn("数量不足", result["invite_status"]["reason"])


if __name__ == "__main__":
    unittest.main()
