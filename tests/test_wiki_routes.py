"""Wiki P3 收官：`datamind/api.py` 的 14 条 `/v1/wiki/*` 路由接线（契约 `docs/WIKI_API.md` §2/§3）。

**为什么是静态断言，不用 `TestClient`（本仓库实测）**：系统 python（`C:\\Python314`，
验证命令用的 `python -m pytest`）有 pytest + fastapi，但**没有 psycopg2**
（`datamind/api.py:13` 顶层 `import psycopg2`，所以 `import datamind.api` 直接失败），
也**没有 httpx**（`starlette.testclient` 需要它）。因此 `tests/test_route_auth_coverage.py`
在这个解释器里连收集都过不去，任何依赖 `datamind.api` 导入或用例的写法都会跟着失效。

本文件的做法：**把 `api.py` 里真实的鉴权面源码抽出来执行**（`ast` 定位 → `exec` 到独立
命名空间），路由表与处理器体也从源码解析。断言的对象因此仍是 `api.py` 里那段代码本身，
而不是本文件里的副本；在能 import `datamind.api` 的解释器（`.venv`）里另有一组
"抽取结果 == 真身"的交叉核对，防止抽取漂移。

覆盖的四类不变式（前三类就是本次接线的四个坑）：

1. **权限逐条显式**：8 条读 + #10 按契约 §2 的论证表返回 `None`（= 任意已登录身份，
   "读接口不做角色门"，先例 `/v1/sessions`）；**5 条写路由必须逐条列名**为
   `reviewer` / `approver`——漏一条不是误拒而是**越权**（落到 `None` = 默认放开），
   所以除了正向断言还有一条反证：清空显式表后写路由只能落到默认拒绝，绝不落到 `None`；
2. `/v1/wiki` **不进**任何匿名白名单（契约 §2.2：首次实现不加匿名读取例外），
   且恒 `no-store`（§2.3）；
3. **读也必须用 `_write_dsn()`**：`021:605-607` 已把 8 张 `wiki_*` 表从 `readonly_user`
   REVOKE（实测 42501），传 `_read_dsn()` 会让这 14 条全部 500；
4. `reviewer` 与 `approver` **互不继承**（`auth.py:195` 只给 `admin` 旁路），带反例。

不需要数据库：全部断言都是静态/纯逻辑，8 张 wiki 表不会被触碰。
"""
import ast
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from datamind import wiki, wiki_jobs, wiki_query  # noqa: E402  纯逻辑不需要 psycopg2

API_PATH = ROOT / 'datamind' / 'api.py'
AUTH_PATH = ROOT / 'datamind' / 'auth.py'
API_SOURCE = API_PATH.read_text(encoding='utf-8')
AUTH_SOURCE = AUTH_PATH.read_text(encoding='utf-8')

#: `api.py` 里 wiki 区块的起始注释，用来把"路由接线区"从整份文件里切出来。
WIKI_BLOCK_MARKER = 'wiki knowledge graph'

#: 契约 §2 的 14 条路由**逐条**冻结：
#: `(编号, 方法, 路径, `_required_permission` 的返回值, 服务层模块, 服务层函数)`。
#: 读接口的 `None` 是契约 §3.1 矩阵的**显式决定**（"读接口只要求身份，不做角色门"），
#: 不是"漏了规则"：它们仍必须过中间件的身份关（见下面 `_is_public_request` 的断言）。
WIKI_ROUTES = (
    ('1', 'GET', '/v1/wiki/entries', None, 'wiki_query', 'entry_catalogue'),
    ('2', 'GET', '/v1/wiki/spaces/{space_id}/graph', None, 'wiki_query', 'space_graph'),
    ('3', 'GET', '/v1/wiki/entries/{entry_id}', None, 'wiki', 'entry_detail'),
    ('4', 'GET', '/v1/wiki/entries/{entry_id}/revisions', None, 'wiki', 'revision_history'),
    ('5', 'GET', '/v1/wiki/entries/{entry_id}/revisions/{revision_id}', None, 'wiki',
     'revision_detail'),
    ('6', 'GET', '/v1/wiki/entries/{entry_id}/graph', None, 'wiki_query', 'entry_neighborhood'),
    ('7', 'GET', '/v1/wiki/entries/{entry_id}/provenance', None, 'wiki_query', 'entry_provenance'),
    ('8', 'GET', '/v1/wiki/entries/{entry_id}/export', None, 'wiki_query', 'entry_export'),
    ('9', 'POST', '/v1/wiki/builds', 'reviewer', 'wiki_jobs', 'submit_build'),
    ('10', 'GET', '/v1/wiki/builds/{build_id}', None, 'wiki_jobs', 'read_build'),
    ('11', 'POST', '/v1/wiki/builds/{build_id}/retry', 'reviewer', 'wiki_jobs', 'retry_build'),
    ('12', 'POST', '/v1/wiki/entries/{entry_id}/revisions', 'reviewer', 'wiki',
     'create_revision'),
    ('13', 'POST', '/v1/wiki/entries/{entry_id}/reviews', 'reviewer', 'wiki', 'submit_review'),
    ('14', 'POST', '/v1/wiki/entries/{entry_id}/publications', 'approver', 'wiki', 'publish'),
)

