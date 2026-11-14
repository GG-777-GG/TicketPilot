/**
 * 文件职责：检查本机生产预览的页面入口、静态资源和后端代理。
 * 作用与配合：读取 dist，与已启动的 Vite preview 返回内容逐项比较。
 * 使用位置：先构建并启动预览，再运行 npm run check:preview。
 * 边界：只发送本机 GET 请求，不执行浏览器渲染或业务写操作。
 */
import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'

const base = new URL(process.argv[2] ?? 'http://127.0.0.1:4173/')
assert.ok(base.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname),
    '该脚本只检查本机 HTTP 预览地址')
assert.equal(base.pathname, '/', '预览地址应使用根路径')

const dist = new URL('../dist/', import.meta.url)
const entry = await readFile(new URL('index.html', dist))

/** path 是本机请求路径；expectedStatus 是此请求预期的 HTTP 状态码。 */
async function get(path, expectedStatus = 200) {
    const response = await fetch(new URL(path, base), {
        signal: AbortSignal.timeout(5_000),
        cache: 'no-store',
    })
    assert.equal(response.status, expectedStatus, path + ' HTTP 状态不符')
    return response
}

const routes = [
    '/login', '/knowledge/ingestion', '/knowledge/ingestion/demo',
    '/knowledge/documents', '/knowledge/documents/demo',
    '/knowledge/documents/demo/versions/1', '/chat',
    '/tickets', '/tickets/new', '/tickets/demo',
]

// 深层地址应回退到应用入口，之后才由 React Router 选择实际页面。
for (const path of ['/', ...routes]) {
    const response = await get(path)
    assert.deepEqual(Buffer.from(await response.arrayBuffer()), entry, path + ' 未返回构建入口')
}

const resources = new Set()
for (const file of await readdir(new URL('assets/', dist), { withFileTypes: true })) {
    if (file.isFile()) resources.add('/assets/' + encodeURIComponent(file.name))
}
const buildAssetCount = resources.size

// 入口引用也必须存在，防止 HTML 指向旧文件名或遗漏 favicon。
for (const match of entry.toString('utf8').matchAll(/(?:src|href)="([^"]+)"/g)) {
    const url = new URL(match[1], base)
    if (url.origin === base.origin) resources.add(url.pathname)
}

for (const path of resources) {
    const file = new URL(path.slice(1), dist)
    assert.ok(file.href.startsWith(dist.href), '资源路径超出 dist')
    const expected = await readFile(file)
    const response = await get(path)
    const type = response.headers.get('content-type') ?? ''
    if (path.endsWith('.js')) assert.match(type, /javascript/, path + ' 脚本类型错误')
    if (path.endsWith('.css')) assert.match(type, /text\/css/, path + ' 样式类型错误')
    if (path.endsWith('.svg')) assert.match(type, /image\/svg\+xml/, path + ' 图片类型错误')
    // 比较文件内容，避免缺失资源被 SPA 的 200 HTML 回退掩盖。
    assert.deepEqual(Buffer.from(await response.arrayBuffer()), expected, path + ' 内容不符')
}

const proxy = await get('/v1/tickets', 401)
assert.ok(proxy.headers.get('content-type')?.includes('application/json'), '代理未返回 JSON')
assert.equal(typeof (await proxy.json()).detail, 'string', '代理未返回认证错误说明')

console.log(JSON.stringify({
    preview: base.origin,
    pageEntries: routes.length,
    buildAssets: buildAssetCount,
    checkedResources: resources.size,
    proxyStatus: 401,
    businessWrites: 0,
    browserRenderingVerified: false,
}, null, 2))
