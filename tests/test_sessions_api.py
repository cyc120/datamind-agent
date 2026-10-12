"""聊天会话 HTTP 契约：会话是既有 `runs` 行的命名分组，正文一律由 runs 推导。

契约与防回归意图（见 db/migrations/020_chat_sessions.sql、datamind/sessions.py、datamind/api.py）：

* **没有消息表**：detail 的 messages/turns 必须来自 `runs WHERE conversation_id=session_id`
  并按 started_at 排序，所以“插一条 run 就能看到那一轮”是要被真实验证的行为，而不是文档承诺；
* **DELETE 是软删除**：行必须仍在库里且 `deleted_at IS NOT NULL`。这是本文件最要紧的防回归点——
  物理删除会连带毁掉 runs → trace_events/claims 的证据链，而会话只是 runs 的命名分组，
  没有资格毁掉别的东西；
* **归属隔离**：别人的会话必须与“不存在”返回同一个 404 SESSION_NOT_FOUND，且不出现在列表里。
  测试不能靠“列表恰好为空”蒙混过关，必须先证明双方各自的列表都是活的；
* **标题与搜索**：空白/超长标题要稳定错误码；ILIKE 里的字面量 `%` / `_` 必须转义，
  否则 `100%` 会命中 `100X`、`A_B` 会命中 `AXB`；
* **默认拒绝**：/v1/sessions 不在任何 PUBLIC_* 名单里，匿名请求由**中间件在路由之前**拒绝
  （401 LOGIN_REQUIRED）；已登录用户不分角色，只管理自己的会话；聊天正文永不缓存。

每个测试只创建自己的 disposable 账号、会话行与 run 行；tearDown 按 session_id / run_id
精确删除后再 auth_fixtures.cleanup，因此可重复运行且不动 demo 数据。全程不调用真实模型
（会话归属的端到端用例注入固定 SQL 生成器）。
"""
import hashlib
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import psycopg2
from psycopg2.extras import Json

import auth_fixtures
from datamind import sessions as service
from datamind.agent import DataMindAgent, _read_dsn, _write_dsn
from datamind.api import app


#: 固定 SQL 生成器：只证明“run 会带上 conversation_id 并触发自动命名”，不调用真实模型。
GOLD_SQL = "SELECT count(*) FROM orders WHERE created_at >= '2024-01-01' AND created_at < '2025-01-01'"
ORDER_QUESTION = "2024年一共有多少笔订单？"


def three_arg_sql_generator(question, catalog, feedback):
    """与多轮之前完全相同的三参数签名：多一个参数就该炸，钉住调用签名。"""
    return {"sql": GOLD_SQL, "interpretation": "会话归属桩"}


