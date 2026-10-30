import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

from _load import ROOT

# the NSE pack: nine skills (the upstream analyst and pelosi skills have no Indian equivalent)
SKILLS = ["trader-scanner", "trader-news", "trader-entry", "trader-exit", "trader-timemachine",
          "trader-risk", "trader-budget", "trader-broker", "trader-alerts"]
TOOLS = {"trader-scanner": "scan", "trader-news": "news", "trader-entry": "entry", "trader-exit": "exit",
         "trader-timemachine": "timemachine", "trader-risk": "check", "trader-budget": "size",
         "trader-broker": "ticket", "trader-alerts": "alerts"}
ABOUT = ("9 Claude skills for NSE day trading with Zerodha: scan the NSE for setups, track the news, "
         "time every entry and exit, and replay every outcome before you risk a rupee.")
# skills on the way to an order carry the safety block
ORDER_PATH = ["trader-scanner", "trader-entry", "trader-exit", "trader-timemachine", "trader-budget",
              "trader-risk", "trader-broker", "trader-alerts"]
DROPPED = ["trader-analyst", "trader-pelosi"]
# US-only things that must not survive the port (outside the tests and MIGRATION.md)
US_WORDS = ["NYSE", "Nasdaq Trader", "nasdaqtrader", "Alpaca", "alpaca", "place_stock_order", "IBKR",
            "Interactive Brokers", "SEC EDGAR", "sec_user_agent", "New York", "15:55", "TSLA"]
EM_DASH = chr(0x2014)
FINRA_NOTICE = "pdt_guard is a US (FINRA) rule with no NSE equivalent"


def frontmatter(path):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


def every_text_file():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "trader")]
        for f in files:
            if f.endswith((".md", ".py", ".json", ".csv", ".txt", ".xml", ".sh", ".service", ".example")) \
                    or f in ("LICENSE", ".gitignore"):
                yield os.path.join(base, f)


def product_files():
    """Skill folders, trader_common, deploy and the top-level docs: not the tests."""
    for path in every_text_file():
        rel = os.path.relpath(path, ROOT)
        if rel.startswith("tests" + os.sep) or rel == "MIGRATION.md":
            continue
        yield path, rel