WIKI_WRITE_ROUTES = tuple(route for route in WIKI_ROUTES if route[1] == 'POST')
SERVICE_MODULES = {'wiki': wiki, 'wiki_jobs': wiki_jobs, 'wiki_query': wiki_query}

#: `api.py` 里必须被抽出来执行的鉴权面常量与函数（抽不到就是被改名/删除了，测试要红）。
_SURFACE_CONSTANTS = ('PUBLIC_PATHS', 'PUBLIC_READ_PREFIXES', 'PUBLIC_WRITE_PATHS',
                      'SENSITIVE_READ_PREFIXES', '_NO_STORE_PREFIXES', 'WIKI_WRITE_PERMISSIONS')
_SURFACE_FUNCTIONS = ('_public_chat_enabled', '_is_public_write', '_is_public_request',
                      '_no_store_path', '_wiki_write_permission', '_required_permission')


class _Request:
    """`_is_public_request` / `_required_permission` 需要的最小请求替身（只有路径与方法）。"""

    def __init__(self, method, path, client=('203.0.113.9', 41800)):
        self.method = method
        self.url = type('_Url', (), {'path': path})()
        self.headers = {}
        self.scope = {'type': 'http', 'method': method, 'path': path, 'client': client,
                      'headers': [], 'scheme': 'http', 'server': ('testserver', 80),
                      'query_string': b'', 'root_path': ''}


def _concrete(path):
    """把 `{param}` 换成字面量：中间件与 `_required_permission` 看到的是具体路径。"""
    return '/'.join('x' if part.startswith('{') else part for part in path.split('/'))


def _module_ast():
    return ast.parse(API_SOURCE)


def _surface():
    """把 `api.py` 的鉴权面（常量 + 函数）从**源码**里抽出来执行。

    每个用例都拿一份新命名空间，所以用例之间不会互相污染（例如反证里清空
    `WIKI_WRITE_PERMISSIONS` 只影响那一份）。
    """
    namespace = {'os': os}
    for node in _module_ast().body:
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            name = getattr(target, 'id', None)
            if name in _SURFACE_CONSTANTS:
                exec(ast.get_source_segment(API_SOURCE, node), namespace)  # noqa: S102
        elif isinstance(node, ast.FunctionDef) and node.name in _SURFACE_FUNCTIONS:
            exec(ast.get_source_segment(API_SOURCE, node), namespace)  # noqa: S102
    missing = [name for name in _SURFACE_CONSTANTS + _SURFACE_FUNCTIONS if name not in namespace]
    if missing:
        raise AssertionError('api.py 的鉴权面缺少：%s' % missing)
    return namespace


def _declared_routes():
    """`api.py` 里 `@app.get(...)` / `@app.post(...)` 声明的全部路由：`(方法, 路径, 处理器名)`。"""
    routes = []
    for node in _module_ast().body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not decorator.args:
                continue
            func = decorator.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id == 'app'):
                continue
            path = decorator.args[0]
            if isinstance(path, ast.Constant) and isinstance(path.value, str):
                routes.append((func.attr.upper(), path.value, node.name))
    return routes


def _route_handler(method, path):
    for route_method, route_path, handler in _declared_routes():
        if (route_method, route_path) == (method, path):
            return handler
    raise AssertionError('api.py 没有声明路由 %s %s' % (method, path))


def _handler_facts(handler):
    """处理器体的相对 import 集合与裸函数调用集合（`(模块, 名字)` / `名字`）。"""
    for node in _module_ast().body:
        if isinstance(node, ast.FunctionDef) and node.name == handler:
            imports, calls = set(), set()
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom) and child.level == 1 and child.module:
                    imports.update((child.module, alias.name) for alias in child.names)
                elif isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    calls.add(child.func.id)
            return imports, calls
    raise AssertionError('api.py 里没有处理器 %s' % handler)


