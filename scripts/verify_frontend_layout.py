"""前端布局与可点性验证：用无头 Chrome + CDP 真的渲染、真的点。

WHY 需要这个脚本（它是一条真实 bug 的产物，不是预防性洁癖）：
    侧栏历史区被 flex 压成 **4px**、`overflow: hidden` 又把底部裁掉，连「历史对话」标题都
    看不见；`⋯` 菜单在列表底部那几行被滚动容器裁掉，**真实鼠标点不到「重命名」**。
    这两件事在源码里、在 `--dump-dom` 的 DOM 树里、在「元素存在」的断言里**全都看不出来**
    —— 元素在，只是 0 高度或被裁剪。所以判据必须是**几何**与**命中测试（hit test）**：
      * 高度：`getBoundingClientRect()` 是否给了可用空间；
      * 可点性：`document.elementFromPoint(中心)` 是否就是那个可点元素（程序化 `.click()`
        会绕过命中测试，所以它能点通**不代表**用户能点通）；
      * 真实鼠标：`Input.dispatchMouseEvent` 走完整命中测试链路。

没有 Chrome 时**跳过并以 0 退出**（本仓库的回归套件不该因为缺浏览器而变红），
但会明确打印「已跳过」，不会伪装成通过。

用法：
    .venv/bin/python scripts/verify_frontend_layout.py
    DATAMIND_CHROME=/path/to/chrome .venv/bin/python scripts/verify_frontend_layout.py
环境变量：
    DATAMIND_BASE_URL   默认 http://127.0.0.1:8000（API 必须已在运行）
"""
import base64
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

BASE_URL = os.environ.get('DATAMIND_BASE_URL', 'http://127.0.0.1:8000').rstrip('/')
APP_URL = BASE_URL + '/#/app'

#: 视口档位：一档够高、一档常见笔记本、一档最矮（历史区被压没就是在这些尺寸上暴露的）
VIEWPORTS = ((1440, 900), (1280, 720), (800, 600))
#: 历史区的可用高度下限（CSS 里的 min-height 是 216，这里留一点余量，同时防止它被改回 0）
MIN_HISTORY_HEIGHT = 160
#: 底部状态区必须能被滚动带入视野；允许的误差（取整/边框）
REACH_TOLERANCE = 4

CHROME_CANDIDATES = (
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
)


def find_chrome():
    explicit = os.environ.get('DATAMIND_CHROME')
    if explicit:
        return explicit if pathlib.Path(explicit).exists() else None
    for candidate in CHROME_CANDIDATES:
        if pathlib.Path(candidate).exists():
            return candidate
    for name in ('google-chrome', 'chromium', 'chromium-browser', 'chrome'):
        found = shutil.which(name)
        if found:
            return found
    return None