class Repo(unittest.TestCase):
    def test_nine_skill_folders_at_the_root(self):
        found = sorted(d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))
        for d in DROPPED:
            self.assertFalse(os.path.exists(os.path.join(ROOT, d)), d)

    def test_root_layout(self):
        root = sorted(d for d in os.listdir(ROOT) if not d.startswith((".git", ".DS")) and d != "trader")
        want = [".claude-plugin", "LICENSE", "README.md", "deploy", "tests", "trader_common"] + SKILLS
        for optional in ("MIGRATION.md", "docs"):     # docs: roadmap and proposals, not part of the skills
            if os.path.exists(os.path.join(ROOT, optional)):
                want.append(optional)
        self.assertEqual(root, sorted(want))

    def test_frontmatter_name_and_description(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertRegex(fm, r"description: ", s)
            self.assertGreater(len(fm), 200, "%s description is too thin" % s)
            self.assertGreater(len(text.splitlines()), 60, s)

    def test_order_path_skills_carry_the_safety_rule(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("explicit yes", low, s)
            self.assertIn('"dry_run": true', low, s)
            self.assertIn("always allow", low, s)
            self.assertIn("scheduled task", low, s)

    def test_plugin_manifest_points_at_the_root_skills(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            plugin = json.load(fh)
        self.assertEqual(plugin["name"], "nse-day-trader-skill")
        self.assertEqual(plugin["skills"], "./")
        self.assertEqual(plugin["description"], ABOUT)
        self.assertEqual(plugin["repository"], "https://github.com/asaurav025/nse-day-trader-skill")
        with open(os.path.join(ROOT, ".claude-plugin", "marketplace.json"), encoding="utf-8") as fh:
            market = json.load(fh)
        self.assertEqual(market["name"], "nse-day-trader-skill")
        self.assertEqual(market["plugins"][0]["name"], "nse-day-trader-skill")
        self.assertEqual(market["plugins"][0]["source"], "./")

    def test_readme_installs_the_fork(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = " ".join(fh.read().split())
        self.assertTrue(readme.startswith("# The Day Trader skill for NSE"))
        for line in (ABOUT,
                     "https://github.com/asaurav025/nse-day-trader-skill install skill",
                     "/plugin marketplace add asaurav025/nse-day-trader-skill",
                     "/plugin install nse-day-trader-skill@nse-day-trader-skill",
                     "## Fine print", "Not financial advice."):
            self.assertIn(line, readme)
        for s in SKILLS:
            self.assertIn("/" + s, readme, s)
        for d in DROPPED:
            self.assertNotIn("/" + d, readme, d)

    def test_no_em_dashes_and_no_personal_email(self):
        for path in every_text_file():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            rel = os.path.relpath(path, ROOT)
            if not rel.startswith(os.path.join("tests", "fixtures")):  # fixtures are real data, kept as published
                self.assertNotIn(EM_DASH, text, rel)
            for mail in ("jakeschincariol" + "@" + "gmail.com", "asaurav025" + "@" + "gmail.com"):
                self.assertNotIn(mail, text, rel)  # built, so this file passes too

    def test_no_us_leftovers(self):
        for path, rel in product_files():
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            for w in US_WORDS:
                self.assertNotIn(w, text, "%s still says %r" % (rel, w))
            for line in text.splitlines():
                if "FINRA" in line:
                    self.assertIn(FINRA_NOTICE, line, rel)

    def test_every_tool_runs_on_python_3_8(self):
        # no 3.9+ only modules or syntax in the tools or the shared module (the README promises 3.8)
        folders = SKILLS + ["trader_common"]
        for s in folders:
            folder = os.path.join(ROOT, s)
            for f in os.listdir(folder):
                if not f.endswith(".py"):
                    continue
                with open(os.path.join(folder, f), encoding="utf-8") as fh:
                    src = fh.read()
                for bad in ("import zoneinfo", "from zoneinfo", ".removeprefix(", ".removesuffix(",
                            "match ", "list[", "dict[", "tuple["):
                    if bad == "match ":
                        self.assertNotRegex(src, r"(?m)^\s+match \w+.*:$", "%s/%s" % (s, f))
                    elif bad.endswith("["):
                        self.assertNotRegex(src, r"(?<![\w.])%s" % re.escape(bad), "%s/%s" % (s, f))
                    else:
                        self.assertNotIn(bad, src, "%s/%s" % (s, f))

    def test_every_tool_runs_standalone_from_anywhere(self):
        # each script finds trader_common by itself: python3 <skill>/<tool>.py from any folder
        with tempfile.TemporaryDirectory() as tmp:
            for s, tool in sorted(TOOLS.items()):
                path = os.path.join(ROOT, s, tool + ".py")
                r = subprocess.run([sys.executable, path, "--help"], cwd=tmp, capture_output=True, text=True,
                                   timeout=60)
                self.assertEqual(r.returncode, 0, "%s: %s" % (path, r.stderr[-300:]))

    def test_day_trading_limits_ship_in_the_example_config(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        for k in ("max_loss_per_trade_inr", "max_daily_loss_inr", "max_order_inr", "max_trades_per_day",
                  "min_reward_risk", "require_stop", "no_entries_before", "no_entries_after", "flat_by",
                  "entry_limit_buffer_pct", "stop_limit_buffer_pct", "exit_limit_buffer_pct"):
            self.assertIn(k, cfg, k)
        self.assertEqual((cfg["no_entries_before"], cfg["no_entries_after"], cfg["flat_by"]),
                         ("09:30", "14:45", "15:10"))
        self.assertEqual(cfg["product"], "MIS")
        self.assertEqual(cfg["broker"], "zerodha")
        self.assertIs(cfg["require_stop"], True)
        self.assertIs(cfg.get("allow_short", False), False)
        self.assertIs(cfg.get("allow_margin", False), False)
        for k in cfg:
            self.assertFalse(k.endswith("_usd"), k)
            self.assertNotIn(k, ("pdt_guard", "paper", "fractional", "allow_options", "sec_user_agent"))

    def test_dry_run_is_the_default(self):
        with open(os.path.join(ROOT, "trader-budget", "config.example.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertIs(cfg["dry_run"], True)

    def test_secrets_and_state_are_never_tracked(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as fh:
            ignore = fh.read().splitlines()
        for entry in ("trader/config.json", "trader/state/", "trader/", "deploy/.env.deploy", "*.env",
                      ".mcp.json"):
            self.assertIn(entry, ignore, entry)
        r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
        if r.returncode == 0:   # a source tarball has no git: nothing to check then
            tracked = r.stdout.splitlines()
            self.assertFalse([f for f in tracked if f.startswith("trader/")], "trader/ is tracked")
            self.assertNotIn("deploy/.env.deploy", tracked)
            self.assertFalse([f for f in tracked if f.endswith(".env")], ".env files are tracked")

    def test_deploy_scripts_parse(self):
        for f in ("bootstrap.sh", "check-egress-ip.sh"):
            path = os.path.join(ROOT, "deploy", f)
            r = subprocess.run(["bash", "-n", path], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, "%s: %s" % (f, r.stderr))
            with open(path, encoding="utf-8") as fh:
                self.assertIn("set -euo pipefail", fh.read(), f)

    def test_deploy_examples_hold_no_secrets(self):
        with open(os.path.join(ROOT, "deploy", "kite.env.example"), encoding="utf-8") as fh:
            env = dict(l.split("=", 1) for l in fh.read().splitlines() if l and not l.startswith("#") and "=" in l)
        self.assertEqual(env["KITE_API_KEY"], "")
        self.assertEqual(env["KITE_API_SECRET"], "")
        self.assertEqual(env["APP_HOST"], "127.0.0.1")
        self.assertIn("modify_order", env["EXCLUDED_TOOLS"])
        with open(os.path.join(ROOT, "deploy", ".env.deploy.example"), encoding="utf-8") as fh:
            self.assertRegex(fh.read(), r"(?m)^CF_TUNNEL_TOKEN=$")


if __name__ == "__main__":
    unittest.main()
