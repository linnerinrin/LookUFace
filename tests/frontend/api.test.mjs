/**
 * api.js 单元测试（Node 内置 test runner，无需浏览器）
 *
 * 运行：
 *   node --test tests/frontend/
 *
 * 这里用一个最小的 window/document/localStorage/fetch 替身加载 api.js，
 * 以便在纯 Node 环境下验证请求封装、错误语义与安全辅助函数。
 */
import { test, describe, before, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const API_JS = resolve(here, '../../app/static/api.js');

/** 构造一个足够像浏览器的执行环境并载入 api.js。 */
function loadApi({ fetchImpl } = {}) {
  const store = new Map();
  const listeners = new Map();

  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
  };

  const documentStub = {
    body: { appendChild() {}, classList: { toggle() {} } },
    createElement: () => ({
      style: {}, setAttribute() {}, appendChild() {},
    }),
    getElementById: () => null,
    addEventListener() {},
  };

  const windowStub = {
    localStorage,
    fetch: fetchImpl || (() => Promise.reject(new Error('fetch not stubbed'))),
    addEventListener: (type, fn) => {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    dispatchEvent: (event) => {
      (listeners.get(event.type) || []).forEach((fn) => fn(event));
      return true;
    },
    location: { protocol: 'http:', host: 'localhost:8835', pathname: '/static/idx.html' },
    setTimeout,
    clearTimeout,
    StorageEvent: class StorageEvent {
      constructor(type, init) { this.type = type; Object.assign(this, init); }
    },
    CustomEvent: class CustomEvent {
      constructor(type, init) { this.type = type; Object.assign(this, init || {}); }
    },
  };

  const context = {
    window: windowStub,
    document: documentStub,
    localStorage,
    console,
    JSON,
    Object,
    Array,
    String,
    Number,
    Error,
    Promise,
    Symbol,
    setTimeout,
    clearTimeout,
    encodeURIComponent,
    // api.js 在 StorageEvent 不可用时回落到 CustomEvent，必须同时提供
    CustomEvent: windowStub.CustomEvent,
    StorageEvent: windowStub.StorageEvent,
  };
  context.globalThis = context;

  vm.createContext(context);
  vm.runInContext(readFileSync(API_JS, 'utf8'), context, { filename: 'api.js' });

  return { LF: windowStub.LookUFace, windowStub, listeners };
}

/** 构造一个 fetch 替身，按序返回给定响应。 */
function stubFetch(responses) {
  const calls = [];
  const impl = (url, init) => {
    calls.push({ url, init });
    const next = responses.shift() || { status: 200, body: '{}' };
    return Promise.resolve({
      ok: next.status >= 200 && next.status < 300,
      status: next.status,
      text: () => Promise.resolve(next.body ?? ''),
      blob: () => Promise.resolve(new Blob([next.body ?? ''])),
    });
  };
  impl.calls = calls;
  return impl;
}

describe('LookUFace.api', () => {
  test('导出预期的命名空间', () => {
    const { LF } = loadApi();
    assert.ok(LF, 'window.LookUFace 应存在');
    for (const key of ['api', 'auth', 'toast', 'log', 'theme', 'escapeHtml']) {
      assert.ok(LF[key], `LookUFace.${key} 应存在`);
    }
    assert.equal(LF.api.BASE, '/api/v1');
  });

  test('向后兼容 window.API_BASE', () => {
    const { windowStub } = loadApi();
    assert.equal(windowStub.API_BASE, '/api/v1');
  });

  test('GET 自动附加 Authorization 头', async () => {
    const fetchImpl = stubFetch([{ status: 200, body: '{"ok":true}' }]);
    const { LF } = loadApi({ fetchImpl });
    LF.auth.setSession('tok-123', { id: 1, email: 'a@b.c' });

    const data = await LF.api.get('/faces');

    assert.equal(JSON.stringify(data), '{"ok":true}');
    assert.equal(fetchImpl.calls[0].url, '/api/v1/faces');
    assert.equal(fetchImpl.calls[0].init.headers.Authorization, 'Bearer tok-123');
  });

  test('POST 序列化 JSON 并设置 Content-Type', async () => {
    const fetchImpl = stubFetch([{ status: 200, body: '{"success":true}' }]);
    const { LF } = loadApi({ fetchImpl });

    await LF.api.post('/sessions/checkin', { name: '张三' });

    const { init } = fetchImpl.calls[0];
    assert.equal(init.method, 'POST');
    assert.equal(init.headers['Content-Type'], 'application/json');
    assert.equal(init.body, JSON.stringify({ name: '张三' }));
  });

  test('非 2xx 抛出带 status/code/detail 的 ApiError', async () => {
    const fetchImpl = stubFetch([
      { status: 422, body: '{"detail":"请求参数校验失败","code":"validation_error"}' },
    ]);
    const { LF } = loadApi({ fetchImpl });

    await assert.rejects(
      () => LF.api.post('/sessions/checkin', {}),
      (err) => {
        assert.equal(err.name, 'ApiError');
        assert.equal(err.status, 422);
        assert.equal(err.code, 'validation_error');
        assert.equal(err.message, '请求参数校验失败');
        return true;
      },
    );
  });

  test('details 字段被拼进错误信息，便于定位字段', async () => {
    const fetchImpl = stubFetch([
      {
        status: 422,
        body: JSON.stringify({
          detail: '请求参数校验失败',
          code: 'validation_error',
          details: { name: 'field required' },
        }),
      },
    ]);
    const { LF } = loadApi({ fetchImpl });

    await assert.rejects(() => LF.api.get('/faces'), (err) => {
      assert.match(err.message, /name: field required/);
      return true;
    });
  });

  test('401 清空登录态并派发 unauthorized 事件', async () => {
    const fetchImpl = stubFetch([{ status: 401, body: '{"detail":"无效令牌"}' }]);
    const { LF, listeners } = loadApi({ fetchImpl });
    LF.auth.setSession('stale-token', { id: 1 });

    let notified = 0;
    window_listener(listeners, 'lookuface:unauthorized', () => { notified += 1; });

    await assert.rejects(() => LF.api.get('/faces'));
    assert.equal(LF.auth.getToken(), null, '401 后应清空 token');
    assert.equal(notified, 1, '应派发一次 unauthorized 事件');
  });

  test('skipAuthRedirect 时不清空登录态', async () => {
    const fetchImpl = stubFetch([{ status: 401, body: '{"detail":"邮箱或密码错误"}' }]);
    const { LF } = loadApi({ fetchImpl });
    LF.auth.setSession('keep-me', { id: 1 });

    await assert.rejects(() => LF.api.post('/auth/login', {}, { skipAuthRedirect: true }));
    assert.equal(LF.auth.getToken(), 'keep-me');
  });

  test('raw 模式返回 Response 以便读取二进制', async () => {
    const fetchImpl = stubFetch([{ status: 200, body: 'binary' }]);
    const { LF } = loadApi({ fetchImpl });

    const response = await LF.api.post('/faces/screenshot', { name: 'x' }, { raw: true });
    assert.equal(typeof response.blob, 'function');
  });

  test('raw 模式下非 2xx 也抛错', async () => {
    const fetchImpl = stubFetch([{ status: 404, body: '' }]);
    const { LF } = loadApi({ fetchImpl });
    await assert.rejects(
      () => LF.api.post('/faces/screenshot', {}, { raw: true }),
      (err) => err.status === 404,
    );
  });

  test('upload 使用 FormData 且不设置 Content-Type', async () => {
    const fetchImpl = stubFetch([{ status: 200, body: '{"success":true}' }]);
    const { LF } = loadApi({ fetchImpl });
    const form = new FormData();
    form.append('name', '张三');

    await LF.api.upload('/faces/register', form);

    const { init } = fetchImpl.calls[0];
    assert.equal(init.body, form);
    assert.equal(init.headers['Content-Type'], undefined,
      'FormData 不能手动设置 Content-Type，否则 boundary 会丢失');
  });

  test('空响应体解析为空对象而不是抛错', async () => {
    const fetchImpl = stubFetch([{ status: 200, body: '' }]);
    const { LF } = loadApi({ fetchImpl });
    // 注意：VM 里创建的对象原型与外层不同，deepStrictEqual 会因原型不同而失败，
    // 因此用 JSON 比较结构。
    assert.equal(JSON.stringify(await LF.api.get('/version')), '{}');
  });
});

describe('LookUFace.auth', () => {
  test('setSession/getUser/isLoggedIn/clearSession 往返', () => {
    const { LF } = loadApi();
    assert.equal(LF.auth.isLoggedIn(), false);

    LF.auth.setSession('t', { id: 7, email: 'x@y.z' });
    assert.equal(LF.auth.isLoggedIn(), true);
    assert.equal(LF.auth.getUser().id, 7);
    assert.equal(LF.auth.getToken(), 't');

    LF.auth.clearSession();
    assert.equal(LF.auth.isLoggedIn(), false);
    assert.equal(LF.auth.getUser(), null);
  });

  test('displayName 优先昵称，回落邮箱前缀', () => {
    const { LF } = loadApi();
    LF.auth.setSession('t', { id: 1, nickname: '小明', email: 'xm@a.com' });
    assert.equal(LF.auth.displayName(), '小明');

    LF.auth.setSession('t', { id: 1, nickname: null, email: 'xm@a.com' });
    assert.equal(LF.auth.displayName(), 'xm');
  });

  test('损坏的 user JSON 不会抛错', () => {
    const { LF, windowStub } = loadApi();
    windowStub.localStorage.setItem('user', '{not json');
    assert.equal(LF.auth.getUser(), null);
  });
});

describe('LookUFace.escapeHtml', () => {
  test('转义全部 HTML 危险字符（旧版漏掉引号）', () => {
    const { LF } = loadApi();
    assert.equal(
      LF.escapeHtml(`<img src=x onerror="alert(1)">'&`),
      '&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&#39;&amp;',
    );
  });

  test('null / undefined / 数字安全处理', () => {
    const { LF } = loadApi();
    assert.equal(LF.escapeHtml(null), '');
    assert.equal(LF.escapeHtml(undefined), '');
    assert.equal(LF.escapeHtml(42), '42');
  });
});

describe('LookUFace.theme', () => {
  test('toggle 在 dark/light 间切换并持久化', () => {
    const { LF, windowStub } = loadApi();
    assert.equal(LF.theme.current(), 'light');
    assert.equal(LF.theme.toggle(), 'dark');
    assert.equal(windowStub.localStorage.getItem('theme'), 'dark');
    assert.equal(LF.theme.toggle(), 'light');
    assert.equal(windowStub.localStorage.getItem('theme'), 'light');
  });

  test('无法访问 localStorage 时回落 light 而不抛错', () => {
    const { LF, windowStub } = loadApi();
    windowStub.localStorage.getItem = () => { throw new Error('blocked'); };
    assert.equal(LF.theme.current(), 'light');
  });
});

describe('LookUFace.log', () => {
  test('只上报 error/warn，不上报 info（避免日志噪声）', async () => {
    const fetchImpl = stubFetch([{ status: 200, body: '{}' }, { status: 200, body: '{}' }]);
    const { LF } = loadApi({ fetchImpl });

    LF.log.send('info', '调试信息');
    LF.log.error('出错了');
    LF.log.warn('警告');
    await new Promise((r) => setTimeout(r, 10));

    assert.equal(fetchImpl.calls.length, 2, 'info 不应触发上报');
    const bodies = fetchImpl.calls.map((c) => JSON.parse(c.init.body));
    assert.deepEqual(bodies.map((b) => b.level), ['error', 'warn']);
    assert.equal(bodies[0].page, 'idx.html');
  });

  test('超长消息被截断到 2000 字符', async () => {
    const fetchImpl = stubFetch([{ status: 200, body: '{}' }]);
    const { LF } = loadApi({ fetchImpl });

    LF.log.error('x'.repeat(5000));
    await new Promise((r) => setTimeout(r, 10));

    assert.equal(JSON.parse(fetchImpl.calls[0].init.body).message.length, 2000);
  });
});

describe('LookUFace.toast', () => {
  test('重复调用不创建多个节点', () => {
    const created = [];
    const { LF, windowStub } = loadApi();
    // 用一个记录创建次数的 document 替身
    const originalDoc = globalThis.document;
    const container = { children: [], appendChild(node) { this.children.push(node); } };
    const doc = {
      body: container,
      createElement: () => { const el = { style: {}, setAttribute() {} }; created.push(el); return el; },
      getElementById: () => (container.children.length ? container.children[0] : null),
      addEventListener() {},
    };
    // 重新加载以使用新 document
    const store = new Map();
    const ctx = {
      window: { localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
                fetch: () => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{}') }),
                addEventListener() {}, dispatchEvent() {},
                location: { pathname: '/static/idx.html' } },
      document: doc,
      console, JSON, Object, Array, String, Error, Promise, setTimeout, clearTimeout,
      encodeURIComponent,
    };
    ctx.globalThis = ctx;
    vm.createContext(ctx);
    vm.runInContext(readFileSync(API_JS, 'utf8'), ctx, { filename: 'api.js' });

    ctx.window.LookUFace.toast('第一次');
    ctx.window.LookUFace.toast('第二次');
    assert.equal(created.length, 1, '第二个 toast 应复用已有节点');
    assert.equal(created[0].textContent, '第二次');
    void LF; void windowStub; void originalDoc;
  });
});

/** 在给定监听表上注册回调。 */
function window_listener(listeners, type, fn) {
  if (!listeners.has(type)) listeners.set(type, []);
  listeners.get(type).push(fn);
}

describe('网络层失败诊断（Failed to fetch 的归因）', () => {
  test('fetch 网络失败时抛出 NetworkError 而不是裸 TypeError', async () => {
    // 模拟浏览器在无法建立连接时抛出的错误
    const fetchImpl = () => Promise.reject(new TypeError('Failed to fetch'));
    const { LF } = loadApi({ fetchImpl });

    await assert.rejects(
      () => LF.api.post('/auth/login', { email: 'a@b.c', password: 'x' }),
      (err) => {
        assert.equal(err.name, 'NetworkError');
        assert.equal(err.status, 0);
        assert.equal(err.code, 'network_error');
        assert.ok(err.message.includes('无法连接服务器'), `实际信息: ${err.message}`);
        return true;
      },
    );
  });

  test('诊断信息会指出服务不可达、页面来源与访问方式', async () => {
    const fetchImpl = () => Promise.reject(new TypeError('Failed to fetch'));
    const { LF } = loadApi({ fetchImpl });

    await assert.rejects(() => LF.api.post('/auth/send-code', {}), (err) => {
      assert.ok(err.hint, '应有 hint');
      assert.ok(err.hint.includes('/api/v1/health'), '应给出探测地址');
      assert.ok(err.hint.includes('start.bat'), '应提示启动服务');
      assert.ok(err.hint.includes('代理'), '应提示检查代理');
      assert.ok(err.hint.includes('127.0.0.1'), '应提示正确的访问地址');
      return true;
    });
  });

  test('业务错误（ApiError）不会被误判为网络错误', async () => {
    const fetchImpl = stubFetch([
      { status: 401, body: '{"detail":"邮箱或密码错误","code":"unauthorized"}' },
    ]);
    const { LF } = loadApi({ fetchImpl });

    await assert.rejects(
      () => LF.api.post('/auth/login', {}, { skipAuthRedirect: true }),
      (err) => {
        assert.equal(err.name, 'ApiError', '必须保留 ApiError 类型');
        assert.equal(err.status, 401);
        return true;
      },
    );
  });

  test('超时（AbortError）被识别为「连接被挂起」，指向代理问题', async () => {
    const abortError = new Error('The operation was aborted.');
    abortError.name = 'AbortError';
    // 业务请求与探测请求都超时
    const fetchImpl = () => Promise.reject(abortError);
    const { LF } = loadApi({ fetchImpl });

    await assert.rejects(() => LF.api.get('/faces'), (err) => {
      assert.equal(err.name, 'NetworkError');
      assert.ok(err.hint.includes('超时'), `实际: ${err.hint}`);
      assert.ok(err.hint.includes('代理'), '超时应指向代理配置问题');
      return true;
    });
  });

  test('file:// 打开页面时给出针对性提示', async () => {
    const fetchImpl = () => Promise.reject(new TypeError('Failed to fetch'));
    const { LF, windowStub } = loadApi({ fetchImpl });
    windowStub.location.protocol = 'file:';
    windowStub.location.origin = 'null';

    await assert.rejects(() => LF.api.get('/health'), (err) => {
      assert.ok(err.hint.includes('file://'), `实际: ${err.hint}`);
      assert.ok(err.hint.includes('start.bat'));
      return true;
    });
  });

  test('服务可达但返回错误状态时，诊断说明「可达」而非「不可达」', async () => {
    // 业务请求失败，但探测请求返回 200
    let call = 0;
    const fetchImpl = () => {
      call += 1;
      if (call === 1) return Promise.reject(new TypeError('Failed to fetch'));
      return Promise.resolve({
        ok: true, status: 200,
        text: () => Promise.resolve('{}'),
      });
    };
    const { LF } = loadApi({ fetchImpl });

    await assert.rejects(() => LF.api.get('/faces'), (err) => {
      assert.ok(err.hint.includes('可达'), `实际: ${err.hint}`);
      return true;
    });
  });

  test('probe() 可单独调用（供「重新检测」按钮使用）', async () => {
    const fetchImpl = () => Promise.resolve({
      ok: true, status: 200, text: () => Promise.resolve('{}'),
    });
    const { LF } = loadApi({ fetchImpl });

    const text = await LF.api.probe();
    assert.ok(typeof text === 'string');
    assert.ok(text.includes('可达'), `实际: ${text}`);
  });

  test('NetworkError 与 ApiError 都被导出', () => {
    const { LF } = loadApi();
    assert.equal(typeof LF.api.NetworkError, 'function');
    assert.equal(typeof LF.api.ApiError, 'function');
    assert.equal(typeof LF.api.showConnectivityBanner, 'function');
    assert.equal(typeof LF.api.hideConnectivityBanner, 'function');
  });
});

describe('toast 对长诊断信息的处理', () => {
  test('保留换行并延长显示时间', () => {
    const created = [];
    const container = { children: [], appendChild(n) { this.children.push(n); } };
    const doc = {
      body: container,
      createElement: () => {
        const el = { style: {}, setAttribute() {}, appendChild() {} };
        created.push(el);
        return el;
      },
      getElementById: () => (container.children.length ? container.children[0] : null),
      addEventListener() {},
    };
    const ctx = {
      window: {
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        fetch: () => Promise.resolve({ ok: true, status: 200, text: () => Promise.resolve('{}') }),
        addEventListener() {}, dispatchEvent() {},
        location: { pathname: '/static/idx.html', protocol: 'http:', origin: 'http://x' },
      },
      document: doc,
      console, JSON, Object, Array, String, Error, Promise, Symbol,
      setTimeout, clearTimeout, encodeURIComponent,
      CustomEvent: class {}, StorageEvent: class {},
    };
    ctx.globalThis = ctx;
    vm.createContext(ctx);
    vm.runInContext(readFileSync(API_JS, 'utf8'), ctx, { filename: 'api.js' });

    const longText = '无法连接服务器\n第二行\n第三行';
    ctx.window.LookUFace.toast(longText, true);

    assert.equal(created.length, 1);
    assert.equal(created[0].textContent, longText, '换行必须保留');
    assert.ok(
      created[0].style.cssText.includes('pre-line'),
      '必须设置 white-space:pre-line，否则换行会被折叠',
    );
  });
});