def _handler_names(handler):
    """处理器体里出现过的**标识符**（`ast.Name`）：注释与字符串不算，只看代码。

    它比"调用了什么"更严：既拦 `_read_dsn()`，也拦 `_read_dsn` 被当值传来传去。
    """
    for node in _module_ast().body:
        if isinstance(node, ast.FunctionDef) and node.name == handler:
            return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
    raise AssertionError('api.py 里没有处理器 %s' % handler)


def _literal(source, name):
    """模块级字面量赋值（`ROLES = {...}` 这种），用 `ast.literal_eval` 取真值。"""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == name:
            return ast.literal_eval(node.value)
    raise AssertionError('没有模块级字面量 %s' % name)


def _source_line(source, needle):
    """含 `needle` 的那一行（压掉空白，便于逐字比对）。"""
    for line in source.splitlines():
        if needle in line:
            return re.sub(r'\s+', '', line)
    raise AssertionError('源码里没有这一行：%s' % needle)


class WikiRouteDeclarationsTest(unittest.TestCase):
    """契约 §2 路由总表：14 条，方法与路径一字不差，并且真的绑到了冻结的实现上。"""

    def test_api_declares_exactly_the_fourteen_contract_routes(self):
        declared = {(method, path) for method, path, _handler in _declared_routes()
                    if path.startswith('/v1/wiki')}
        expected = {(method, path) for _n, method, path, _p, _m, _f in WIKI_ROUTES}
        self.assertEqual(declared, expected)
        self.assertEqual(len(declared), 14)

    def test_every_route_is_bound_to_its_frozen_service_function(self):
        """每条路由都调用**它该调**的那个服务层函数（不做第二套实现）。"""
        for number, method, path, _permission, module_name, function_name in WIKI_ROUTES:
            with self.subTest(route=number, path=path):
                imports, calls = _handler_facts(_route_handler(method, path))
                self.assertIn((module_name, function_name), imports)
                self.assertIn(function_name, calls)
                self.assertTrue(callable(getattr(SERVICE_MODULES[module_name], function_name)),
                                '%s.%s 不存在' % (module_name, function_name))

    def test_the_wiki_routes_are_appended_as_one_self_contained_block(self):
        """改动范围：14 条落在 `api.py` 末尾的**一个**区块里，没有散进别人的路由之间。

        这是"纯增加、不重构"的可执行形态：区块里 `@app.` 装饰器恰好 14 个，而且
        `api.py` 从这个区块往后**只有**这 14 个处理器，没有任何别人的路由被挪动或改写。
        """
        marker_line = API_SOURCE[:API_SOURCE.index(WIKI_BLOCK_MARKER)].count('\n')
        block = API_SOURCE[API_SOURCE.index(WIKI_BLOCK_MARKER):]
        self.assertEqual(block.count('@app.'), 14)
        self.assertEqual(sorted(node.name for node in ast.parse(API_SOURCE).body
                                if isinstance(node, ast.FunctionDef)
                                and node.lineno > marker_line),
                         sorted(_route_handler(method, path)
                                for _n, method, path, *_ in WIKI_ROUTES))

    def test_reads_use_the_application_role_dsn_and_never_the_readonly_one(self):
        """坑①：`021:605-607` 把 8 张 `wiki_*` 表从 `readonly_user` REVOKE（实测 42501）。

        因此 14 条处理器**全部**要调 `_write_dsn()`，一条都不能出现 `_read_dsn()`
        （先例 `api.py:1218` 用 `_write_dsn()` 读 `chat_sessions`）。
        """
        for number, method, path, *_ in WIKI_ROUTES:
            handler = _route_handler(method, path)
            _imports, calls = _handler_facts(handler)
            with self.subTest(route=number, path=path):
                self.assertIn('_write_dsn', calls)
                self.assertNotIn('_read_dsn', calls)
                # 不只是"没被调用"：`_handler_names` 看的是处理器体的**全部标识符**，
                # 连"把 `_read_dsn` 当值传来传去"也拦；注释里提到它说明原因不算。
                self.assertNotIn('_read_dsn', _handler_names(handler))