class ChatSessionAPITest(unittest.TestCase):
    def setUp(self):
        self.dsn = _write_dsn()
        self.session_ids = []
        self.run_ids = []
        self.clients = []
        # 角色与会话语义无关：这里只是挑一个确定的角色建 fixture 账号，
        # 归属隔离用例的两条账号必须同角色，才能证明「看不到」是因为 owner 不匹配、
        # 而不是因为角色不同被拒。「任意已登录用户都放行」由
        # test_non_viewer_accounts_are_not_role_gated 单独钉住。
        self.owner = self.new_client(roles=("viewer",), name="会话所有者")
        self.other = self.new_client(roles=("viewer",), name="会话越权者")

    def tearDown(self):
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            if self.run_ids:
                # evidence_* 没有指向 runs 的 FK，必须先删；runs 的删除会级联 trace_events/claims。
                cur.execute("DELETE FROM evidence_edges WHERE run_id=ANY(%s)", (self.run_ids,))
                cur.execute("DELETE FROM evidence_nodes WHERE run_id=ANY(%s)", (self.run_ids,))
                cur.execute("DELETE FROM claims WHERE run_id=ANY(%s)", (self.run_ids,))
                cur.execute("DELETE FROM runs WHERE run_id=ANY(%s)", (self.run_ids,))
            if self.session_ids:
                cur.execute("DELETE FROM chat_sessions WHERE session_id=ANY(%s)", (self.session_ids,))
        for client in self.clients:
            auth_fixtures.cleanup(client)
            client.close()

    def new_client(self, roles=("viewer",), name="会话隔离测试"):
        client = TestClient(app)
        self.clients.append(client)
        auth_fixtures.login(client, roles, name=name)
        return client

    def identity(self, client):
        """服务层直调时用的身份（与中间件构造的形状一致）。"""
        return {"user_id": client._fixture_users[0],
                "_session_hash": hashlib.sha256(client.cookies.get("datamind_session").encode()).hexdigest()}

    def assert_error(self, response, status, code=None):
        self.assertEqual(response.status_code, status, response.text)
        if code is not None:
            self.assertEqual(response.json()["detail"]["error_code"], code)

    def assert_private(self, response):
        """聊天正文是隐私：永不缓存，也绝不回显会话材料。"""
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("csrf_token", response.text)
        self.assertNotIn("_session_hash", response.text)

    def create(self, title=None, client=None):
        body = {} if title is None else {"title": title}
        response = (client or self.owner).post("/v1/sessions", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_private(response)
        session = response.json()["session"]
        if session["session_id"] not in self.session_ids:
            self.session_ids.append(session["session_id"])
        return session

    def insert_run(self, session_id, question, answer=None, *, started_at=None, status="COMPLETED"):
        """直接插 run 行：会话 transcript 的唯一数据来源就是这张表。"""
        run_id = "run-" + uuid.uuid4().hex[:16]
        self.run_ids.append(run_id)
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO runs(run_id,conversation_id,question,status,answer,
                confidence_level,plan,versions,started_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (run_id, session_id, question, status, answer, "high", Json({}), Json({}),
                         started_at or datetime.now(timezone.utc)))
        return run_id

    def listing(self, client=None, **params):
        response = (client or self.owner).get("/v1/sessions", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def ids(self, client=None, **params):
        return [item["session_id"] for item in self.listing(client, **params)["sessions"]]

    def detail(self, session_id, client=None):
        return (client or self.owner).get("/v1/sessions/" + session_id)

    # ---------- 1. 默认拒绝 ----------

    def test_session_routes_reject_anonymous_before_routing(self):
        anonymous = TestClient(app)
        self.clients.append(anonymous)
        target = "/v1/sessions/" + "sess-" + "0" * 24
        for response in (anonymous.get("/v1/sessions"),
                         anonymous.post("/v1/sessions", json={}),
                         anonymous.get(target),
                         anonymous.patch(target, json={"title": "越权改名"}),
                         anonymous.delete(target)):
            self.assert_error(response, 401, "LOGIN_REQUIRED")
            self.assertEqual(response.json(), {"detail": {"error_code": "LOGIN_REQUIRED"}})
            self.assert_private(response)
        # 这条路径根本没有注册路由：401 只能来自中间件（先于路由的默认拒绝）。
        # 如果哪次改动把 /v1/sessions 挪出默认拒绝，这里先变成 404，正是我们要看到的报警。
        self.assert_error(anonymous.get("/v1/sessions/no/such/route"), 401, "LOGIN_REQUIRED")

    def test_non_viewer_accounts_are_not_role_gated(self):
        """契约「角色不限（任意已登录用户）」的钉子（docs/SESSIONS.md §1）。

        历史缺陷（本轮已修）：写事务曾用 ``lock_identity(cur, identity, 'viewer')``，
        而 ``lock_identity`` 在 datamind/auth.py 是**精确角色成员匹配**而不是层级判定，
        于是 roles=('reviewer',) / ('approver',) 的账号会在 POST/PATCH/DELETE 上得到
        403 PERMISSION_DENIED；auth_fixtures.login 的默认角色恰好就是 reviewer，
        所以这条缺陷很容易被“测试自己带了 viewer”掩盖过去。

        这里断言的是**放行**，而且要求建/列/改/删完整走通：把契约钉死，
        而不是把当时的错误行为固化成“也算通过”。谁把角色门加回来，这条就会红。
        """
        for roles in (("reviewer",), ("approver",), ("viewer", "reviewer")):
            client = self.new_client(roles=roles, name="非 viewer 账号")
            created = client.post("/v1/sessions", json={"title": "非 viewer 账号的会话"})
            self.assertEqual(created.status_code, 200,
                             f"roles={roles} 被角色门挡下（任意已登录用户都应放行）：{created.text}")
            session_id = created.json()["session"]["session_id"]
            self.session_ids.append(session_id)
            self.assertEqual(self.ids(client), [session_id])
            renamed = client.patch("/v1/sessions/" + session_id, json={"title": "改个名字"})
            self.assertEqual(renamed.status_code, 200, renamed.text)
            self.assertEqual(renamed.json()["session"]["title"], "改个名字")
            self.assertEqual(client.delete("/v1/sessions/" + session_id).status_code, 200)

    # ---------- 2. 建/列/详情往返 ----------

    def test_create_list_detail_round_trip(self):
        session = self.create(title="季度复盘")
        # 只暴露白名单列：user_id / deleted_at 绝不能出现在响应里（归属与软删除都是服务端的事）。
        self.assertEqual(set(session), {"session_id", "title", "created_at", "updated_at"})
        self.assertEqual(session["title"], "季度复盘")
        self.assertTrue(session["session_id"].startswith("sess-"))
        self.assertEqual(len(session["session_id"]), 5 + 24)

        page = self.listing()
        self.assertEqual((page["limit"], page["offset"], page["has_more"]), (20, 0, False))
        self.assertEqual([item["session_id"] for item in page["sessions"]], [session["session_id"]])
        self.assertEqual(page["sessions"][0]["run_count"], 0)
        self.assertIsNone(page["sessions"][0]["last_question"])

        detail = self.detail(session["session_id"])
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assert_private(detail)
        self.assertEqual(detail.json()["session"], session)
        self.assertEqual(detail.json()["messages"], [])
        self.assertEqual(detail.json()["turns"], [])

    def test_create_uses_the_default_title_and_rejects_blank_or_spoofed_fields(self):
        session = self.create()
        self.assertEqual(session["title"], service.DEFAULT_TITLE)
        # 显式空白是“非法”，与“省略标题”必须区分开：前者 422，后者用服务端默认标题。
        for title in ("   ", "\t\n"):
            self.assert_error(self.owner.post("/v1/sessions", json={"title": title}),
                              422, "SESSION_TITLE_REQUIRED")
        self.assertEqual(self.owner.post("/v1/sessions", json={"title": ""}).status_code, 422)
        # extra=forbid：客户端不能自带归属或软删除字段来伪造服务端状态。
        for body in ({"user_id": "spoof"}, {"session_id": "sess-" + "0" * 24}, {"title": 123}):
            self.assertEqual(self.owner.post("/v1/sessions", json=body).status_code, 422, body)

    # ---------- 3. 重命名与标题边界 ----------

    def test_rename_updates_title_and_enforces_the_title_contract(self):
        session = self.create(title="原始标题")
        session_id = session["session_id"]
        renamed = self.owner.patch("/v1/sessions/" + session_id, json={"title": "  新标题  "})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assert_private(renamed)
        self.assertEqual(renamed.json()["session"]["title"], "新标题")  # strip 后入库
        self.assertEqual(self.detail(session_id).json()["session"]["title"], "新标题")

        self.assertEqual(self.owner.patch("/v1/sessions/" + session_id, json={"title": ""}).status_code, 422)
        for title in ("   ", "\t\n"):
            self.assert_error(self.owner.patch("/v1/sessions/" + session_id, json={"title": title}),
                              422, "SESSION_TITLE_REQUIRED")
        for body in ({}, {"title": None}, {"title": "x" * 201}, {"name": "spoof"}):
            self.assertEqual(self.owner.patch("/v1/sessions/" + session_id, json=body).status_code, 422, body)

        # 200 字符是 DDL CHECK (length(title)<=200) 的上限，恰好 200 必须可写。
        boundary = "边" * service.TITLE_MAX
        self.assertEqual(self.owner.patch("/v1/sessions/" + session_id,
                                          json={"title": boundary}).status_code, 200)
        # 201 字符走 HTTP 时被 pydantic max_length 先拦（框架 422，detail 是列表），
        # 服务层直调才是 SESSION_TITLE_TOO_LONG。两条路都不能落库。
        over = self.owner.patch("/v1/sessions/" + session_id, json={"title": "超" * (service.TITLE_MAX + 1)})
        self.assertEqual(over.status_code, 422, over.text)
        with self.assertRaises(HTTPException) as error:
            service.rename(session_id, {"title": "超" * (service.TITLE_MAX + 1)}, self.dsn,
                           self.identity(self.owner))
        self.assertEqual((error.exception.status_code, error.exception.detail["error_code"]),
                         (422, "SESSION_TITLE_TOO_LONG"))
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT title FROM chat_sessions WHERE session_id=%s", (session_id,))
            self.assertEqual(cur.fetchone()[0], boundary)  # 被拒的重命名没有改动任何一行

    # ---------- 4. 软删除 ----------

    def test_delete_is_soft_and_keeps_the_linked_audit_chain(self):
        session = self.create(title="待删除会话")
        session_id = session["session_id"]
        run_id = self.insert_run(session_id, "这条 run 的证据链不能被删会话毁掉", answer="不会")
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO trace_events(event_id,run_id,event_type,status) "
                        "VALUES (%s,%s,'run.started','succeeded')",
                        ("evt-" + uuid.uuid4().hex[:16], run_id))

        removed = self.owner.delete("/v1/sessions/" + session_id)
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json()["session"]["session_id"], session_id)
        # 列表与详情都必须当它不存在；再删一次也是 404，不能出现“第二次成功”。
        self.assertNotIn(session_id, self.ids())
        self.assert_error(self.detail(session_id), 404, "SESSION_NOT_FOUND")
        self.assert_error(self.owner.patch("/v1/sessions/" + session_id, json={"title": "复活"}),
                          404, "SESSION_NOT_FOUND")
        self.assert_error(self.owner.delete("/v1/sessions/" + session_id), 404, "SESSION_NOT_FOUND")

        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            # 回归核心：行必须还在，只是被打了删除戳。硬删除会让 fetchone() 变成 None。
            cur.execute("SELECT deleted_at, created_at, title FROM chat_sessions WHERE session_id=%s",
                        (session_id,))
            row = cur.fetchone()
            self.assertIsNotNone(row, "软删除后 chat_sessions 行必须仍然存在")
            self.assertIsNotNone(row[0], "deleted_at 必须被写入")
            self.assertGreaterEqual(row[0], row[1])
            self.assertEqual(row[2], "待删除会话")
            # 证据链原封不动：run 仍指向该会话，trace 行仍在。
            cur.execute("SELECT conversation_id FROM runs WHERE run_id=%s", (run_id,))
            self.assertEqual(cur.fetchone()[0], session_id)
            cur.execute("SELECT count(*) FROM trace_events WHERE run_id=%s", (run_id,))
            self.assertEqual(cur.fetchone()[0], 1)

    # ---------- 5. 归属隔离（最重要） ----------

    def test_foreign_sessions_are_invisible_and_undeletable(self):
        mine = self.create(title="甲的会话")
        theirs = self.create(title="乙的会话", client=self.other)
        # 先证明乙的列表是活的：不能只断言“空”就自以为隔离生效了。
        self.assertEqual(self.ids(self.other), [theirs["session_id"]])
        self.assertNotIn(mine["session_id"], self.ids(self.other))
        # 越权读/改/删一律与“不存在”同码，隐藏会话是否存在。
        for response in (self.detail(mine["session_id"], client=self.other),
                         self.other.patch("/v1/sessions/" + mine["session_id"], json={"title": "越权改名"}),
                         self.other.delete("/v1/sessions/" + mine["session_id"])):
            self.assert_error(response, 404, "SESSION_NOT_FOUND")
        # 越权 DELETE 不能真的删掉甲的行，甲必须始终看得见自己的会话。
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT user_id, deleted_at, title FROM chat_sessions WHERE session_id=%s",
                        (mine["session_id"],))
            user_id, deleted_at, title = cur.fetchone()
        self.assertEqual(user_id, self.owner._fixture_users[0])
        self.assertIsNone(deleted_at)
        self.assertEqual(title, "甲的会话")
        self.assertIn(mine["session_id"], self.ids())
        self.assertEqual(self.detail(mine["session_id"]).json()["session"]["title"], "甲的会话")
        # 反向同样成立：搜索也不能把别人的会话捞出来。
        self.assert_error(self.detail(theirs["session_id"]), 404, "SESSION_NOT_FOUND")
        self.assertNotIn(theirs["session_id"], self.ids())
        self.assertEqual(self.ids(client=self.owner, q="乙的"), [])
        self.assertEqual(self.ids(client=self.other, q="甲的"), [])

    # ---------- 6. 搜索与通配符转义 ----------

    def test_search_matches_title_and_run_text_and_escapes_wildcards(self):
        percent = self.create(title="折扣100%达成")
        other_percent = self.create(title="折扣100X达成")
        underscore = self.create(title="进度A_B记录")
        other_underscore = self.create(title="进度AXB记录")
        named = self.create(title="风机振动排查")
        by_question = self.create()
        by_question_run = self.insert_run(by_question["session_id"], "齿轮箱油温异常的退款流程是什么",
                                          answer="走线下审批")
        by_answer = self.create()
        by_answer_run = self.insert_run(by_answer["session_id"], "与关键词无关的问题",
                                        answer="季节性波动明显")

        self.assertEqual(self.ids(q="风机"), [named["session_id"]])
        self.assertIn(by_question_run, [m["run_id"] for m in
                                        self.detail(by_question["session_id"]).json()["messages"]])
        self.assertEqual(self.ids(q="退款流程"), [by_question["session_id"]])   # 命中 run.question
        self.assertEqual(self.ids(q="季节性波动"), [by_answer["session_id"]])   # 命中 run.answer
        # 字面量 % / _ 绝不能当通配符：不转义时 '100%' 会连 ...100X... 一起命中，
        # 'A_B' 会连 AXB 一起命中——这两条正是转义写错的典型症状。
        self.assertEqual(self.ids(q="100%"), [percent["session_id"]])
        self.assertEqual(self.ids(q="A_B"), [underscore["session_id"]])
        # 空白 q 等于“没有过滤”；没命中的词返回空而不是全量。
        self.assertEqual(set(self.ids(q="   ")),
                         {percent["session_id"], other_percent["session_id"], underscore["session_id"],
                          other_underscore["session_id"], named["session_id"], by_question["session_id"],
                          by_answer["session_id"]})
        self.assertEqual(self.ids(q="不存在的关键词"), [])
        # q 超过服务层上限时给出稳定错误码（HTTP 层没有 max_length，必须由服务层兜住）。
        self.assert_error(self.owner.get("/v1/sessions", params={"q": "x" * (service.QUERY_MAX + 1)}),
                          422, "SESSION_QUERY_TOO_LONG")
        self.assertEqual(by_answer_run, self.detail(by_answer["session_id"]).json()["messages"][0]["run_id"])

    # ---------- 7. 分页 ----------

    def test_pagination_bounds_and_has_more(self):
        created = [self.create(title="分页-%d" % index) for index in range(3)]
        ordered = [item["session_id"] for item in created]
        first = self.listing(limit=2)
        # 最近活动优先：三个会话按创建顺序落库，最新的一页应是 [第 3 个, 第 2 个]。
        self.assertEqual((first["limit"], first["offset"], first["has_more"]), (2, 0, True))
        self.assertEqual([item["session_id"] for item in first["sessions"]], [ordered[2], ordered[1]])
        second = self.listing(limit=2, offset=2)
        self.assertEqual((second["has_more"], [item["session_id"] for item in second["sessions"]]),
                         (False, [ordered[0]]))
        # 偏移越过末尾：空列表 + has_more False（这是正常空页，不是错误）。
        for offset in (3, service.OFFSET_MAX):
            tail = self.listing(limit=20, offset=offset)
            self.assertEqual((tail["sessions"], tail["has_more"]), ([], False))
        # Query 边界由框架拦截：limit=0/999 不能悄悄退化成默认值。
        for params in ({"limit": 0}, {"limit": 999}, {"limit": service.LIMIT_MAX + 1},
                       {"offset": -1}, {"offset": service.OFFSET_MAX + 1}):
            self.assertEqual(self.owner.get("/v1/sessions", params=params).status_code, 422, params)

    # ---------- 8. run 归属与自动命名 ----------

    def test_linked_run_is_the_transcript_and_the_first_question_names_the_session(self):
        session = self.create()
        session_id = session["session_id"]
        self.assertEqual(session["title"], service.DEFAULT_TITLE)
        later = self.insert_run(session_id, "第二轮问题", answer="第二轮答案")
        earlier = self.insert_run(session_id, "第一轮问题", answer="第一轮答案",
                                  started_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        # 另一条 run 指向别的会话：出现在这里就是串会话。
        foreign = self.insert_run("sess-" + uuid.uuid4().hex[:24], "别人的问题", answer="别人的答案")

        body = self.detail(session_id).json()
        # question=用户轮、answer=助手轮，按 started_at 升序——我们故意让后插的那条更早。
        self.assertEqual([m["run_id"] for m in body["messages"]], [earlier, later])
        self.assertEqual([m["question"] for m in body["messages"]], ["第一轮问题", "第二轮问题"])
        self.assertEqual([m["answer"] for m in body["messages"]], ["第一轮答案", "第二轮答案"])
        self.assertNotIn(foreign, [m["run_id"] for m in body["messages"]])
        self.assertEqual([turn["role"] for turn in body["turns"]],
                         ["user", "assistant", "user", "assistant"])
        self.assertEqual([turn["text"] for turn in body["turns"]],
                         ["第一轮问题", "第一轮答案", "第二轮问题", "第二轮答案"])
        # 仅插入 run 不会改标题：真正触发自动命名的是 agent.run 里的 touch（见下一个用例）。
        self.assertEqual(body["session"]["title"], service.DEFAULT_TITLE)

        # 自动命名：去掉首尾空白、折叠内部空白、截到 40 字（不补省略号）。
        service.touch(session_id, "  第一轮   问题\n带空白  ", self.dsn)
        self.assertEqual(self.detail(session_id).json()["session"]["title"], "第一轮 问题 带空白")
        # 已经是自定义标题就不再被后续问题覆盖。
        service.touch(session_id, "第二轮问题", self.dsn)
        self.assertEqual(self.detail(session_id).json()["session"]["title"], "第一轮 问题 带空白")
        # 问题折叠后为空时回落到既有标题（不能把标题清空）。
        service.touch(session_id, " \t\n ", self.dsn)
        self.assertEqual(self.detail(session_id).json()["session"]["title"], "第一轮 问题 带空白")

    def test_auto_title_truncates_to_forty_characters_and_touch_tolerates_gone_sessions(self):
        session = self.create()
        session_id = session["session_id"]
        question = "很长的首轮问题。" * 10
        service.touch(session_id, question, self.dsn)
        title = self.detail(session_id).json()["session"]["title"]
        self.assertEqual(title, question[:service.AUTO_TITLE_LIMIT])
        self.assertEqual(len(title), service.AUTO_TITLE_LIMIT)
        self.assertNotIn("…", title)  # 不补省略号：长度与 DDL 的 200 上限都可预期
        self.assertNotIn("...", title)

        # 对不存在的会话 touch 必须静默（best-effort），不能把用户已经拿到的答案搞崩。
        service.touch("sess-" + uuid.uuid4().hex[:24], "问题", self.dsn)
        # 对已软删除的会话 touch 不能“复活”它，也不能改标题。
        removed = self.create(title="将被删除")
        self.owner.delete("/v1/sessions/" + removed["session_id"])
        service.touch(removed["session_id"], "删除后的问题", self.dsn)
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT title, deleted_at FROM chat_sessions WHERE session_id=%s",
                        (removed["session_id"],))
            title, deleted_at = cur.fetchone()
        self.assertEqual(title, "将被删除")
        self.assertIsNotNone(deleted_at)

    # ---------- 9. 未知会话 ----------

    def test_unknown_session_ids_are_not_found_for_every_verb(self):
        for session_id in ("sess-" + "0" * 24, "sess-" + uuid.uuid4().hex[:24], "not-a-session", "sess-"):
            with self.subTest(session_id=session_id):
                self.assert_error(self.detail(session_id), 404, "SESSION_NOT_FOUND")
                self.assert_error(self.owner.patch("/v1/sessions/" + session_id, json={"title": "x"}),
                                  404, "SESSION_NOT_FOUND")
                self.assert_error(self.owner.delete("/v1/sessions/" + session_id),
                                  404, "SESSION_NOT_FOUND")

    # ---------- 10. 与 /v1/chat 的接线 ----------

    def test_agent_run_stamps_conversation_id_and_auto_names_the_session(self):
        """端到端：agent.run(conversation_id=...) 写 runs.conversation_id 并触发 touch。

        生成器是固定桩，不碰真实模型；这条路正是 /v1/chat 携带 conversation_id 时走的。
        """
        session = self.create()
        session_id = session["session_id"]
        agent = DataMindAgent()
        self.run_ids.append(agent.run_id)
        try:
            agent.run(ORDER_QUESTION, database_sources=["demo_retail"],
                      generator=three_arg_sql_generator, conversation_id=session_id)
        finally:
            agent.close()
        with psycopg2.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT conversation_id, question FROM runs WHERE run_id=%s", (agent.run_id,))
            row = cur.fetchone()
        self.assertEqual(row, (session_id, ORDER_QUESTION), "run 必须通过 conversation_id 入组")
        detail = self.detail(session_id).json()
        self.assertEqual([m["run_id"] for m in detail["messages"]], [agent.run_id])
        self.assertEqual(detail["session"]["title"], ORDER_QUESTION)  # 默认标题 -> 首问

    def test_chat_passes_conversation_id_only_when_supplied(self):
        session = self.create()
        with patch("datamind.api.DataMindAgent") as factory:
            factory.return_value.run.return_value = {"status": "COMPLETED", "answer": "ok", "turns_used": 0}
            plain = self.owner.post("/v1/chat", json={"question": "不带会话的问题"})
            self.assertEqual(plain.status_code, 200, plain.text)
            # 没有会话时该 kwarg 必须“完全不存在”，而不是传 None：否则无会话调用的调用签名、
            # 乃至 test_multi_turn 冻结的提示词哈希都会变。
            self.assertNotIn("conversation_id", factory.return_value.run.call_args.kwargs)
            linked = self.owner.post("/v1/chat", json={"question": "带会话的问题",
                                                       "conversation_id": session["session_id"]})
            self.assertEqual(linked.status_code, 200, linked.text)
            self.assertEqual(factory.return_value.run.call_args.kwargs["conversation_id"],
                             session["session_id"])
            # 非法会话号在路由层就被拒（不允许任意字符串冒充会话归属）。
            for bad in ("not-a-session", "sess-" + "A" * 24, "sess-" + "0" * 23):
                self.assertEqual(self.owner.post("/v1/chat", json={"question": "坏会话号",
                                                                   "conversation_id": bad}).status_code,
                                 422, bad)

    # ---------- 11. 隐私：只读角色看不到 chat_sessions ----------

    def test_chat_sessions_are_not_readable_by_the_readonly_role(self):
        self.create(title="私有会话")
        connection = psycopg2.connect(_read_dsn())
        try:
            with connection.cursor() as cur:
                with self.assertRaises(psycopg2.errors.InsufficientPrivilege):
                    cur.execute("SELECT session_id FROM chat_sessions LIMIT 1")
            connection.rollback()
        finally:
            connection.close()

    # ---------- 12. 畸形输入：NUL 绝不能变成 500 ----------

    def test_nul_bytes_get_stable_codes_instead_of_500(self):
        """`\\x00` 是 psycopg2 的非法文本参数，泄漏到 SQL 参数就是 500（修复前六条路径全 500）。

        400 类错误必须回落到稳定的 SESSION_* 码：搜索词/标题是 422；路径里的 session_id 是
        404——含 NUL 的 id 不可能对应真实行，与「越权 = 不存在」同一套隐藏语义。
        """
        session_id = self.create(title="NUL 边界")["session_id"]
        cases = ((self.owner.get("/v1/sessions", params={"q": "a\x00b"}),
                  422, "SESSION_QUERY_INVALID"),
                 (self.owner.post("/v1/sessions", json={"title": "a\x00b"}),
                  422, "SESSION_TITLE_REQUIRED"),
                 (self.owner.patch("/v1/sessions/" + session_id, json={"title": "a\x00b"}),
                  422, "SESSION_TITLE_REQUIRED"),
                 (self.owner.get("/v1/sessions/sess-%00abc"), 404, "SESSION_NOT_FOUND"),
                 (self.owner.patch("/v1/sessions/sess-%00abc", json={"title": "x"}),
                  404, "SESSION_NOT_FOUND"),
                 (self.owner.delete("/v1/sessions/sess-%00abc"), 404, "SESSION_NOT_FOUND"))
        for response, status, code in cases:
            with self.subTest(status=status, code=code):
                self.assert_error(response, status, code)


if __name__ == "__main__":
    unittest.main()
