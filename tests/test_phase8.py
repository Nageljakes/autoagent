import ast
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/autohub-portal/scripts"


def function_from_file(filename, name, namespace=None):
    # Exercise pure boundaries without requiring CRM/network packages.
    tree = ast.parse((SCRIPTS / filename).read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    scope = namespace or {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), filename, "exec"), scope)
    return scope[name]


class Phase8Tests(unittest.TestCase):
    def test_confirmation_rejects_http_and_application_failures(self):
        check = function_from_file("action_prospect.py", "require_crm_confirmation")
        for code, payload, text in [(500, {"success": True}, ""), (200, {"success": False}, ""),
                                    (200, {"error": "denied"}, ""), (200, False, ""),
                                    (200, None, '<input type="password">'), (204, None, "")]:
            with self.subTest(code=code, payload=payload, text=text):
                response = Mock(status_code=code, text=text)
                response.json.return_value = payload
                with self.assertRaises(RuntimeError):
                    check(response)
        for code, payload in [(200, None), (201, {"success": True})]:
            response = Mock(status_code=code, text="")
            response.json.return_value = payload
            check(response)

    def test_diary_reads_schema_and_latest_search_window(self):
        for filename in ["generate_diary_cards.py", "generate_all_21_diary_cards.py"]:
            requests = Mock()
            history = function_from_file(filename, "get_wa_history", {
                "requests": requests, "os": os, "re": re, "WA_API": "http://fixture",
                "clean_phone": lambda phone: phone,
            })
            requests.get.return_value.status_code = 200
            requests.get.return_value.json.return_value = {"messages": [{"from_me": 1, "content": "Latest reply"}]}
            with patch.dict(os.environ, {"SALESPERSON_NAME": "Configured Advisor"}):
                self.assertEqual(history("123", "Fixture Customer", []), "Configured Advisor: Latest reply")
            requests.get.return_value.json.return_value = {"results": [
                {"content": str(i), "from_me": 0} for i in range(5, 0, -1)]}
            self.assertEqual(history("", "Fixture Customer", []), "Customer: 3 | Customer: 4 | Customer: 5")

    def test_both_export_commands_use_configured_databases_from_any_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            crm = tmp / "crm.db"
            wa = tmp / "wa.db"
            out = tmp / "nested" / "export.json"
            with sqlite3.connect(crm) as db:
                db.execute("CREATE TABLE prospects (custid, name, phone, vehicle_model, likelihood_tier, likelihood_score, last_diary_date, first_seen, status)")
                db.execute("INSERT INTO prospects VALUES ('1','Fixture Customer','0712345678','Car','HIGH',90,NULL,NULL,'ACTIVE')")
                db.execute("CREATE TABLE prospect_notes (custid, entry_date, note, recorded_at)")
            db.close()
            with sqlite3.connect(wa) as db:
                db.execute("CREATE TABLE messages (phone_number, content, from_me, timestamp)")
                db.executemany("INSERT INTO messages VALUES (?,?,?,?)", [('27712345678','old',0,1), ('27712345678','new',1,2)])
            db.close()
            env = dict(os.environ, PROSPECT_HISTORY_DB=str(crm), SQLITE_DB_PATH=str(wa),
                       CRM_SYNC_JSON=str(out), CRM_GIST_ID="", SALESPERSON_NAME="Configured Advisor")
            for script in ["scripts/generate_crm_sync.py", "jax-shared/scripts/crm_autosync.py"]:
                subprocess.run([sys.executable, str(ROOT / script)], cwd=tmp, env=env, check=True, capture_output=True)
                data = json.loads(out.read_text())
                self.assertEqual(data["leads"][0]["waSnapshot"], "Configured Advisor: new")
                self.assertEqual(data["leads"][0]["agent"], "Configured Advisor")


if __name__ == "__main__":
    unittest.main()