class CDP:
    """够用的 DevTools Protocol 客户端：WebSocket 握手 + 帧编解码 + Runtime/Input。

    为什么自己写：本仓库的 .venv 里没有任何 WebSocket 客户端，而这里真正需要的
    只有「发一条 JSON、收一条 JSON（或一条事件）」。
    """

    def __init__(self, port):
        self.port = port
        self.sock = None
        self.buf = b''
        self.next_id = 0
        self.events = []

    def wait_ready(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{self.port}/json/version',
                                            timeout=2) as response:
                    return json.loads(response.read())
            except Exception:
                time.sleep(0.3)
        raise TimeoutError('DevTools 端口未就绪')

    def new_target(self, url):
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}/json/new?{urllib.parse.quote(url, safe="")}', method='PUT')
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def close_target(self, target_id):
        try:
            urllib.request.urlopen(
                f'http://127.0.0.1:{self.port}/json/close/{target_id}', timeout=5).read()
        except Exception:
            pass

    def connect(self, ws_url):
        path = ws_url.split(f'127.0.0.1:{self.port}', 1)[1] or '/'
        sock = socket.create_connection(('127.0.0.1', self.port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                      'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                      f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise EOFError('握手期间连接被关闭')
            data += chunk
        head, _, rest = data.partition(b'\r\n\r\n')
        if b'101' not in head.split(b'\r\n')[0]:
            raise RuntimeError(head.decode('utf-8', 'replace'))
        self.sock, self.buf = sock, rest

    def _recv_exact(self, size):
        while len(self.buf) < size:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError('连接已关闭')
            self.buf += chunk
        out, self.buf = self.buf[:size], self.buf[size:]
        return out

    def _send(self, payload):
        mask, size = os.urandom(4), len(payload)
        header = bytearray([0x81])
        if size < 126:
            header.append(0x80 | size)
        elif size < 65536:
            header.append(0x80 | 126)
            header += struct.pack('>H', size)
        else:
            header.append(0x80 | 127)
            header += struct.pack('>Q', size)
        self.sock.sendall(bytes(header) + mask +
                          bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload)))

    def _read(self):
        while True:
            first, second = self._recv_exact(2)
            opcode, size = first & 0x0F, second & 0x7F
            if size == 126:
                size = struct.unpack('>H', self._recv_exact(2))[0]
            elif size == 127:
                size = struct.unpack('>Q', self._recv_exact(8))[0]
            payload = self._recv_exact(size)
            if opcode == 0x8:
                raise EOFError('服务端关闭了 WebSocket')
            if opcode == 0x9:
                self.sock.sendall(b'\x8a\x80' + os.urandom(4))
                continue
            if opcode in (0x1, 0x2):
                return json.loads(payload.decode('utf-8'))

    def call(self, method, params=None, timeout=40):
        self.next_id += 1
        mid = self.next_id
        self._send(json.dumps({'id': mid, 'method': method, 'params': params or {}}).encode())
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._read()
            if message.get('id') == mid:
                if 'error' in message:
                    raise RuntimeError(f'{method}: {message["error"]}')
                return message.get('result', {})
            self.events.append(message)
        raise TimeoutError(method)

    def js(self, expression, timeout=40):
        result = self.call('Runtime.evaluate',
                           {'expression': expression, 'returnByValue': True}, timeout=timeout)
        if 'exceptionDetails' in result:
            raise RuntimeError(str(result['exceptionDetails'].get('text')))
        return result.get('result', {}).get('value')

    def real_click(self, x, y):
        """真实鼠标事件：走完整命中测试（`element.click()` 会绕过它）。"""
        for kind in ('mousePressed', 'mouseReleased'):
            self.call('Input.dispatchMouseEvent',
                      {'type': kind, 'x': x, 'y': y, 'button': 'left', 'clickCount': 1})

    def mouse_move(self, x, y):
        self.call('Input.dispatchMouseEvent', {'type': 'mouseMoved', 'x': x, 'y': y})

    def console_errors(self):
        problems = []
        for event in self.events:
            if event.get('method') == 'Runtime.exceptionThrown':
                detail = event['params'].get('exceptionDetails', {})
                problems.append('JS 异常: ' + str(detail.get('text')))
            elif (event.get('method') == 'Runtime.consoleAPICalled'
                  and event['params'].get('type') == 'error'):
                args = event['params'].get('args', [])
                problems.append('console.error: ' +
                                ' '.join(str(a.get('value', '')) for a in args)[:200])
        return problems


MEASURE = r'''(() => {
  const box = (sel) => { const el = document.querySelector(sel);
    if (!el) return null; const b = el.getBoundingClientRect();
    return {x: b.x, y: b.y, w: b.width, h: b.height, top: b.top, bottom: b.bottom}; };
  const side = document.querySelector('.sidenav');
  const sessions = document.querySelector('.sessions');
  const body = document.querySelector('.sess-body');
  const bodyBox = body ? body.getBoundingClientRect() : null;
  const inside = (b) => !!(bodyBox && b && b.height > 0
    && b.top >= bodyBox.top - 1 && b.bottom <= bodyBox.bottom + 1);
  const rows = [...document.querySelectorAll('[data-testid="session-row"]')];
  const fullyVisible = rows.filter((r) => inside(r.getBoundingClientRect())).length;
  return {
    sessions: box('.sessions'),
    body: box('.sess-body'),
    title: box('.sess-title'),
    search: box('.sess-search'),
    // 标题与搜索框钉在滚动容器外面：即使列表滚到底，它们也必须在可视区
    titleVisible: (() => { const b = document.querySelector('.sess-title');
      if (!b) return false; const r = b.getBoundingClientRect(); return r.height > 0 && r.bottom > 0; })(),
    searchVisible: (() => { const s = document.querySelector('.sess-search');
      if (!s) return false; const r = s.getBoundingClientRect(); return r.height > 0 && r.bottom > 0; })(),
    fullyVisibleRows: fullyVisible,
    rows: rows.length,
    sidebarScrollable: side ? side.scrollHeight > side.clientHeight : false,
    foot: box('.side-foot'),
    viewport: {w: innerWidth, h: innerHeight},
  };
})()'''

