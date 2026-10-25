"""本机PostgreSQL真实事务：每项独立随机schema，不接触现有用户/订单。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import unittest
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
from psycopg import sql

from database import load_database_url
from portal_auth import hash_password
from portal_store import PostgresPortalStore, PortalConflictError, PortalNotFoundError
from tests.test_customer_portal import fixture_image
from vision_service import validate_image


@unittest.skipUnless(os.getenv("RUN_PORTAL_POSTGRES_INTEGRATION") == "1", "需显式启用本机PG集成")
class PortalPostgresIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.url = load_database_url()
        if urlsplit(self.url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            self.skipTest("只允许本机PostgreSQL")
        self.schema = "opspilot_portal_it_" + uuid4().hex
        with psycopg.connect(self.url, connect_timeout=5) as db:
            db.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup)
        self.migration = (Path(__file__).resolve().parents[1]/"db/migrations/003_customer_portal.sql").read_text(encoding="utf-8")
        with self.connection() as db: db.execute(self.migration)
        self.store = PostgresPortalStore(self.connection)

    def connection(self):
        return psycopg.connect(self.url, connect_timeout=5, options=f"-c search_path={self.schema},public")

    def cleanup(self):
        self.assertRegex(self.schema, r"^opspilot_portal_it_[a-f0-9]{32}$")
        with psycopg.connect(self.url, connect_timeout=5) as db:
            db.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def payload(self, message="查询订单 O-7101"):
        return {"request_id":str(uuid4()), "message":message, "mode":"mock"}

    def test_concurrent_conversation_creation_with_same_key_creates_one_row(self):
        key = str(uuid4())
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: str(self.store.create_conversation("U-unit","新会话",key)["id"]),range(8)))
        self.assertEqual(set(ids),{key})
        with self.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM portal_conversations").fetchone()[0],1)
        with self.assertRaises(PortalConflictError):
            self.store.create_conversation("U-other","not-owned",key)
        self.assertEqual(self.store.create_conversation("U-unit","must-not-overwrite",key)["title"],"新会话")

    def test_creation_retry_still_works_when_quota_is_full(self):
        key = str(uuid4())
        self.store.create_conversation("U-unit","first",key)
        with self.connection() as db:
            db.execute("""INSERT INTO portal_conversations(id,owner_user_id,title)
                SELECT gen_random_uuid(),'U-unit','quota' FROM generate_series(1,199)""")
        self.assertEqual(str(self.store.create_conversation("U-unit","first",key)["id"]),key)
        with self.assertRaises(PortalConflictError):
            self.store.create_conversation("U-unit","new")

    def test_history_excludes_empty_and_attachment_only_drafts_but_keeps_failed_turns(self):
        empty = str(self.store.create_conversation("U-unit","old empty")["id"])
        attachment = str(self.store.create_conversation("U-unit","attachment")["id"])
        self.store.add_image("U-unit",attachment,validate_image(fixture_image()))
        populated = str(self.store.create_conversation("U-unit","actual question")["id"])
        turn,_ = self.store.claim_turn("U-unit",populated,self.payload(),[],"agent")
        self.store.finish_turn("U-unit",populated,str(turn["id"]),None,"FAILED","InputError")
        items=self.store.list_conversations("U-unit")
        self.assertEqual([str(row["id"]) for row in items],[populated])
        self.assertEqual(self.store.list_conversations("U-unit",1),[])
        self.assertEqual(self.store.list_conversations("U-other"),[])
        self.assertEqual(self.store.conversation("U-unit",empty)["title"],"old empty")
        self.assertEqual(len(self.store.conversation("U-unit",attachment)["turns"]),0)

    def test_old_empty_rows_at_quota_can_be_reused_without_deleting_data(self):
        with self.connection() as db:
            db.execute("""INSERT INTO portal_conversations(id,owner_user_id,title)
                SELECT gen_random_uuid(),'U-unit','新会话' FROM generate_series(1,200)""")
        recovered=self.store.create_conversation("U-unit","新会话",str(uuid4()))
        self.assertEqual(recovered["owner_user_id"],"U-unit")
        with self.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM portal_conversations").fetchone()[0],200)
        self.assertEqual(self.store.list_conversations("U-unit"),[])

    def test_session_persists_across_store_recreation_and_revokes(self):
        self.store.create_account("unit-user", "U-unit", hash_password("test-user-password"))
        self.store.create_session("unit-user", "test-token-hash", "test-csrf-hash")
        other = PostgresPortalStore(self.connection)
        self.assertEqual(other.session("test-token-hash")["user_id"], "U-unit")
        other.revoke_session("test-token-hash")
        self.assertIsNone(self.store.session("test-token-hash"))

    def test_expired_and_disabled_accounts_cannot_use_sessions(self):
        self.store.create_account("unit-user", "U-unit", hash_password("test-user-password"))
        self.store.create_session("unit-user", "token-a", "csrf-a")
        with self.connection() as db: db.execute("UPDATE portal_sessions SET expires_at=NOW()-INTERVAL '1 second'")
        self.assertIsNone(self.store.session("token-a"))
        self.store.create_session("unit-user", "token-b", "csrf-b")
        with self.connection() as db: db.execute("UPDATE portal_accounts SET active=false")
        self.assertIsNone(self.store.session("token-b"))

    def test_login_limit_is_atomic_across_connections(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.admit_login("unit-user", "127.0.0.1"), range(20)))
        self.assertEqual(sum(results), 10)

    def test_history_and_images_are_durable_and_owner_scoped(self):
        cid = str(self.store.create_conversation("U-unit", "private")["id"])
        image = self.store.add_image("U-unit", cid, validate_image(fixture_image()))
        image_id = str(image["id"])
        other = PostgresPortalStore(self.connection)
        self.assertEqual(other.image("U-unit", cid, image_id)["width"], 16)
        self.assertEqual(other.list_conversations("U-other"), [])
        with self.assertRaises(PortalNotFoundError): other.conversation("U-other", cid)
        second = str(other.create_conversation("U-unit", "second")["id"])
        with self.assertRaises(PortalNotFoundError): other.image("U-unit", second, image_id)

    def test_repeated_request_returns_same_turn_and_changed_payload_is_rejected(self):
        cid = str(self.store.create_conversation("U-unit", "test")["id"])
        payload = self.payload()
        turn, created = self.store.claim_turn("U-unit", cid, payload, [], "agent")
        repeated, again = self.store.claim_turn("U-unit", cid, payload, [], "agent")
        self.assertTrue(created); self.assertFalse(again)
        self.assertEqual(turn["id"], repeated["id"])
        self.assertEqual(str(turn["request_id"]), payload["request_id"])
        self.assertNotIn("request_hash", turn)
        recovered = self.store.conversation("U-unit", cid)["turns"][0]
        self.assertEqual(str(recovered["request_id"]), payload["request_id"])
        self.assertNotIn("request_hash", recovered)
        with self.assertRaises(PortalConflictError):
            self.store.claim_turn("U-unit", cid, {**payload,"message":"different"}, [], "agent")
        self.store.finish_turn("U-unit", cid, str(turn["id"]), {"answer":"answer", "status":"COMPLETED"}, "COMPLETED")
        self.assertEqual(self.store.conversation("U-unit", cid)["turns"][0]["result"]["answer"], "answer")

    def test_concurrent_turn_claims_only_one_request(self):
        cid = str(self.store.create_conversation("U-unit", "test")["id"])
        def claim(_):
            try: return self.store.claim_turn("U-unit", cid, self.payload(), [], "agent")[1]
            except PortalConflictError: return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(claim, range(8)))
        self.assertEqual(sum(results), 1)

    def test_action_claim_refreshes_timeout_clock_and_rejects_foreign_turn(self):
        cid = str(self.store.create_conversation("U-unit", "old approval")["id"])
        turn, _ = self.store.claim_turn("U-unit", cid, self.payload(), [], "agent")
        tid = str(turn["id"])
        self.store.finish_turn("U-unit", cid, tid, {"status": "WAITING_CONFIRMATION"}, "WAITING_CONFIRMATION")
        with self.connection() as db:
            db.execute("UPDATE portal_turns SET updated_at=NOW()-INTERVAL '1 hour' WHERE id=%s", (tid,))
        self.store.claim_action("U-unit", cid, tid)
        with self.connection() as db:
            self.assertTrue(db.execute("SELECT updated_at>NOW()-INTERVAL '1 minute' FROM portal_turns WHERE id=%s", (tid,)).fetchone()[0])
        self.assertEqual(str(self.store.conversation("U-unit", cid)["active_turn_id"]), tid)
        other = str(self.store.create_conversation("U-unit", "other")["id"])
        with self.assertRaises(PortalNotFoundError):
            self.store.claim_action("U-unit", other, tid)
        self.assertIsNone(self.store.conversation("U-unit", other)["active_turn_id"])

    def test_stale_sync_does_not_release_new_action_claim(self):
        cid = str(self.store.create_conversation("U-unit", "sync race")["id"])
        turn, _ = self.store.claim_turn("U-unit", cid, self.payload(), [], "agent")
        tid = str(turn["id"])
        saved = self.store.finish_turn("U-unit", cid, tid, {"status": "WAITING_CONFIRMATION"}, "WAITING_CONFIRMATION")
        self.store.claim_action("U-unit", cid, tid)
        with self.assertRaises(PortalConflictError):
            self.store.finish_turn("U-unit", cid, tid, saved["result"], "WAITING_CONFIRMATION",
                expected_snapshot=(saved["updated_at"], None))
        self.assertEqual(str(self.store.conversation("U-unit", cid)["active_turn_id"]), tid)

    def test_more_than_100_turns_keyset_pages_and_new_claim(self):
        cid=str(self.store.create_conversation('U-unit','long')['id'])
        with self.connection() as db:
            db.execute("""INSERT INTO portal_turns(id,conversation_id,request_id,request_hash,message,mode,kind,status,created_at)
                SELECT gen_random_uuid(),%s,gen_random_uuid(),'unit',i::text,'mock','agent','COMPLETED',NOW()
                FROM generate_series(1,137) i""",(cid,))
        pages=[];page=self.store.conversation('U-unit',cid)
        while True:
            pages.extend(str(t['id']) for t in page['turns'])
            if not page['next_before']:break
            page=self.store.conversation('U-unit',cid,page['next_before'])
        self.assertEqual(len(pages),137);self.assertEqual(len(set(pages)),137)
        turn,created=self.store.claim_turn('U-unit',cid,self.payload(),[],'agent')
        self.assertTrue(created)
        self.assertEqual(str(self.store.request_turn('U-unit',cid,turn['request_id'])['id']),str(turn['id']))
        with self.assertRaises(PortalNotFoundError):self.store.conversation('U-other',cid)

    def test_semantic_memory_is_durable_owner_scoped_and_deletes_with_source(self):
        from conversation_memory import MEMORY_MODEL,source_hash
        with self.connection() as db:
            if not db.execute("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname='vector')").fetchone()[0]:
                self.skipTest('该实例没有已安装pgvector；不在测试schema创建共享扩展')
            db.execute((Path(__file__).resolve().parents[1]/'db/migrations/004_conversation_memory.sql').read_text(encoding='utf-8'))
        cid=str(self.store.create_conversation('U-unit','memory')['id'])
        payload=self.payload('保留购物小票。')
        turn,_=self.store.claim_turn('U-unit',cid,payload,[],'agent');tid=str(turn['id'])
        self.store.finish_turn('U-unit',cid,tid,{'answer':'历史信息不是批准'},'COMPLETED')
        vector=[1.0]+[0.0]*511
        self.store.save_memory('U-unit',cid,tid,source_hash(payload['message']),MEMORY_MODEL,[vector,vector])
        other=PostgresPortalStore(self.connection)
        self.assertEqual(len(other.search_memory('U-unit',cid,MEMORY_MODEL,vector)),1)
        self.assertEqual(other.unindexed_memory('U-unit',cid,MEMORY_MODEL),[])
        with self.assertRaises(PortalNotFoundError):other.search_memory('U-other',cid,MEMORY_MODEL,vector)
        with self.assertRaises(PortalConflictError):other.save_memory('U-unit',cid,tid,'stale',MEMORY_MODEL,[vector])
        with self.connection() as db:
            db.execute('DELETE FROM portal_turns WHERE id=%s',(tid,))
            self.assertEqual(db.execute('SELECT COUNT(*) FROM portal_turn_memory').fetchone()[0],0)

    def test_unknown_business_result_blocks_new_turn_not_replayed(self):
        cid = str(self.store.create_conversation("U-unit", "test")["id"])
        turn, _ = self.store.claim_turn("U-unit", cid, self.payload(), [], "agent")
        self.store.finish_turn("U-unit", cid, str(turn["id"]), None, "RESULT_UNKNOWN", "Timeout")
        with self.assertRaises(PortalConflictError):
            self.store.claim_turn("U-unit", cid, self.payload(), [], "agent")

    def test_migration_rerun_preserves_accounts_and_conversations(self):
        self.store.create_account("unit-user", "U-unit", hash_password("test-user-password"))
        cid = str(self.store.create_conversation("U-unit", "preserved")["id"])
        with self.connection() as db: db.execute(self.migration)
        self.assertEqual(self.store.account("unit-user")["user_id"], "U-unit")
        self.assertEqual(self.store.conversation("U-unit", cid)["title"], "preserved")

    def test_stale_vision_recovery_unlocks_and_rejects_late_completion(self):
        cid = str(self.store.create_conversation("U-unit", "vision recovery")["id"])
        turn, _ = self.store.claim_turn("U-unit", cid, self.payload(), [], "vision")
        tid = str(turn["id"])
        with self.assertRaises(PortalConflictError):
            self.store.recover_interrupted_vision("U-unit", cid, tid)
        with self.connection() as db:
            db.execute("UPDATE portal_turns SET updated_at=NOW()-INTERVAL '16 minutes' WHERE id=%s", (tid,))
        with self.assertRaises(PortalNotFoundError):
            self.store.recover_interrupted_vision("U-other", cid, tid)
        recovered = self.store.recover_interrupted_vision("U-unit", cid, tid)
        self.assertEqual(recovered["error_code"], "VISION_INTERRUPTED")
        self.assertEqual(recovered["status"], "FAILED")
        self.assertIsNone(self.store.conversation("U-unit", cid)["active_turn_id"])
        following, created = self.store.claim_turn("U-unit", cid, self.payload("next"), [], "vision")
        self.assertTrue(created)
        late = self.store.finish_turn("U-unit", cid, tid, {"answer":"late"}, "COMPLETED")
        self.assertEqual(late["status"], "FAILED")
        self.assertEqual(str(self.store.conversation("U-unit", cid)["active_turn_id"]), str(following["id"]))

    def test_vision_recovery_cannot_clear_another_turn_or_recover_agent(self):
        cid = str(self.store.create_conversation("U-unit", "guard")["id"])
        turn, _ = self.store.claim_turn("U-unit", cid, self.payload(), [], "vision")
        tid = str(turn["id"])
        with self.connection() as db:
            db.execute("UPDATE portal_turns SET updated_at=NOW()-INTERVAL '16 minutes' WHERE id=%s", (tid,))
            db.execute("UPDATE portal_conversations SET active_turn_id=%s WHERE id=%s", (str(uuid4()),cid))
        with self.assertRaises(PortalConflictError):
            self.store.recover_interrupted_vision("U-unit", cid, tid)
        with self.connection() as db:
            db.execute("UPDATE portal_turns SET kind='agent' WHERE id=%s", (tid,))
        with self.assertRaises(PortalConflictError):
            self.store.recover_interrupted_vision("U-unit", cid, tid)
