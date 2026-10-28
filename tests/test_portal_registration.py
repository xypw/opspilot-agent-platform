"""Registration privilege, revocation and transaction boundaries; offline by default."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, Mock

from portal_store import PortalConflictError
from tests import test_customer_portal as portal_fixture
from tests import test_portal_postgres_integration as pg_fixture


class RegistrationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=portal_fixture.PortalAuthenticationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.client=self.fixture.client

    def test_registration_cannot_choose_identity_or_role_and_password_is_not_echoed(self):
        for extra in ({'roles':['admin']},{'user_id':'U-admin'},{'demo_order_id':'O-7101'}):
            response=self.client.post('/auth/register',json={'username':'newuser','password':'unit-password-long',**extra})
            self.assertEqual(response.status_code,422)
            self.assertNotIn('unit-password-long',response.text)

    def test_short_password_and_cross_origin_registration_are_rejected(self):
        self.assertEqual(self.client.post('/auth/register',json={'username':'newuser','password':'short'}).status_code,422)
        response=self.client.post('/auth/register',headers={'Origin':'https://foreign.example'},
                                  json={'username':'newuser','password':'unit-password-long'})
        self.assertEqual(response.status_code,403)

    def test_customer_cannot_list_users_or_change_accounts(self):
        self.fixture.login()
        self.assertEqual(self.client.get('/auth/admin/users').status_code,403)
        self.assertEqual(self.client.patch('/auth/admin/users/customer1',headers=self.fixture.csrf(),
                                           json={'active':False}).status_code,403)

    def test_upgrade_cannot_promote_a_registered_username(self):
        from contextlib import contextmanager
        from scripts.upgrade_portal_accounts import upgrade
        from auth import AuthenticatedUser
        cursor = Mock()
        cursor.fetchall.return_value = [
            {'username':'ys10087','user_id':'U-registered','roles':['customer']}]

        @contextmanager
        def transaction():
            yield cursor

        store = Mock(transaction=transaction)
        configured = {'unit-token': AuthenticatedUser('U-admin',frozenset({'knowledge_admin'}),'unit-token')}
        with patch('portal_store.PostgresPortalStore',return_value=store), \
                patch('auth._configured_tokens',return_value=configured):
            with self.assertRaises(RuntimeError):
                upgrade()
        self.assertFalse(any('UPDATE portal_accounts' in call.args[0]
                             for call in cursor.execute.call_args_list))


@unittest.skipUnless(os.getenv('RUN_PORTAL_POSTGRES_INTEGRATION')=='1','需显式启用本机PG集成')
class RegistrationPostgresTests(unittest.TestCase):
    connection=pg_fixture.PortalPostgresIntegrationTests.connection
    cleanup=pg_fixture.PortalPostgresIntegrationTests.cleanup

    def setUp(self):
        pg_fixture.PortalPostgresIntegrationTests.setUp(self)
        with self.connection() as db:
            db.execute((Path(__file__).resolve().parents[1]/'db/migrations/005_portal_registration.sql').read_text(encoding='utf-8'))
            db.execute('''CREATE TABLE orders(id TEXT PRIMARY KEY,owner_user_id TEXT,status TEXT,product TEXT,
                delivered_at DATE,amount_cents INT);
                CREATE TABLE order_return_policies(order_id TEXT PRIMARY KEY REFERENCES orders(id),category TEXT,
                product_condition TEXT,exclusion_confirmed_at_purchase BOOLEAN,downloaded_or_unsealed BOOLEAN,
                activated BOOLEAN,merchant_no_reason_days INT,policy_version TEXT)''')

    def test_register_creates_owned_order_policy_and_customer_only(self):
        row=self.store.register('newuser','test-hash')
        self.assertEqual(row['roles'],['customer'])
        with self.connection() as db:
            self.assertEqual(db.execute('SELECT owner_user_id FROM orders WHERE id=%s',
                (row['demo_order_id'],)).fetchone()[0],row['user_id'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM order_return_policies').fetchone()[0],1)

    def test_policy_failure_rolls_back_account_and_order(self):
        with self.connection() as db:
            db.execute("ALTER TABLE order_return_policies ADD CHECK(category='impossible')")
        with self.assertRaises(Exception):self.store.register('rollback','test-hash')
        self.assertIsNone(self.store.account('rollback'))
        with self.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],0)

    def test_concurrent_same_username_registers_once(self):
        def register(_):
            try:return self.store.register('concurrent','test-hash')
            except PortalConflictError:return None
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(register,range(4)))
        self.assertEqual(sum(x is not None for x in results),1)
        with self.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0],1)

    def test_disable_revokes_sessions_and_enable_does_not_restore_them(self):
        admin=self.store.register('adminunit','test-hash')
        self.store.register('customerunit','test-hash')
        with self.connection() as db:db.execute("UPDATE portal_accounts SET roles=ARRAY['admin','knowledge_admin'] WHERE username='adminunit'")
        self.store.create_session('customerunit','tokenhash','csrfhash')
        self.assertIsNotNone(self.store.session('tokenhash'))
        self.store.set_account_active('adminunit','customerunit',False)
        self.assertIsNone(self.store.session('tokenhash'))
        self.store.set_account_active('adminunit','customerunit',True)
        self.assertIsNone(self.store.session('tokenhash'))
        with self.assertRaises(PortalConflictError):self.store.set_account_active('adminunit','adminunit',False)
        with self.assertRaises(PermissionError):self.store.set_account_active('customerunit','adminunit',False)
        self.assertNotIn('password_hash',self.store.list_accounts()[0])

    def test_login_finishing_after_disable_cannot_restore_a_session(self):
        self.store.register('adminunit','test-hash')
        self.store.register('customerunit','test-hash')
        with self.connection() as db:
            db.execute("UPDATE portal_accounts SET roles=ARRAY['admin'] WHERE username='adminunit'")
        # Login read/verified the account before the administrator disabled it.
        self.assertIsNotNone(self.store.account('customerunit'))
        self.store.set_account_active('adminunit','customerunit',False)
        with self.assertRaises(PortalConflictError):
            self.store.create_session('customerunit','late-login','csrfhash')
        self.store.set_account_active('adminunit','customerunit',True)
        self.assertIsNone(self.store.session('late-login'))