MENU_PROBE = r'''(() => {
  const item = (name) => document.querySelector(`[data-testid="session-${name}"]`);
  const probe = (el) => {
    if (!el) return {exists: false};
    const b = el.getBoundingClientRect();
    const cx = b.x + b.width / 2, cy = b.y + b.height / 2;
    const hit = document.elementFromPoint(cx, cy);
    return {exists: true, x: cx, y: cy, h: b.height,
            hitIsItem: hit === el,
            hitTag: hit ? hit.tagName + '.' + String(hit.className) : null,
            withinContainer: (() => { const c = document.querySelector('.sessions').getBoundingClientRect();
              return b.top >= c.top - 1 && b.bottom <= c.bottom + 1; })()};
  };
  return {menu: !!document.querySelector('[data-testid="session-menu-popup"]'),
          rename: probe(item('rename')), remove: probe(item('delete'))};
})()'''


class Report:
    def __init__(self):
        self.failures = []
        self.checks = 0

    def check(self, ok, label, detail=''):
        self.checks += 1
        print(f'  [{"PASS" if ok else "FAIL"}] {label}{(" — " + detail) if detail else ""}')
        if not ok:
            self.failures.append(label)
        return ok


def open_menu_at_row(client, index_from_end):
    """点开倒数第 N 行的 ⋯ 菜单（用真实鼠标，先把它滚进视野）。"""
    return client.js(f'''(() => {{
      const menus = [...document.querySelectorAll('[data-testid="session-menu"]')];
      const target = menus[menus.length - {index_from_end}];
      if (!target) return null;
      target.scrollIntoView({{block: 'nearest'}});
      const b = target.getBoundingClientRect();
      return {{x: b.x + b.width / 2, y: b.y + b.height / 2}};
    }})()''')


