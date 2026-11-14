/** 启动隔离的本机测试页，用系统浏览器无头运行；不访问真实业务后端。 */
import assert from 'node:assert/strict'
import {execFileSync, spawn} from 'node:child_process'
import {existsSync} from 'node:fs'
import {mkdtemp, rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import {basename, dirname, join, resolve} from 'node:path'
import {fileURLToPath} from 'node:url'
import {createServer} from 'vite'
import react from '@vitejs/plugin-react'

const browserPath = [process.env.TICKETPILOT_TEST_BROWSER,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].find(candidate => candidate && existsSync(candidate))
assert.ok(browserPath, '需要 Edge/Chrome，或通过 TICKETPILOT_TEST_BROWSER 指定浏览器路径')

const profile = await mkdtemp(join(tmpdir(), 'ticketpilot-state-test-'))
let resolveReport
let rejectReport
const report = new Promise((accept, reject) => {
    resolveReport = accept
    rejectReport = reject
})
const server = await createServer({
    root: fileURLToPath(new URL('../', import.meta.url)),
    configFile: false,
    plugins: [react(), {
        name: 'state-lifecycle-report',
        configureServer(testServer) {
            // 在 Vite 的 HTML 回退和 404 中间件之前接收测试结果。
            testServer.middlewares.use('/__state_lifecycle_result', (request, response) => {
                let content = ''
                request.on('data', chunk => { content += chunk })
                request.on('end', () => {
                    try {
                        const results = JSON.parse(content)
                        response.end('ok')
                        resolveReport(results)
                    } catch (error) {
                        response.statusCode = 400
                        response.end('invalid report')
                        rejectReport(error)
                    }
                })
            })
        },
    }],
    server: {host: '127.0.0.1', port: 0, strictPort: true, hmr: false, watch: null},
})
let browser
let deadline
try {
    deadline = setTimeout(() => rejectReport(new Error('浏览器状态回归超时')), 45_000)
    await server.listen()
    const address = server.httpServer.address()
    browser = spawn(browserPath, [
        '--headless', '--disable-gpu', '--no-first-run', '--disable-extensions',
        '--disable-background-networking', '--user-data-dir=' + profile,
        `http://127.0.0.1:${address.port}/tests/state-lifecycle.html`,
    ], {stdio: 'ignore', windowsHide: true})
    browser.on('error', rejectReport)
    const results = await report
    for (const result of results) {
        console.log((result.passed ? 'PASS ' : 'FAIL ') + result.name + (result.error ? ': ' + result.error : ''))
    }
    assert.equal(results.length, 11, '浏览器检查数量不符')
    assert.ok(results.every(result => result.passed), '浏览器状态回归存在失败')
    console.log('11 项浏览器状态回归通过；全部业务接口使用模拟响应。')
} finally {
    clearTimeout(deadline)
    if (browser && browser.exitCode === null) {
        const closed = new Promise(done => browser.once('exit', done))
        if (process.platform === 'win32') {
            // 浏览器包含多个子进程，只结束本次启动的浏览器进程树。
            execFileSync('taskkill', ['/PID', String(browser.pid), '/T', '/F'], {stdio: 'ignore', windowsHide: true})
        } else {
            browser.kill()
        }
        await closed
    }
    await server.close()
    // 仅删除本次创建、位于系统临时目录的隔离浏览器配置。
    assert.equal(dirname(resolve(profile)), resolve(tmpdir()))
    assert.ok(basename(profile).startsWith('ticketpilot-state-test-'))
    await rm(profile, {recursive: true, force: true, maxRetries: 20, retryDelay: 100})
}
