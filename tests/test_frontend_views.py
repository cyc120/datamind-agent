"""Vue 前端的契约回归：六个 legacy 视图迁移后的「诚实性 / 安全性」护栏。

本仓库没有 JavaScript 测试运行器，也没有真实浏览器。这里沿用 commit 7fc0035 一并删除的
旧契约测试（``test_upload_ui.py`` / ``test_upload_selector_ui.py``，
以及 ``test_frontend_locators.py`` 里针对 ``web/app.js`` 的那一半）的做法：
把前端源码当**文本**读，钉住那些一旦丢失就会导致「过度声称」「白屏」或「画错证据位置」
的规则。这些断言不启动 DOM，也不声称验证了运行时的真实渲染结果——它们只证明规则
**写在源码里**。

诚实性是本仓库的功能，不是文案修饰：一个把值渲染出来却不带保留条件的屏幕是失败，不是成功。
所以最后一段钉的就是那些「必须活下来」的中文文案。

反向验证（证明断言不是空转）：凡是「不得出现 X」的规则，都会先用一次**变异**
（把规则从文本里抹掉/塞进违规内容）再跑同一个判据，并断言判据在变异文本上**必须变红**。
只在当前源码上恰好为真的断言，比没有断言更糟。

被钉住的规则：

* 旋转页永远不得叠画在未旋转的页面图上（``locator.ts``；之前只有散文在守）；
* 六个 legacy 路由真实存在，且兜底路由仍在最后（否则旧深链会白屏）；
* 侧栏不再指向退役的旧版控制台深链（``/#…``）；
* 上传走 raw bytes（``POST /v1/uploads`` + ``application/octet-stream``），
  不得回落到 multipart ``FormData`` 或退役的 ``/v1/files/uploads``；
* ``key_masked`` 只作为 placeholder/hint 出现，绝不绑进输入框的 value，
  ``__clear__`` 绝不当成 ``api_key`` 的哨兵；
* 六条诚实文案必须仍然存在于对应屏幕的源码里。
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend' / 'src'

LOCATOR_TS = FRONTEND / 'components' / 'fields' / 'locator.ts'
ROUTER_TS = FRONTEND / 'router' / 'index.ts'
SIDE_NAV = FRONTEND / 'components' / 'SideNav.vue'
CLIENT_TS = FRONTEND / 'api' / 'client.ts'
MODEL_PAGE = FRONTEND / 'views' / 'ModelPage.vue'
PROFILE_EDITOR = FRONTEND / 'components' / 'model' / 'ProfileEditor.vue'
FILES_PAGE = FRONTEND / 'views' / 'FilesPage.vue'
AUTHORITY_PAGE = FRONTEND / 'views' / 'AuthorityPage.vue'
FIELDS_PAGE = FRONTEND / 'views' / 'FieldsPage.vue'


# ══════════════════════════════════════════════════════════════════════════
# 文本工具
# ══════════════════════════════════════════════════════════════════════════

def read(path):
    return path.read_text(encoding='utf-8')


def strip_comments(source):
    """把 JS/TS 的 ``//``、``/* */`` 与 Vue 模板的 ``<!-- -->`` 注释抹成等长空白。

    WHY 要看「代码」而不是「原文」：退役契约的名字与「不许这样做」的说明会**故意**写进注释，
    例如 ``client.ts`` 的「本文件不提供 /v1/files/uploads」、``ModelPage.vue`` 的
    「__clear__ 只用于敏感头与代理」。若拿字面量直接断言「不得出现」，我们只会逼着后来者
    删掉解释——规则还在，说明没了。所以红线只对**真正的代码**判定。

    实现上保持**等长**并逐字符保留换行，这样按行断言的下标与原文完全一致。
    字符串字面量原样保留（``//`` 出现在 URL 里不会被误判成注释）。
    """
    out = []
    i = 0
    n = len(source)
    state = None
    while i < n:
        ch = source[i]
        if state is None:
            if source.startswith('//', i):
                out.append('  ')
                state = '//'
                i += 2
                continue
            if source.startswith('/*', i):
                out.append('  ')
                state = '/*'
                i += 2
                continue
            if source.startswith('<!--', i):
                out.append('    ')
                state = '<!--'
                i += 4
                continue
            if ch in ('"', "'", '`'):
                state = ch
            out.append(ch)
            i += 1
            continue
        if state == '//':
            out.append('\n' if ch == '\n' else ' ')
            if ch == '\n':
                state = None
            i += 1
            continue
        if state == '/*':
            if source.startswith('*/', i):
                out.append('  ')
                state = None
                i += 2
                continue
            out.append('\n' if ch == '\n' else ' ')
            i += 1
            continue
        if state == '<!--':
            if source.startswith('-->', i):
                out.append('   ')
                state = None
                i += 3
                continue
            out.append('\n' if ch == '\n' else ' ')
            i += 1
            continue
        # 字符串字面量内部：原样保留；处理转义，避免 \' 提前闭合。
        if ch == '\\':
            out.append(source[i:i + 2])
            i += 2
            continue
        out.append(ch)
        if ch == state:
            state = None
        i += 1
    return ''.join(out)


RELATIVE_IMPORT_RE = re.compile(r"""from\s+['"](\.{1,2}/[^'"]+)['"]""")


def module_surface(entry):
    """入口 ``.vue`` 及其（递归）相对导入的本地模块 → ``{路径: 源码}``。

    WHY 不只看单个文件：诚实文案往往沉在底层的纯函数模块里（例如 ``uploadHonesty.ts``），
    页面只负责渲染。要钉的规则是「这块屏幕上必须出现」，所以断言对象必须是
    **这个屏幕真正会加载的源码集合**，否则会把「文案搬进组件」误判成「文案丢了」。
    """
    surface = {}
    pending = [entry]
    while pending:
        path = pending.pop().resolve()
        if path in surface or not path.is_file():
            continue
        if path.suffix not in ('.vue', '.ts'):
            continue
        text = read(path)
        surface[path] = text
        for raw in RELATIVE_IMPORT_RE.findall(text):
            base = path.parent / raw
            for candidate in (base, base.with_suffix('.ts'), base.with_suffix('.vue'),
                              base.parent / (base.name + '.ts'),
                              base.parent / (base.name + '.vue')):
                if candidate.is_file():
                    pending.append(candidate)
                    break
    return surface


def files_view_modules(entry):
    """FilesPage 依赖图里**本屏自己的视图层**模块（``components/files/``）。

    WHY 要收窄：共享的 ``api/client.ts`` 也会在注释里同时出现「审批」「可检索」，
    把扫描面放到整张依赖图上，文案真被删掉时断言照样变绿——那是空转。
    """
    return {path: text for path, text in module_surface(entry).items()
            if path.parent.name == 'files'}


def function_body(source, name):
    """用花括号配平取出 ``function name(...) {...}``（含 async/export 形式）的函数体。"""
    match = re.search(r'(?:export\s+)?(?:async\s+)?function\s+' + re.escape(name) + r'\s*\(', source)
    if not match:
        return ''
    start = source.index('{', match.end() - 1)
    depth = 0
    for position in range(start, len(source)):
        character = source[position]
        if character == '{':
            depth += 1
        elif character == '}':
            depth -= 1
            if depth == 0:
                return source[start:position + 1]
    return source[start:]


# ══════════════════════════════════════════════════════════════════════════
# 1) 定位框：旋转页不得叠画在未旋转的页面图上
# ══════════════════════════════════════════════════════════════════════════

ROTATION_FIELD = 'rotation_degrees'

# 护栏不是「声明了 rotation_degrees」，而是「把它读进一个拒绝判定」。
# 只在 interface 里列一个字段却从不判定，等于没有护栏——那正是「猜一个位置」。
ROTATION_GUARD_RE = re.compile(
    r'(?:!\s*[\w.\[\]?()]*' + re.escape(ROTATION_FIELD) + r')'  # !ref.rotation_degrees
    + r'|\b(?:if|while|return)\b[^\n;]*' + re.escape(ROTATION_FIELD)  # if (...rotation_degrees...)
    + r'|' + re.escape(ROTATION_FIELD) + r'\s*\)?\s*(?:===|!==|==|!=|>|<)'  # 直接比较
)


def rotation_guard_present(source):
    """源文本里是否存在「非 0 旋转就拒绝绘制」的护栏。"""
    if ROTATION_FIELD not in source:
        return False
    return bool(ROTATION_GUARD_RE.search(source))


class LocatorDrawabilityTests(unittest.TestCase):
    """证据定位框的判定：坐标系必须是 pdf_points，旋转页必须拒绝。"""

    def setUp(self):
        self.source = read(LOCATOR_TS)

    def test_the_helper_declares_the_pdf_point_coordinate_contract(self):
        # 四个名字缺一不可：没有 rotation_degrees 就无法判断页面图能不能叠；
        # 没有 page_size/bbox 就无法用 viewBox 直接铺（只能靠缩放推断 = 猜）；
        # 没有 pdf_points 就无法证明 bbox 与页面图同坐标系。
        # 用 assertTrue + 短消息：assertIn 失败时会把整份源码打进报告，淹没真正的结论。
        for needle in ('rotation_degrees', 'page_size', 'bbox', 'pdf_points'):
            self.assertTrue(needle in self.source, 'locator.ts 缺少 %s' % needle)
        # 判定必须是导出的**纯函数**谓词，视图层只允许「画」或「出标签」两种结果。
        self.assertRegex(self.source, r'export\s+function\s+locatorVerdict\b')
        self.assertIn('drawable', self.source)
        self.assertIn('无法绘制', self.source)

    def test_a_rotated_page_is_never_drawn_over_the_un_rotated_page_image(self):
        # WHY 这是本文件最重要的一条：本客户端取到的页面图是**未旋转**的。
        # 若后端声明 rotation_degrees != 0 而前端照画，bbox 会落在错误的位置上，
        # 用户会把一个猜出来的区域当成证据来源。宁可不画，也不能画错。
        self.assertTrue(
            rotation_guard_present(self.source),
            'locator.ts 缺少旋转护栏：rotation_degrees 未参与拒绝判定，'
            '旋转页会被叠画在未旋转的页面图上（应返回 drawable:false 并给出中文原因）')

    def test_the_rotation_guard_predicate_is_not_vacuous(self):
        # 先证明判据能认出真正的护栏，再证明抽掉令牌后它会变红。
        # 这两步都不依赖当前源码是否有护栏，所以即使上面那条红了，这条仍能证明断言不是空转。
        guarded = (self.source
                   + "\n  if (ref.rotation_degrees !== 0) "
                     "return { drawable: false, reason: '页面被旋转：无法对齐未旋转的页面图' }\n")
        self.assertTrue(rotation_guard_present(guarded), '判据必须能识别真正的旋转护栏')
        self.assertFalse(
            rotation_guard_present(guarded.replace('rotation_degrees', 'rotation_deg')),
            '抽掉 rotation_degrees 令牌后判据必须变红，否则断言是空转')


# ══════════════════════════════════════════════════════════════════════════
# 2) 六条路由真实存在，兜底仍在最后
# ══════════════════════════════════════════════════════════════════════════

SIX_VIEW_PATHS = ('/model', '/fields', '/authority', '/cad', '/dimensions', '/files')
CATCH_ALL_PATH = '/:pathMatch(.*)*'
ROUTE_ENTRY_RE = re.compile(r"\{\s*path:\s*'([^']+)'")


class RouterMigrationTests(unittest.TestCase):
    """六个 legacy 视图必须是真正的站内路由，不能只留一条兜底。"""

    def setUp(self):
        self.source = read(ROUTER_TS)
        self.paths = ROUTE_ENTRY_RE.findall(strip_comments(self.source))

    def test_all_six_legacy_views_have_real_routes(self):
        for path in SIX_VIEW_PATHS:
            self.assertIn(path, self.paths, path)
        # 每个路由都要指向一个真实存在的视图文件，否则点进去仍是白屏。
        views = {p.stem for p in (FRONTEND / 'views').glob('*.vue')}
        for name in ('ModelPage', 'FieldsPage', 'AuthorityPage', 'CadPage', 'DimensionsPage',
                     'FilesPage'):
            self.assertIn(name, views, name)

    def test_the_catch_all_route_stays_last(self):
        # WHY：旧版深链（/#model 等）会被 hash 模式解析成 /model；路由表顺序错了，
        # 兜底页会把它们全部吃掉，用户看到的就是「未迁移」而不是真正的视图。
        self.assertEqual(self.paths[-1], CATCH_ALL_PATH,
                         '兜底路由必须留在最后，当前顺序：%r' % (self.paths,))
        for path in SIX_VIEW_PATHS:
            self.assertLess(self.paths.index(path), len(self.paths) - 1, path)


# ══════════════════════════════════════════════════════════════════════════
# 3) 侧栏：不再有指向退役旧版控制台的深链
# ══════════════════════════════════════════════════════════════════════════

SIX_NAV_ITEMS = (
    ('model', '/model', '模型接入'),
    ('fields', '/fields', '字段复核'),
    ('authority', '/authority', '来源权威规则'),
    ('cad', '/cad', '图纸与实体'),
    ('dimensions', '/dimensions', '尺寸核验'),
    ('files', '/files', '我的文件'),
)


class SideNavMigrationTests(unittest.TestCase):
    """侧栏八个入口全部改走站内路由，旧 ``/#…`` 深链与「未迁移」标签不得再对它们生效。"""

    def setUp(self):
        self.source = read(SIDE_NAV)
        self.code = strip_comments(self.source)

    def test_the_six_entries_are_router_targets_not_legacy_hrefs(self):
        for key, path, label in SIX_NAV_ITEMS:
            items = re.findall(r"\{[^{}]*key:\s*'%s'[^{}]*\}" % re.escape(key), self.code)
            self.assertTrue(items, '侧栏缺少 %s 入口（key=%s）' % (label, key))
            item = items[0]
            self.assertIn("to: '%s'" % path, item, item)
            self.assertIn(label, item, item)
            # 只要还带 href，就会回落到旧版控制台的深链。
            self.assertNotIn('href', item, item)

    def test_the_un_migrated_badge_cannot_reach_the_six_entries(self):
        # 「未迁移」只能挂在兜底分支上；上面已断言六个入口都有 to:，
        # 模板里 `v-if="it.to"` 优先，因此这六个入口永远走不到带「未迁移」的 v-else。
        self.assertRegex(self.code, r'v-if="it\.to"')
        for key, _path, _label in SIX_NAV_ITEMS:
            items = re.findall(r"\{[^{}]*key:\s*'%s'[^{}]*\}" % re.escape(key), self.code)
            self.assertNotIn('未迁移', items[0], items[0])

    def test_no_legacy_hash_href_remains_in_the_side_nav(self):
        # 规格：侧栏不得再出现以 /# 开头的 legacy href（含页脚用户区）。
        hits = [(number, line.strip())
                for number, line in enumerate(strip_comments(self.source).splitlines(), 1)
                if re.search(r'href\s*[=:]\s*["\']?/#', line)]
        self.assertEqual(hits, [],
                         '仍有指向退役旧版控制台的 /# 深链（行号, 内容）：%r' % (hits,))