def main():
    chrome = find_chrome()
    if not chrome:
        print('已跳过：本机没有找到 Chrome/Chromium（可用 DATAMIND_CHROME 指定路径）。')
        print('注意：跳过不等于通过——这条验证覆盖的是「元素存在但不可见/点不到」这类')
        print('      只有真实浏览器才暴露的缺陷（见文件头注释）。')
        return 0

    try:
        urllib.request.urlopen(BASE_URL + '/health', timeout=5).read()
    except Exception as exc:
        print(f'无法访问 {BASE_URL}/health：{exc}')
        print('请先启动服务（bash scripts/start_local.sh --background）。')
        return 2

    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]

    profile = tempfile.mkdtemp(prefix='datamind-layout-')
    process = subprocess.Popen(
        [chrome, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
         f'--user-data-dir={profile}', f'--remote-debugging-port={port}', 'about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = Report()
    client = None
    target = None
    try:
        client = CDP(port)
        version = client.wait_ready()
        print(f'浏览器：{version.get("Browser")}')
        target = client.new_target(APP_URL)
        client.connect(target['webSocketDebuggerUrl'])
        client.call('Runtime.enable')

        deadline = time.time() + 30
        while time.time() < deadline:
            if client.js("document.querySelectorAll('[data-testid=\"session-row\"]').length > 0"):
                break
            time.sleep(0.4)
        else:
            report.check(False, '工作台渲染出会话列表', '30s 内没有出现任何会话行')
            return 1

        print('\n[1] 各视口下历史区必须真的可见')
        for width, height in VIEWPORTS:
            client.call('Emulation.setDeviceMetricsOverride',
                        {'width': width, 'height': height, 'deviceScaleFactor': 1, 'mobile': False})
            client.js("document.querySelector('.sess-body').scrollTop = 0;"
                      "document.querySelector('.sidenav').scrollTop = 0")
            time.sleep(1.0)
            measured = client.js(MEASURE)
            tag = f'{width}x{height}'
            report.check(measured['sessions']['h'] >= MIN_HISTORY_HEIGHT,
                         f'{tag} 历史区高度 ≥ {MIN_HISTORY_HEIGHT}px',
                         f'实际 {measured["sessions"]["h"]:.0f}px')
            report.check(measured['titleVisible'], f'{tag} 「历史对话」标题在可视区内')
            report.check(measured['fullyVisibleRows'] >= 1,
                         f'{tag} 至少 1 行会话完整可见',
                         f'实际 {measured["fullyVisibleRows"]} 行')

            # 列表滚到底：标题与搜索框钉在滚动容器之外，必须还在可视区
            client.js("document.querySelector('.sess-body').scrollTop = 99999")
            time.sleep(0.4)
            scrolled = client.js(MEASURE)
            report.check(scrolled['titleVisible'] and scrolled['searchVisible'],
                         f'{tag} 列表滚到底后标题与搜索框仍可见',
                         f'title={scrolled["titleVisible"]} search={scrolled["searchVisible"]}')

            # 侧栏整体滚到底：底部状态区必须够得到（不是被 overflow 裁掉）
            client.js("document.querySelector('.sidenav').scrollTop = 99999")
            time.sleep(0.4)
            foot = client.js("(() => { const f = document.querySelector('.side-foot');"
                             "const b = f.getBoundingClientRect();"
                             "return {bottom: b.bottom, viewport: innerHeight}; })()")
            report.check(foot['bottom'] <= foot['viewport'] + REACH_TOLERANCE,
                         f'{tag} 侧栏滚到底后底部状态区可达',
                         f'底部 {foot["bottom"]:.0f} / 视口 {foot["viewport"]}')
            client.js("document.querySelector('.sess-body').scrollTop = 0;"
                      "document.querySelector('.sidenav').scrollTop = 0")

        client.call('Emulation.setDeviceMetricsOverride',
                    {'width': 1280, 'height': 900, 'deviceScaleFactor': 1, 'mobile': False})
        time.sleep(0.8)

        print('\n[2] ⋯ 菜单：第一行与**列表底部**那行都必须能被真实鼠标点到')
        for label, index_from_end in (('第一行', None), ('列表底部那行', 1)):
            if index_from_end is None:
                spot = client.js('''(() => { const m = document.querySelector('[data-testid="session-menu"]');
                    m.scrollIntoView({block: 'nearest'}); const b = m.getBoundingClientRect();
                    return {x: b.x + b.width/2, y: b.y + b.height/2}; })()''')
            else:
                spot = open_menu_at_row(client, index_from_end)
            client.real_click(spot['x'], spot['y'])
            time.sleep(0.5)
            probe_result = client.js(MENU_PROBE)
            opened = report.check(probe_result['menu'], f'{label} ⋯ 菜单被真实点击打开')
            if opened:
                for action in ('rename', 'remove'):
                    info = probe_result[action]
                    label_cn = '重命名' if action == 'rename' else '删除'
                    report.check(info['exists'] and info['hitIsItem'],
                                 f'{label} 「{label_cn}」菜单项可被命中测试点到',
                                 f'命中到 {info.get("hitTag")}')
                # Esc 收起，不改动任何数据
                client.call('Input.dispatchKeyEvent', {'type': 'keyDown', 'key': 'Escape',
                                                       'code': 'Escape', 'windowsVirtualKeyCode': 27})
                time.sleep(0.3)

        print('\n[3] 折叠开关：必须**真的把空间收回去**，而不只是藏掉内容')
        client.js("document.querySelector('.sess-body').scrollTop = 0;"
                  "document.querySelector('.sidenav').scrollTop = 0")
        time.sleep(0.3)
        # 折叠前先记下几何：折叠后历史区要变矮、下面的导航要上移
        before = client.js(MEASURE)
        before_nav = client.js("Math.round(document.querySelector('.nav').getBoundingClientRect().top)")
        toggle = client.js('''(() => { const t = document.querySelector('[data-testid="history-toggle"]');
            const b = t.getBoundingClientRect(); return {x: b.x + b.width/2, y: b.y + b.height/2}; })()''')
        client.real_click(toggle['x'], toggle['y'])
        time.sleep(0.5)
        collapsed = client.js("""(() => {
          const t = document.querySelector('[data-testid="history-toggle"]');
          const s = document.querySelector('.sessions').getBoundingClientRect();
          return {
            search: !!document.querySelector('[data-testid="session-search"]'),
            rows: document.querySelectorAll('[data-testid="session-row"]').length,
            expanded: t.getAttribute('aria-expanded'),
            height: s.height,
            navTop: Math.round(document.querySelector('.nav').getBoundingClientRect().top),
          };
        })()""")
        report.check(not collapsed['search'] and collapsed['rows'] == 0
                     and collapsed['expanded'] == 'false',
                     '折叠后内容真的收起了（没有搜索框 / 没有会话行 / aria-expanded=false）')
        # 这一条是这次 bug 的核心：内容藏了，但 min-height 还在，216px 空白照样占着
        report.check(collapsed['height'] <= 48,
                     '折叠后历史区高度回落到标题一行（≤48px）',
                     f"实际 {collapsed['height']:.0f}px（展开态 {before['sessions']['h']:.0f}px）")
        report.check(collapsed['navTop'] < before_nav,
                     '折叠后下方导航上移（空间真的被收回）',
                     f"导航 top {before_nav} → {collapsed['navTop']}")
        client.real_click(toggle['x'], toggle['y'])
        time.sleep(0.5)
        restored = client.js("""(() => {
          const s = document.querySelector('.sessions').getBoundingClientRect();
          return {search: !!document.querySelector('[data-testid="session-search"]'),
                  height: s.height,
                  rows: document.querySelectorAll('[data-testid="session-row"]').length};
        })()""")
        report.check(restored['search'] and restored['height'] >= MIN_HISTORY_HEIGHT
                     and restored['rows'] > 0,
                     '再点一次完整恢复（搜索框 / 高度 / 会话行都回来）',
                     f"高度 {restored['height']:.0f}px，{restored['rows']} 行")

        # 折叠是**持久偏好**：刷新后必须还是折叠态，否则用户会觉得「又弹回来了」
        client.real_click(toggle['x'], toggle['y'])
        time.sleep(0.5)
        client.call('Page.enable')
        client.call('Page.navigate', {'url': APP_URL})
        deadline = time.time() + 25
        while time.time() < deadline:
            if client.js("document.querySelectorAll('[data-testid=\"session-row\"]').length > 0"):
                break
            time.sleep(0.4)
        persisted = client.js("""(() => {
          const t = document.querySelector('[data-testid="history-toggle"]');
          const s = document.querySelector('.sessions').getBoundingClientRect();
          return {expanded: t.getAttribute('aria-expanded'), height: s.height,
                  stored: localStorage.getItem('datamind.sessions.collapsed')};
        })()""")
        report.check(persisted['expanded'] == 'false' and persisted['height'] <= 48
                     and persisted['stored'] == '1',
                     '折叠状态刷新后保持（落盘生效，不会自己弹回来）',
                     f"aria-expanded={persisted['expanded']} 高度={persisted['height']:.0f}px "
                     f"localStorage={persisted['stored']}")
        # 收尾：恢复展开，别把「折叠」这个偏好留在验证环境的 profile 里
        restored_toggle = client.js('''(() => { const t = document.querySelector('[data-testid="history-toggle"]');
            const b = t.getBoundingClientRect(); return {x: b.x + b.width/2, y: b.y + b.height/2}; })()''')
        client.real_click(restored_toggle['x'], restored_toggle['y'])
        time.sleep(0.4)
        report.check(client.js("!!document.querySelector('[data-testid=\"session-search\"]')"),
                     '刷新后仍能展开回来')

        print('\n[4] 控制台不得有 JS 异常')
        errors = client.console_errors()
        report.check(not errors, '没有 JS 异常 / console.error',
                     '; '.join(errors[:3]) if errors else '')

        print(f'\n结果: {report.checks - len(report.failures)} pass, {len(report.failures)} fail')
        for failure in report.failures:
            print('  FAIL:', failure)
        return 1 if report.failures else 0
    finally:
        if client and target:
            client.close_target(target['id'])
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