class WikiPermissionMappingTest(unittest.TestCase):
    """坑②：`_required_permission` 是硬编码路径匹配表——漏一条 = 默认放开 = 越权。"""

    @classmethod
    def setUpClass(cls):
        cls.surface = _surface()

    def _permission(self, method, path):
        return self.surface['_required_permission'](_Request(method, _concrete(path)))

    def test_every_wiki_route_matches_the_contract_permission_table(self):
        """14 条逐条比对契约 §2 的"所需权限（`_required_permission` 返回值）"列。"""
        for number, method, path, permission, _module, _function in WIKI_ROUTES:
            with self.subTest(route=number, path=path):
                self.assertEqual(self._permission(method, path), permission,
                                 '#%s %s %s 的权限与契约 §2 不一致' % (number, method, path))

    def test_every_wiki_write_route_has_an_explicit_non_default_permission(self):
        """**最重要的一条**：5 条写路由都必须有显式要求，且来自那张逐条列名的表。

        `None` 在这里的含义是"任意已登录身份"。对写路由来说那就是默认放开，
        所以除了比对契约值，还要证明这个值**来自显式表**而不是别的路径恰好接住。
        """
        self.assertEqual([route[0] for route in WIKI_WRITE_ROUTES], ['9', '11', '12', '13', '14'])
        for number, method, path, permission, _module, _function in WIKI_WRITE_ROUTES:
            with self.subTest(route=number, path=path):
                concrete = _concrete(path)
                self.assertIsNotNone(self._permission(method, path),
                                     '#%s 落到 None（默认放开）' % number)
                self.assertIn(permission, ('reviewer', 'approver'))
                self.assertEqual(self.surface['_wiki_write_permission'](concrete), permission,
                                 '#%s 没有被逐条列名' % number)

    def test_publications_is_approver_and_never_default_open_or_reviewer(self):
        """#14 的专项断言：它必须以 `/publications` 结尾 → 既不命中 `/reviews`
        也不命中 `/approvals` 兜底，正是"落到 `None`"那个缺陷的现场。"""
        write = {route[0]: route for route in WIKI_WRITE_ROUTES}
        number, method, path, permission, _module, _function = write['14']
        self.assertEqual(permission, 'approver')
        self.assertEqual(self._permission(method, path), 'approver')
        self.assertIsNotNone(self._permission(method, path))
        self.assertNotEqual(self._permission(method, path), 'reviewer')
        self.assertTrue(path.endswith('/publications'))

    def test_the_generic_suffix_fallbacks_cannot_cover_the_write_routes(self):
        """两条通用兜底规则（`POST + /reviews`、`POST + /approvals`）接不住这些路径。

        这就是"必须显式加规则"的成因：#13 是唯一**巧合**命中 `/reviews` 的写路由，
        #9/#11/#12 与 #14 全部落空。
        """
        paths = [route[2] for route in WIKI_WRITE_ROUTES]
        self.assertEqual([path for path in paths if path.endswith('/reviews')],
                         ['/v1/wiki/entries/{entry_id}/reviews'])
        self.assertEqual([path for path in paths if path.endswith('/approvals')], [])

    def test_without_the_explicit_table_the_writes_are_denied_not_left_open(self):
        """反证：清空显式表后，5 条写路由只能落到"默认拒绝"，绝不能落到 `None`。

        漏一条规则的正确失败方向是**误拒**（403），不是越权（任意登录用户可写）。
        """
        surface = _surface()
        self.assertTrue(surface['WIKI_WRITE_PERMISSIONS'])
        surface['WIKI_WRITE_PERMISSIONS'] = ()
        for number, method, path, _permission, _module, _function in WIKI_WRITE_ROUTES:
            with self.subTest(route=number, path=path):
                concrete = _concrete(path)
                self.assertIsNone(surface['_wiki_write_permission'](concrete))
                fallback = surface['_required_permission'](_Request(method, concrete))
                self.assertIsNotNone(fallback, '#%s 在缺规则时变成了默认放开' % number)
                self.assertEqual(fallback, 'admin')

    def test_unnamed_wiki_write_paths_are_denied_by_default(self):
        """`/v1/wiki/*` 的写方法整体在门内：没列名的路径 → `admin`，不是 `None`。"""
        surface = _surface()
        for path in ('/v1/wiki', '/v1/wiki/entries', '/v1/wiki/builds/x', '/v1/wiki/entries/x',
                     '/v1/wiki/entries/x/archive', '/v1/wiki/builds/x/cancel'):
            with self.subTest(path=path):
                self.assertEqual(surface['_required_permission'](_Request('POST', path)), 'admin')

    def test_the_wiki_gate_exists_in_the_mapping_source(self):
        """上一条依赖 `wiki_path` 分支存在；把它钉在源码上，删除即失败。"""
        self.assertIn("wiki_path=path=='/v1/wiki'orpath.startswith('/v1/wiki/')",
                      _source_line(API_SOURCE, 'wiki_path=path=='))

    def test_read_routes_stay_role_free_but_still_need_an_identity(self):
        """读接口按契约返回 `None`（无角色门）——它保护的是"必须有身份"而不是角色。

        `None` 只在中间件**已经**要求 identity 的前提下才成立：见下一页的匿名白名单断言。
        """
        reads = [route for route in WIKI_ROUTES if route[1] != 'POST']
        self.assertEqual(len(reads), 9)
        for number, method, path, permission, _module, _function in reads:
            with self.subTest(route=number, path=path):
                self.assertIsNone(permission)
                self.assertIsNone(self._permission(method, path))