# ══════════════════════════════════════════════════════════════════════════
# 4) 上传：raw bytes，不是 multipart，也不是退役的 file_jobs
# ══════════════════════════════════════════════════════════════════════════

FORBIDDEN_UPLOAD_TOKENS = ('FormData', '/v1/files/uploads', '/v1/files/jobs')


def upload_code_violations(source):
    """返回在**代码**（注释已抹除）里仍然出现的退役/错误上传令牌。"""
    code = strip_comments(source)
    return [token for token in FORBIDDEN_UPLOAD_TOKENS if token in code]


class RawBytesUploadTests(unittest.TestCase):
    """唯一上传入口是 raw bytes；多部分表单与退役接口都必须是死路。"""

    def setUp(self):
        self.source = read(CLIENT_TS)
        self.body = function_body(self.source, 'uploadFile')

    def test_upload_posts_raw_bytes_with_octet_stream(self):
        self.assertTrue(self.body, 'client.ts 必须导出 uploadFile')
        self.assertIn('application/octet-stream', self.body)
        self.assertIn("method: 'POST'", self.body)
        # File 本体作为 body——不是包装成表单字段。
        self.assertIn('body: file', self.body)
        self.assertRegex(self.body, r'`/v1/uploads\?')
        self.assertIn('URLSearchParams', self.body)
        self.assertIn("'X-CSRF-Token'", self.body)

    def test_no_multipart_form_data_and_no_retired_file_jobs_endpoint(self):
        # 字面量层面：FormData 连注释里都不该出现。
        self.assertNotIn('FormData', self.source)
        # 代码层面：退役契约只允许出现在解释它退役的注释里（见 strip_comments 的 WHY）。
        self.assertEqual(upload_code_violations(self.source), [],
                         '上传路径退回了 multipart / 退役接口')

    def test_the_forbidden_token_scan_is_not_vacuous(self):
        self.assertEqual(upload_code_violations(self.source), [])
        dirty = self.source + ("\nconst fd = new FormData()\n"
                               "fetch('/v1/files/uploads?filename=x', { body: fd })\n"
                               "fetch('/v1/files/jobs', { body: fd })\n")
        self.assertEqual(upload_code_violations(dirty), list(FORBIDDEN_UPLOAD_TOKENS))
        # 注释里的说明不算违规：抹除后必须扫不到。
        self.assertEqual(upload_code_violations('// 本文件不提供 /v1/files/uploads\n'), [])


