"""PostgreSQL 用户会话/可见聊天仓库。执行历史不替代 LangGraph 检查点。"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import hashlib
import json
from uuid import uuid4

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from database import create_database_connection


class PortalNotFoundError(LookupError):
    pass


class PortalConflictError(ValueError):
    pass


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def public_turn(row: dict) -> dict:
    # request_id is an owner-scoped correlation key, not a credential.
    # The portal must retain it to recover a lost POST response using GET.
    return {key: value for key, value in row.items()
            if key != "request_hash"}


class PostgresPortalStore:
    def __init__(self, connection_factory=create_database_connection):
        self.connect = connection_factory

    @contextmanager
    def transaction(self):
        with self.connect() as connection:
            with connection.cursor(row_factory=dict_row) as cursor:
                yield cursor

    def account(self, username: str) -> dict | None:
        with self.transaction() as cursor:
            cursor.execute("SELECT * FROM portal_accounts WHERE username=%s AND active", (username,))
            return cursor.fetchone()

    def create_account(self, username: str, user_id: str, password_hash: str) -> bool:
        with self.transaction() as cursor:
            cursor.execute("""INSERT INTO portal_accounts(username,user_id,password_hash)
                VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""", (username, user_id, password_hash))
            return cursor.rowcount == 1

    def admit_login(self, username: str, address: str) -> bool:
        with self.transaction() as cursor:
            admitted = True
            for scope, limit in (("user:" + username, 10), ("address:" + address, 60)):
                cursor.execute("""INSERT INTO portal_login_attempts(scope,window_start,attempts)
                    VALUES (%s,FLOOR(EXTRACT(EPOCH FROM NOW())/900)::bigint,1)
                    ON CONFLICT(scope) DO UPDATE SET
                    window_start=EXCLUDED.window_start,
                    attempts=CASE WHEN portal_login_attempts.window_start=EXCLUDED.window_start
                                  THEN portal_login_attempts.attempts+1 ELSE 1 END
                    RETURNING attempts""", (fingerprint(scope),))
                admitted = cursor.fetchone()["attempts"] <= limit and admitted
            return admitted

    def create_session(self, username: str, token_hash: str, csrf_hash: str):
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM portal_sessions WHERE expires_at <= NOW()")
            cursor.execute("""INSERT INTO portal_sessions(token_hash,username,csrf_hash,expires_at)
                VALUES (%s,%s,%s,NOW()+INTERVAL '8 hours')""", (token_hash, username, csrf_hash))

    def session(self, token_hash: str) -> dict | None:
        with self.transaction() as cursor:
            cursor.execute("""SELECT a.username,a.user_id,s.csrf_hash,s.expires_at
                FROM portal_sessions s JOIN portal_accounts a ON a.username=s.username
                WHERE s.token_hash=%s AND s.expires_at>NOW() AND a.active""", (token_hash,))
            return cursor.fetchone()

    def revoke_session(self, token_hash: str):
        with self.transaction() as cursor:
            cursor.execute("DELETE FROM portal_sessions WHERE token_hash=%s", (token_hash,))

    def _conversation(self, cursor, user_id: str, conversation_id: str, *, lock=False) -> dict:
        cursor.execute("""SELECT * FROM portal_conversations
            WHERE id=%s AND owner_user_id=%s""" + (" FOR UPDATE" if lock else ""),
            (conversation_id, user_id))
        row = cursor.fetchone()
        if row is None:
            raise PortalNotFoundError("会话不存在或不属于当前用户")
        return row

    def create_conversation(self, user_id: str, title: str, conversation_id: str | None = None) -> dict:
        conversation_id = conversation_id or str(uuid4())
        with self.transaction() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("portal-conversation:"+user_id,))
            # 浏览器在创建响应丢失后沿用同一ID；先返回原会话，再检查配额。
            cursor.execute("SELECT * FROM portal_conversations WHERE id=%s", (conversation_id,))
            existing = cursor.fetchone()
            if existing:
                if existing["owner_user_id"] != user_id:
                    raise PortalConflictError("无法使用该会话编号，请重新新建会话")
                return existing  # 不用重试请求的标题覆盖已有会话。
            cursor.execute("SELECT COUNT(*) AS count FROM portal_conversations WHERE owner_user_id=%s", (user_id,))
            if cursor.fetchone()["count"] >= 200:
                # 旧版本可能已用空白行占满配额；复用本人无消息、无图片的默认草稿，
                # 不删除记录，也不动有标题/附件/任务的会话。
                cursor.execute("""SELECT c.* FROM portal_conversations c
                    WHERE c.owner_user_id=%s AND c.title='新会话' AND c.active_turn_id IS NULL
                    AND NOT EXISTS (SELECT 1 FROM portal_turns t WHERE t.conversation_id=c.id)
                    AND NOT EXISTS (SELECT 1 FROM portal_images i WHERE i.conversation_id=c.id)
                    ORDER BY c.updated_at DESC,c.id LIMIT 1 FOR UPDATE OF c""", (user_id,))
                reusable = cursor.fetchone()
                if reusable:
                    return reusable
                raise PortalConflictError("演示账户最多保留200个会话，请联系管理员清理")
            cursor.execute("""INSERT INTO portal_conversations(id,owner_user_id,title)
                VALUES (%s,%s,%s) ON CONFLICT(id) DO NOTHING RETURNING *""",
                (conversation_id, user_id, title))
            created = cursor.fetchone()
            if created is None:
                raise PortalConflictError("无法使用该会话编号，请重新新建会话")
            return created

    def list_conversations(self, user_id: str, offset: int = 0) -> list[dict]:
        with self.transaction() as cursor:
            cursor.execute("""SELECT c.id,c.title,c.created_at,c.updated_at FROM portal_conversations c
                WHERE c.owner_user_id=%s
                AND EXISTS (SELECT 1 FROM portal_turns t WHERE t.conversation_id=c.id)
                ORDER BY c.updated_at DESC,c.id LIMIT 50 OFFSET %s""",
                (user_id, offset))
            return cursor.fetchall()

    def conversation(self, user_id: str, conversation_id: str, before: str | None = None) -> dict:
        with self.transaction() as cursor:
            row = self._conversation(cursor, user_id, conversation_id)
            boundary = ""
            params = [conversation_id]
            if before:
                cursor.execute("SELECT created_at,id FROM portal_turns WHERE conversation_id=%s AND id=%s",
                               (conversation_id,before))
                anchor=cursor.fetchone()
                if anchor is None:
                    raise PortalNotFoundError("历史游标不存在或不属于当前会话")
                boundary=" AND (created_at,id)<(%s,%s)"
                params.extend([anchor["created_at"],anchor["id"]])
            cursor.execute("SELECT * FROM portal_turns WHERE conversation_id=%s"+boundary+
                           " ORDER BY created_at DESC,id DESC LIMIT 51",params)
            found=cursor.fetchall()
            turns=list(reversed(found[:50]))
            return {**row,"turns":[public_turn(turn) for turn in turns],
                    "next_before":str(turns[0]["id"]) if len(found)>50 else None}

    def request_turn(self, user_id: str, conversation_id: str, request_id: str) -> dict:
        with self.transaction() as cursor:
            self._conversation(cursor,user_id,conversation_id)
            cursor.execute("SELECT * FROM portal_turns WHERE conversation_id=%s AND request_id=%s",
                           (conversation_id,request_id))
            row=cursor.fetchone()
            if row is None:
                raise PortalNotFoundError("原请求尚未登记或不存在")
            return public_turn(row)

    def unindexed_memory(self,user_id,cid,model,limit=32):
        with self.transaction() as cursor:
            self._conversation(cursor,user_id,cid)
            cursor.execute("""SELECT t.id,t.message FROM portal_turns t
                WHERE t.conversation_id=%s AND t.status<>'RUNNING'
                AND NOT EXISTS (SELECT 1 FROM portal_turn_memory m WHERE m.turn_id=t.id AND m.embedding_model=%s)
                ORDER BY t.created_at,t.id LIMIT %s""",(cid,model,min(limit,100)))
            return cursor.fetchall()

    def save_memory(self,user_id,cid,turn_id,expected_hash,model,vectors):
        from conversation_memory import source_hash,vector_literal
        if not vectors:raise ValueError('语义记忆向量不能为空')
        encoded=[vector_literal(vector) for vector in vectors]
        with self.transaction() as cursor:
            self._conversation(cursor,user_id,cid)
            cursor.execute("SELECT message FROM portal_turns WHERE id=%s AND conversation_id=%s FOR SHARE",(turn_id,cid))
            row=cursor.fetchone()
            if row is None or source_hash(row['message'])!=expected_hash:
                raise PortalConflictError('语义记忆来源已变化，不写入旧向量')
            cursor.execute('DELETE FROM portal_turn_memory WHERE turn_id=%s',(turn_id,))
            for part,value in enumerate(encoded):
                cursor.execute("""INSERT INTO portal_turn_memory(turn_id,part,embedding_model,source_hash,embedding)
                    VALUES (%s,%s,%s,%s,%s::vector)""",(turn_id,part,model,expected_hash,value))

    def search_memory(self,user_id,cid,model,vector,limit=8):
        from conversation_memory import vector_literal
        with self.transaction() as cursor:
            self._conversation(cursor,user_id,cid)
            cursor.execute("""SELECT t.*,max(m.source_hash) AS memory_source_hash,
                min(m.embedding <=> %s::vector) AS memory_distance FROM portal_turn_memory m
                JOIN portal_turns t ON t.id=m.turn_id
                JOIN portal_conversations c ON c.id=t.conversation_id
                WHERE c.owner_user_id=%s AND c.id=%s AND m.embedding_model=%s
                GROUP BY t.id ORDER BY memory_distance,t.created_at DESC,t.id DESC LIMIT %s""",
                (vector_literal(vector),user_id,cid,model,min(limit,20)))
            return [public_turn(row) for row in cursor.fetchall()]

    def add_image(self, user_id: str, conversation_id: str, image: dict) -> dict:
        with self.transaction() as cursor:
            # 账户级事务锁防止多个会话同时上传绕过总量限制；不是业务申请锁。
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("portal-upload:"+user_id,))
            self._conversation(cursor, user_id, conversation_id, lock=True)
            cursor.execute("""SELECT COUNT(*) AS count,COALESCE(SUM(octet_length(content)),0) AS bytes
                FROM portal_images WHERE owner_user_id=%s""", (user_id,))
            total = cursor.fetchone()
            if total["count"] >= 100 or total["bytes"] + len(image["content"]) > 100*1024*1024:
                raise PortalConflictError("当前演示账户图片存储已达上限，请联系管理员清理")
            image_id = str(uuid4())
            cursor.execute("""INSERT INTO portal_images
                (id,conversation_id,owner_user_id,mime_type,content,sha256,width,height)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (image_id, conversation_id, user_id,
                image["mime_type"], image["content"], image["sha256"], image["width"], image["height"]))
            return {"id": image_id, **{k:v for k,v in image.items() if k != "content"}}

    def image(self, user_id: str, conversation_id: str, image_id: str) -> dict:
        with self.transaction() as cursor:
            self._conversation(cursor, user_id, conversation_id)
            cursor.execute("""SELECT * FROM portal_images
                WHERE id=%s AND conversation_id=%s AND owner_user_id=%s""",
                (image_id, conversation_id, user_id))
            row = cursor.fetchone()
            if row is None:
                raise PortalNotFoundError("图片不存在或不属于当前会话")
            row["content"] = bytes(row["content"])
            return row

    def claim_turn(self, user_id: str, conversation_id: str, payload: dict,
                   image_ids: list[str], kind: str) -> tuple[dict, bool]:
        request_hash = fingerprint(json.dumps(payload, sort_keys=True, ensure_ascii=False))
        with self.transaction() as cursor:
            conversation = self._conversation(cursor, user_id, conversation_id, lock=True)
            cursor.execute("SELECT * FROM portal_turns WHERE conversation_id=%s AND request_id=%s",
                           (conversation_id, payload["request_id"]))
            previous = cursor.fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise PortalConflictError("同一请求编号不能用于不同内容")
                return public_turn(previous), False
            if conversation["active_turn_id"]:
                raise PortalConflictError("已有请求正在执行；请刷新状态，不要重复发送")
            cursor.execute("SELECT 1 FROM portal_turns WHERE conversation_id=%s AND status='RESULT_UNKNOWN' LIMIT 1",
                           (conversation_id,))
            if cursor.fetchone():
                raise PortalConflictError("上次业务结果未知，请先同步原任务状态")
            for image_id in image_ids:
                cursor.execute("""SELECT id FROM portal_images
                    WHERE id=%s AND conversation_id=%s AND owner_user_id=%s""",
                    (image_id, conversation_id, user_id))
                if cursor.fetchone() is None:
                    raise PortalNotFoundError("图片不存在或不属于当前会话")
            turn_id = str(uuid4())
            cursor.execute("""INSERT INTO portal_turns
                (id,conversation_id,request_id,request_hash,message,mode,image_ids,kind,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s::uuid[],%s,'RUNNING') RETURNING *""",
                (turn_id, conversation_id, payload["request_id"], request_hash, payload["message"],
                 payload["mode"], image_ids, kind))
            turn = public_turn(cursor.fetchone())
            cursor.execute("""UPDATE portal_conversations SET active_turn_id=%s,updated_at=NOW(),
                title=CASE WHEN title='新会话' THEN %s ELSE title END WHERE id=%s""",
                (turn_id, payload["message"][:40], conversation_id))
            return turn, True

    def turn(self, user_id: str, conversation_id: str, turn_id: str) -> dict:
        with self.transaction() as cursor:
            self._conversation(cursor, user_id, conversation_id)
            cursor.execute("SELECT * FROM portal_turns WHERE id=%s AND conversation_id=%s",
                           (turn_id, conversation_id))
            row = cursor.fetchone()
            if row is None:
                raise PortalNotFoundError("消息不存在或不属于当前会话")
            return public_turn(row)

    def claim_action(self, user_id: str, conversation_id: str, turn_id: str):
        with self.transaction() as cursor:
            conversation = self._conversation(cursor, user_id, conversation_id, lock=True)
            if conversation["active_turn_id"]:
                raise PortalConflictError("请求执行中，请等待或同步状态")
            cursor.execute("""UPDATE portal_turns SET updated_at=NOW()
                WHERE id=%s AND conversation_id=%s""", (turn_id, conversation_id))
            if cursor.rowcount != 1:
                raise PortalNotFoundError("消息不存在或不属于当前会话")
            cursor.execute("UPDATE portal_conversations SET active_turn_id=%s WHERE id=%s",
                           (turn_id, conversation_id))

    def recover_interrupted_vision(self, user_id: str, conversation_id: str, turn_id: str) -> dict:
        """只终止超时的只读图片任务；事务中复核状态，不重发模型或业务工具。"""
        with self.transaction() as cursor:
            conversation = self._conversation(cursor, user_id, conversation_id, lock=True)
            cursor.execute("""SELECT *, updated_at <= NOW()-INTERVAL '15 minutes' AS timed_out
                FROM portal_turns WHERE id=%s AND conversation_id=%s FOR UPDATE""",
                (turn_id, conversation_id))
            row = cursor.fetchone()
            if row is None:
                raise PortalNotFoundError("消息不存在或不属于当前会话")
            if row["kind"] != "vision":
                raise PortalConflictError("业务任务须核实原检查点，不能按图片任务终止")
            if row["status"] != "RUNNING":
                return public_turn({k: v for k, v in row.items() if k != "timed_out"})
            if not row["timed_out"] or str(conversation["active_turn_id"]) != turn_id:
                raise PortalConflictError("图片任务仍可能执行，请等待后同步")
            result = {"status": "FAILED", "kind": "vision", "mode": row["mode"],
                      "answer": "图片任务超时或中断，本轮未取得结果；未执行业务写入，未自动重发模型。",
                      "business_write_executed": False}
            cursor.execute("""UPDATE portal_turns SET status='FAILED',result=%s,
                error_code='VISION_INTERRUPTED',updated_at=NOW()
                WHERE id=%s AND conversation_id=%s RETURNING *""",
                (Jsonb(result), turn_id, conversation_id))
            recovered = cursor.fetchone()
            cursor.execute("""UPDATE portal_conversations SET active_turn_id=NULL,updated_at=NOW()
                WHERE id=%s AND active_turn_id=%s""", (conversation_id, turn_id))
            return public_turn(recovered)

    def finish_turn(self, user_id: str, conversation_id: str, turn_id: str,
                    result: dict | None, status: str, error_code: str | None = None,
                    *, expected_snapshot: tuple | None = None) -> dict:
        with self.transaction() as cursor:
            conversation = self._conversation(cursor, user_id, conversation_id, lock=True)
            cursor.execute("SELECT * FROM portal_turns WHERE id=%s AND conversation_id=%s FOR UPDATE",
                           (turn_id, conversation_id))
            current = cursor.fetchone()
            if current is None:
                raise PortalNotFoundError("消息不存在")
            if expected_snapshot is not None:
                updated_at, active_turn_id = expected_snapshot
                if (current["updated_at"] != updated_at or
                        str(conversation["active_turn_id"]) != str(active_turn_id)):
                    raise PortalConflictError("任务状态已变化，请重新读取进度；未覆盖正在执行的操作")
            if current["kind"] == "vision" and current.get("error_code") == "VISION_INTERRUPTED":
                return public_turn(current)  # 丢弃旧请求的迟到回包，不覆盖已终止状态。
            cursor.execute("""UPDATE portal_turns SET result=%s,status=%s,error_code=%s,updated_at=NOW()
                WHERE id=%s AND conversation_id=%s RETURNING *""",
                (Jsonb(result) if result is not None else None, status, error_code, turn_id, conversation_id))
            row = cursor.fetchone()
            if row is None:
                raise PortalNotFoundError("消息不存在")
            cursor.execute("""UPDATE portal_conversations SET active_turn_id=NULL,updated_at=NOW()
                WHERE id=%s AND active_turn_id=%s""", (conversation_id, turn_id))
            return public_turn(row)


@lru_cache(maxsize=1)
def portal_store() -> PostgresPortalStore:
    # 延迟连接，导入应用和离线测试不会隐式连接数据库或执行 DDL。
    return PostgresPortalStore()