class WikiAnonymousAccessTest(unittest.TestCase):
    """坑③/红线：`/v1/wiki` 不在任何匿名白名单里，且恒 `no-store`。"""

    @classmethod
    def setUpClass(cls):
        cls.surface = _surface()

    def test_the_wiki_prefix_is_in_no_anonymous_allow_list(self):
        surface = self.surface
        self.assertNotIn('/v1/wiki', surface['PUBLIC_PATHS'])
        self.assertNotIn('/v1/wiki', surface['PUBLIC_WRITE_PATHS'])
        for prefix in surface['PUBLIC_READ_PREFIXES']:
            self.assertFalse('/v1/wiki'.startswith(prefix), prefix)
            self.assertFalse(prefix.startswith('/v1/wiki'), prefix)
        # 契约 §1 的白名单冻结值：001 的公开读前缀只有这三条。
        self.assertEqual(surface['PUBLIC_READ_PREFIXES'],
                         ('/v1/documents', '/v1/runs', '/assets/'))

    def test_every_wiki_route_is_unreachable_without_an_identity(self):
        """逐条走 `_is_public_request`（中间件判据）：14 条全部不是公开请求。

        含 `DATAMIND_PUBLIC_CHAT=1` 的情形：那个开关只对 `PUBLIC_WRITE_PATHS`
        （`/v1/chat`、`/v1/chat/stream`）生效，与 Wiki 无关。
        """
        previous = os.environ.get('DATAMIND_PUBLIC_CHAT')
        os.environ['DATAMIND_PUBLIC_CHAT'] = '1'
        try:
            surface = _surface()
            self.assertTrue(surface['_public_chat_enabled']())
            for number, method, path, *_ in WIKI_ROUTES:
                concrete = _concrete(path)
                with self.subTest(route=number, path=path):
                    self.assertFalse(surface['_is_public_request'](_Request(method, concrete)))
        finally:
            if previous is None:
                os.environ.pop('DATAMIND_PUBLIC_CHAT', None)
            else:
                os.environ['DATAMIND_PUBLIC_CHAT'] = previous

    def test_the_wiki_prefix_is_no_store(self):
        surface = self.surface
        self.assertIn('/v1/wiki', surface['_NO_STORE_PREFIXES'])
        self.assertTrue(surface['_no_store_path']('/v1/wiki'))
        for number, method, path, *_ in WIKI_ROUTES:
            concrete = _concrete(path)
            with self.subTest(route=number, path=path):
                self.assertTrue(surface['_no_store_path'](concrete),
                                '%s 的响应可能被缓存' % concrete)
        # 边界：前缀匹配是 prefix + '/'，`/v1/wikiish` 不是 Wiki 私有面。
        self.assertFalse(surface['_no_store_path']('/v1/wikiish'))