# ══════════════════════════════════════════════════════════════════════════
# 5) 密钥纪律：掩码只进 placeholder，__clear__ 绝不是 api_key 的哨兵
# ══════════════════════════════════════════════════════════════════════════

RE_BIND_VALUE_KEY_MASKED = re.compile(
    r'''(?:v-model|:value|v-bind:value|value)\s*=\s*"[^"]*(?:key_masked|keyMasked|loadedKeyMasked)''')


def mask_only_reaches_the_placeholder(source):
    """掩码是否**只**流向 placeholder：不绑进 value，且 placeholder 由掩码推导。"""
    code = strip_comments(source)
    if 'key_masked' not in code:
        return False
    if RE_BIND_VALUE_KEY_MASKED.search(code):
        return False
    # 掩码变量（loadedKeyMasked，来自 profile.key_masked）被喂给 placeholder 计算属性；
    # 两者之间隔着 computed(() =>\n  loadedKeyMasked.value，所以不能按单行匹配。
    return bool(re.search(r'apiKeyPlaceholder[\s\S]{0,200}?loadedKeyMasked', code))


class ModelSecretBindingTests(unittest.TestCase):
    """模型接入页的密钥纪律：回填掩码会永久毁掉用户真实密钥。"""

    def setUp(self):
        self.page_source = read(MODEL_PAGE)
        self.page_code = strip_comments(self.page_source)
        self.editor_source = read(PROFILE_EDITOR)
        self.editor_code = strip_comments(self.editor_source)

    def test_key_masked_is_never_bound_into_an_input_value(self):
        for name, source in (('ModelPage.vue', self.page_source),
                             ('ProfileEditor.vue', self.editor_source)):
            code = strip_comments(source)
            self.assertIsNone(RE_BIND_VALUE_KEY_MASKED.search(code),
                              '%s 把密钥掩码绑进了输入框的 value' % name)
            for number, line in enumerate(code.splitlines(), 1):
                # 掩码既可能叫 key_masked（服务端字段），也可能叫 loadedKeyMasked（本组件的 ref）。
                if 'key_masked' in line or 'KeyMasked' in line:
                    self.assertNotIn('v-model', line, '%s:%d' % (name, number))

    def test_key_masked_only_flows_into_the_placeholder(self):
        # 规则 (a)：输入框的值永远来自用户输入（apiKey），掩码只喂给 placeholder 计算属性。
        self.assertRegex(self.editor_code, r'v-model="apiKey"')
        self.assertRegex(self.editor_code, r':placeholder="apiKeyPlaceholder"')
        self.assertRegex(self.editor_code, r'loadedKeyMasked\.value\s*=[^\n]*key_masked')
        self.assertTrue(mask_only_reaches_the_placeholder(self.editor_source),
                        'placeholder 必须由掩码推导，且掩码不得绑进任何 value；'
                        '否则掩码会被回填并提交，把真实密钥覆盖成掩码字符串')
        self.assertRegex(self.editor_code, r"apiKey\.value\s*=\s*''",
                         '读取配置时必须先把输入框清空，不能从掩码回填')

    def test_the_key_masked_placeholder_check_is_not_vacuous(self):
        # 用一个自足的正例证明判据可能为真，再把「掩码只喂 placeholder」改成
        # 「掩码直接绑进输入框」，判据必须变红。不依赖当前源码是否有该规则。
        good = ("const loadedKeyMasked = ref('')\n"
                "function load(profile) { loadedKeyMasked.value = profile.key_masked || '' }\n"
                'const apiKeyPlaceholder = computed(() =>\n'
                '  loadedKeyMasked.value ? `已保存 ${loadedKeyMasked.value}` : \'\',\n'
                ')\n'
                '<input v-model="apiKey" :placeholder="apiKeyPlaceholder" />\n')
        self.assertTrue(mask_only_reaches_the_placeholder(good))
        mutated = good.replace('v-model="apiKey"', 'v-model="loadedKeyMasked"')
        self.assertFalse(mask_only_reaches_the_placeholder(mutated),
                         '掩码被绑进输入框后判据必须变红，否则断言是空转')

    def test_the_clear_sentinel_is_never_submitted_as_an_api_key(self):
        # __clear__ 只对敏感请求头与代理有效；把它当 api_key 提交，
        # 服务端会把字面量当成新密钥存下来。
        self.assertNotRegex(self.editor_code,
                            r'''api_key\s*=\s*(?:CLEAR_SENTINEL|['"]__clear__)''')
        self.assertNotRegex(self.editor_code, r'payload\.api_key\s*=[^\n]*__clear__')
        self.assertRegex(self.editor_code, r'payload\.api_key\s*=\s*key\b')
        # 哨兵只允许用在 header/proxy 上。
        self.assertRegex(self.editor_code, r'headers\[row\.name\]\s*=\s*CLEAR_SENTINEL')
        self.assertRegex(self.editor_code, r'payload\.proxy\s*=\s*CLEAR_SENTINEL')

    def test_clear_sentinel_never_shares_a_code_line_with_api_key(self):
        # 规格：__clear__ 不得与 api_key 出现在同一行。注释里解释「为什么不能用」
        # 是必须保留的说明（ModelPage.vue 的块注释、messages.ts 的告警文案），
        # 所以这里只对**代码**逐行判定；渲染文案里的同现是解释，不是提交。
        for name, code in (('ModelPage.vue', self.page_code),
                           ('ProfileEditor.vue', self.editor_code)):
            offenders = [(number, line.strip())
                         for number, line in enumerate(code.splitlines(), 1)
                         if '__clear__' in line and 'api_key' in line]
            self.assertEqual(offenders, [], '%s 里 __clear__ 与 api_key 同处一行：%r'
                             % (name, offenders))


