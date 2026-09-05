import ast
import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/autohub-portal/scripts'))
from customer_identity import lookup_customer, AmbiguousCustomerError


class CustomerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = pathlib.Path(self.tmp.name) / 'synthetic.db'
        conn = sqlite3.connect(self.db)
        try:
            conn.execute('CREATE TABLE prospects(custid TEXT, name TEXT, phone TEXT, vehicle_model TEXT, contact_count INTEGER)')
            conn.executemany('INSERT INTO prospects VALUES(?,?,?,?,?)', [
                ('one','Alex Synthetic','0820000001','Example A',1),
                ('two','Alex Synthetic','27820000002','Example B',1)])
            conn.commit()
        finally:
            conn.close()

    def test_ambiguous_name_is_rejected(self):
        with self.assertRaises(AmbiguousCustomerError):
            lookup_customer(self.db, 'Alex')

    def test_id_and_normalized_exact_phone_work(self):
        for query in ['one', '0820000001', '+27 (82) 000-0001']:
            self.assertEqual(lookup_customer(self.db, query)['custid'], 'one')

    def test_partial_phone_and_wildcards_do_not_match(self):
        for query in ['820000001', '%', '_', "' OR 1=1 --"]:
            self.assertIsNone(lookup_customer(self.db, query))

    def test_empty_query_is_rejected(self):
        with self.assertRaises(ValueError):
            lookup_customer(self.db, ' ')

    def test_duplicate_phone_requires_customer_id(self):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("UPDATE prospects SET phone='0820000001' WHERE custid='two'")
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(AmbiguousCustomerError):
            lookup_customer(self.db, '27820000001')
        self.assertEqual(lookup_customer(self.db,'two')['custid'],'two')

    def test_followup_stops_on_ambiguity_before_bridge_or_send(self):
        # Execute the actual resolver without importing network/CRM adapters.
        for script in ['skills/whatsapp-monitor/scripts/action_followup.py', 'skills/autohub-portal/scripts/action_followup.py']:
            tree = ast.parse((ROOT / script).read_text(encoding='utf8'))
            fn = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='resolve_customer_context')
            import re
            env = {'re':re, 'lookup_customer':lookup_customer,'CRM_DB_PATH':self.db,
                   'clean_customer_name':lambda q:(q,''),'normalize_phone':lambda q:q}
            exec(compile(ast.Module(body=[fn],type_ignores=[]),script,'exec'),env)
            with self.assertRaises(AmbiguousCustomerError):
                env['resolve_customer_context']('Alex')

if __name__ == '__main__':
    unittest.main()