class WikiFlatRoleTest(unittest.TestCase):
    """坑④：角色是扁平的（`auth.py:17`），`reviewer` 与 `approver` 互不继承（§3.1 脚注 2）。"""

    def test_the_role_set_is_flat_and_the_bypass_is_admin_only(self):
        self.assertEqual(_literal(AUTH_SOURCE, 'ROLES'),
                         {'viewer', 'reviewer', 'approver', 'admin'})
        bypass = _source_line(AUTH_SOURCE, "if permission and permission not in user['roles']")
        self.assertIn("'admin'notinuser['roles']", bypass)
        self.assertNotIn("'reviewer'notin", bypass)
        self.assertNotIn("'approver'notin", bypass)
        # 角色之间没有任何继承/蕴含声明。
        for hierarchy in ('ROLE_PARENTS', 'implies_role', 'inherits_from', 'ROLE_HIERARCHY'):
            self.assertNotIn(hierarchy, AUTH_SOURCE + API_SOURCE)

    def test_reviewer_and_approver_do_not_inherit_each_other(self):
        for roles, permission in ((['reviewer'], 'approver'), (['approver'], 'reviewer')):
            with self.subTest(roles=roles, permission=permission):
                with self.assertRaises(HTTPException) as denied:
                    wiki.require_permission({'user_id': 'u', 'roles': roles}, permission)
                self.assertEqual(denied.exception.status_code, 403)
                self.assertEqual(denied.exception.detail.get('error_code'),
                                 'WIKI_PERMISSION_DENIED')
                self.assertFalse(wiki.has_role({'roles': roles}, permission))
                self.assertFalse(wiki.is_admin({'roles': roles}))
        # 同一角色自己仍然通过；`admin` 旁路不受影响。
        wiki.require_permission({'user_id': 'u', 'roles': ['reviewer']}, 'reviewer')
        wiki.require_permission({'user_id': 'u', 'roles': ['approver']}, 'approver')
        wiki.require_permission({'user_id': 'u', 'roles': ['admin']}, 'reviewer')
        wiki.require_permission({'user_id': 'u', 'roles': ['admin']}, 'approver')

    def test_the_wrong_role_is_denied_on_every_wiki_write_route(self):
        """把真实映射表 + `auth.py:195` 的规则合起来逐条走一遍（反例矩阵）。

        `auth.py:195` 只给 `admin` 旁路（上一用例已把那一行钉在源码上），这里照同一条
        规则复算：`approver` 打 #9/#11/#12/#13 → 403，`reviewer` 打 #14 → 403。
        中间件层给的是仓库通行的 `PERMISSION_DENIED`；`WIKI_PERMISSION_DENIED` 是服务层
        写事务内复查（`wiki.require_permission`）的码，两层都拒绝。
        """
        surface = _surface()

        def denied(roles, permission):
            return permission is not None and permission not in roles and 'admin' not in roles

        for number, method, path, permission, _module, _function in WIKI_WRITE_ROUTES:
            concrete = _concrete(path)
            self.assertEqual(surface['_required_permission'](_Request(method, concrete)), permission)
            with self.subTest(route=number, path=path, roles=['viewer']):
                self.assertTrue(denied(['viewer'], permission))
            other = 'reviewer' if permission == 'approver' else 'approver'
            with self.subTest(route=number, path=path, roles=[other]):
                self.assertTrue(denied([other], permission),
                                '#%s 被 %s 越权通过' % (number, other))
            with self.subTest(route=number, path=path, roles=['admin']):
                self.assertFalse(denied(['admin'], permission))
        # 读接口不做角色门：`viewer` 也是"允许"（`None` 的真含义）。
        for number, method, path, permission, _module, _function in WIKI_ROUTES:
            if permission is None:
                with self.subTest(route=number, roles=['viewer']):
                    self.assertFalse(denied(['viewer'], permission))


try:  # `.venv` 有 psycopg2：能 import 真身时，再核对一次"抽取 == 真身"与真实路由表。
    import datamind.api as api_module
    from fastapi.routing import APIRoute
except Exception:  # pragma: no cover - 系统 python 走这一支（没有 psycopg2）
    api_module = None
    APIRoute = None


@unittest.skipUnless(api_module is not None, 'datamind.api 需要 psycopg2（只有 .venv 有）')
class ExtractedSurfaceMatchesTheImportedModuleTest(unittest.TestCase):
    """防抽取漂移：`.venv` 下把静态抽取的结果与真实模块逐项比对。"""

    def test_constants_and_permission_function_match_the_imported_module(self):
        surface = _surface()
        for name in _SURFACE_CONSTANTS:
            self.assertEqual(surface[name], getattr(api_module, name), name)
        for number, method, path, *_ in WIKI_ROUTES:
            concrete = _concrete(path)
            with self.subTest(route=number, path=path):
                self.assertEqual(surface['_required_permission'](_Request(method, concrete)),
                                 api_module._required_permission(_Request(method, concrete)))
                self.assertTrue(surface['_no_store_path'](concrete))
                self.assertTrue(api_module._no_store_path(concrete))

    def test_the_app_registers_exactly_the_fourteen_wiki_routes(self):
        registered = {(method, route.path) for route in api_module.app.routes
                      if isinstance(route, APIRoute) and route.path.startswith('/v1/wiki')
                      for method in (route.methods or ())}
        expected = {(method, path) for _n, method, path, _p, _m, _f in WIKI_ROUTES}
        self.assertEqual(registered, expected)


if __name__ == '__main__':
    unittest.main()