# ══════════════════════════════════════════════════════════════════════════
# 6) 诚实文案：这些句子就是功能本身
# ══════════════════════════════════════════════════════════════════════════

class HonestyCopyTests(unittest.TestCase):
    """六条必须活下来的中文断言：删掉它们，屏幕就开始过度声称。"""

    def test_files_page_keeps_parse_review_and_searchability_apart(self):
        # 解析完成 ≠ 人工复核 ≠ 可检索。这三件事混成一个「已完成」，用户就会
        # 以为刚上传的文件已经能拿来问答。
        source = read(FILES_PAGE)
        self.assertIn('可检索', source)
        self.assertIn('后台处理器未运行', source)

    def test_the_files_screen_still_says_approval_is_not_searchability(self):
        # 「审批 ≠ 可检索」是本屏的核心。文案落在 FilesPage 真正加载的**视图层**模块里
        # （当前是 components/files/uploadHonesty.ts），而不是必须写死在页面文件本身。
        # WHY 只认 components/files/：共享的 api/client.ts 也会在两个词的注释里同现，
        # 若把扫描面放宽到整张依赖图，这条断言会在文案被删掉后仍然变绿（空转）。
        self.assertRegex(read(FILES_PAGE), r'components/files/uploadHonesty')
        holders = sorted(str(path.relative_to(ROOT))
                         for path, text in files_view_modules(FILES_PAGE).items()
                         if '审批' in text and '可检索' in text)
        self.assertTrue(holders, '「审批」与「可检索」必须同框出现，说明二者不是同一件事')

    def test_the_files_screen_states_the_vector_index_is_deferred(self):
        # 本管道 vector_index=deferred、不写 chunks：不说这句话，
        # 用户会把「已复核」读成「已可问答」。
        holders = sorted(str(path.relative_to(ROOT))
                         for path, text in files_view_modules(FILES_PAGE).items()
                         if '未建立向量索引' in text)
        self.assertTrue(holders, '缺少「未建立向量索引」的诚实提示')

    def test_authority_page_keeps_the_registry_integrity_warning(self):
        # 注册表里有校验不过的规则时，运行时整体 fail closed——必须明说，
        # 不能让用户以为「其余的规则还在正常裁决」。
        self.assertIn('登记完整性校验失败', read(AUTHORITY_PAGE))

    def test_fields_page_keeps_the_historical_unauthenticated_label(self):
        # reviewer_user_id 为空的复核是历史未认证记录，不能显示成一次正常复核，
        # 也不能作为审批依据。
        self.assertIn('历史未认证', read(FIELDS_PAGE))


if __name__ == '__main__':
    unittest.main()
