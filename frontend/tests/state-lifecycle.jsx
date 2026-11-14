/** 使用真实 React、认证容器、工作台和保存 Hook，所有业务请求均为模拟。 */
import {useEffect, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient, QueryClientProvider} from '@tanstack/react-query'
import {createMemoryRouter, RouterProvider, useParams} from 'react-router'
import {AuthProvider} from '../src/features/auth/AuthProvider'
import {AuthGuard} from '../src/features/auth/components/AuthGuard'
import {useAuth} from '../src/features/auth/hooks/useAuth'
import {WorkspaceLayout} from '../src/layouts/WorkspaceLayout'
import {useUpdateTicketStatus} from '../src/features/tickets/hooks/useUpdateTicketStatus'
import {TicketStatusEditor} from '../src/features/tickets/components/TicketStatusEditor'

const originalFetch = window.fetch.bind(window)
const tokenKey = 'ticketpilot.access_token'
const user = {id: 'test-user', email: 'test@example.com', display_name: '测试账号',
    role: 'admin', is_active: true, created_at: '2026-11-08T00:00:00Z'}
const json = (value, status = 200) => new Response(JSON.stringify(value), {
    status, headers: {'Content-Type': 'application/json'},
})
let auth
let saveStatus
let meCalls
let meHandler
let loginHandler
let patchHandler
let root
let router
const results = []

function assert(condition, message) {
    if (!condition) throw new Error(message)
}

async function waitFor(condition) {
    const deadline = Date.now() + 5_000
    while (!condition()) {
        if (Date.now() > deadline) throw new Error('等待界面状态超时')
        await new Promise(resolve => setTimeout(resolve, 10))
    }
}

function deferred() {
    let resolve
    const promise = new Promise(done => { resolve = done })
    return {promise, resolve}
}

window.fetch = async (input, options = {}) => {
    const url = new URL(String(input), location.origin)
    if (url.pathname === '/v1/auth/me') {
        meCalls += 1
        return meHandler(options)
    }
    if (url.pathname === '/v1/auth/login') return loginHandler(options)
    if (/^\/v1\/tickets\/[^/]+\/status$/.test(url.pathname)) return patchHandler(options)
    throw new Error('测试出现未模拟的请求：' + url.pathname)
}

export function AuthProbe() {
    const value = useAuth()
    useEffect(() => { auth = value }, [value])
    return null
}

export function PageProbe() {
    const {ticketId} = useParams()
    const [draft, setDraft] = useState('')
    const [currentStatus, setCurrentStatus] = useState('open')
    const operation = useUpdateTicketStatus(ticketId)
    useEffect(() => { saveStatus = operation.updateStatus }, [operation.updateStatus])
    return <>
        <p id="ticket-id">{ticketId}</p>
        <p id="draft">{draft}</p>
        <p id="saving">{String(operation.isSaving)}</p>
        <p id="message">{operation.saveMessage}</p>
        <button id="write-draft" onClick={() => setDraft('临时草稿')}>输入草稿</button>
        <button id="show-closed" onClick={() => setCurrentStatus('closed')}>模拟已关闭工单</button>
        <TicketStatusEditor currentStatus={currentStatus} canEdit canReload
            busy={operation.isSaving} onSave={operation.updateStatus} onReload={async () => true}/>
    </>
}

async function mount(savedToken = null) {
    auth = undefined
    saveStatus = undefined
    meCalls = 0
    meHandler = () => json(user)
    loginHandler = () => json({access_token: 'test-token', token_type: 'bearer'})
    patchHandler = () => json({id: 'A', status: 'resolved'})
    sessionStorage.removeItem(tokenKey)
    if (savedToken) sessionStorage.setItem(tokenKey, savedToken)
    router = createMemoryRouter([{
        path: '/tickets/:ticketId',
        element: <AuthGuard mode="protected"><WorkspaceLayout><PageProbe/></WorkspaceLayout></AuthGuard>,
    }, {path: '/login', element: <p>登录页</p>}], {initialEntries: ['/tickets/A']})
    root = createRoot(document.getElementById('root'))
    root.render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}>
        <AuthProvider><AuthProbe/><RouterProvider router={router}/></AuthProvider>
    </QueryClientProvider>)
    // 调用方可在首次 Effect 运行前设置本场景的网络响应。
}

async function scenario(name, run) {
    try {
        await run()
        results.push({name, passed: true})
    } catch (error) {
        results.push({name, passed: false, error: error.message})
    } finally {
        root?.unmount()
        router?.dispose()
        await new Promise(resolve => setTimeout(resolve, 0))
    }
}

await scenario('没有令牌时直接进入未登录状态，不请求身份接口', async () => {
    await mount()
    await waitFor(() => auth?.status === 'anonymous')
    assert(meCalls === 0, '无令牌时发出了身份请求')
})

await scenario('已有有效令牌时恢复身份', async () => {
    await mount('test-token')
    await waitFor(() => auth?.status === 'authenticated')
    assert(auth.session.user.id === user.id, '身份没有正确恢复')
})

await scenario('失效令牌被清除', async () => {
    await mount('invalid-token')
    meHandler = () => json({detail: '登录已失效'}, 401)
    await waitFor(() => auth?.status === 'anonymous')
    assert(sessionStorage.getItem(tokenKey) === null, '无效令牌没有清除')
})

await scenario('网络失败保留令牌，点击重试后可以恢复', async () => {
    await mount('test-token')
    meHandler = () => { throw new TypeError('模拟网络故障') }
    await waitFor(() => auth?.status === 'error')
    assert(sessionStorage.getItem(tokenKey) === 'test-token', '网络失败清除了令牌')
    meHandler = () => json(user)
    auth.retryRestore()
    await waitFor(() => auth?.status === 'authenticated')
})

await scenario('浏览器存储不可读时显示错误，恢复存储后可重试', async () => {
    await mount('test-token')
    const originalGetItem = Storage.prototype.getItem
    Storage.prototype.getItem = () => { throw new Error('模拟存储不可用') }
    try {
        await waitFor(() => auth?.status === 'error')
        assert(meCalls === 0, '存储不可读时不应发送身份请求')
    } finally {
        Storage.prototype.getItem = originalGetItem
    }
    auth.retryRestore()
    await waitFor(() => auth?.status === 'authenticated')
})

await scenario('恢复身份期间退出，迟到的响应不能重新登录', async () => {
    const pending = deferred()
    await mount('test-token')
    meHandler = () => pending.promise
    await waitFor(() => auth?.status === 'checking')
    auth.signOut()
    pending.resolve(json(user))
    await waitFor(() => auth?.status === 'anonymous')
    await new Promise(resolve => setTimeout(resolve, 30))
    assert(auth.status === 'anonymous' && !auth.session, '旧响应覆盖了退出状态')
})

await scenario('普通页面更新保留草稿，切换工单清理草稿和旧保存请求', async () => {
    await mount('test-token')
    await waitFor(() => document.getElementById('write-draft'))
    document.getElementById('write-draft').click()
    await waitFor(() => document.getElementById('draft').textContent === '临时草稿')
    await router.navigate('/tickets/A?refresh=1')
    assert(document.getElementById('draft').textContent === '临时草稿', '普通页面更新清空了草稿')

    const pending = deferred()
    let signal
    patchHandler = options => { signal = options.signal; return pending.promise }
    const operation = saveStatus('resolved')
    await waitFor(() => signal && document.getElementById('saving').textContent === 'true')
    await router.navigate('/tickets/B')
    await waitFor(() => document.getElementById('ticket-id')?.textContent === 'B')
    assert(signal.aborted, '切换工单后没有取消旧请求')
    assert(document.getElementById('draft').textContent === '', '新工单沿用了旧草稿')
    assert(document.getElementById('saving').textContent === 'false', '新工单沿用了旧等待状态')
    pending.resolve(json({id: 'A', status: 'resolved'}))
    assert(await operation === false, '迟到的保存结果未被忽略')
    assert(document.getElementById('message').textContent === '', '旧保存结果出现在新工单')
})

await scenario('切换账号不会沿用旧页面草稿', async () => {
    await mount('test-token')
    await waitFor(() => document.getElementById('write-draft'))
    document.getElementById('write-draft').click()
    await waitFor(() => document.getElementById('draft').textContent === '临时草稿')
    auth.signOut()
    await waitFor(() => auth.status === 'anonymous')
    meHandler = () => json({...user, id: 'another-user', role: 'agent'})
    await auth.signIn({email: 'another@example.com', password: 'test-only'})
    await waitFor(() => auth.session?.user.id === 'another-user')
    await router.navigate('/tickets/A')
    await waitFor(() => document.getElementById('draft'))
    assert(document.getElementById('draft').textContent === '', '新账号沿用了旧草稿')
})

await scenario('登录期间退出，迟到的登录响应不能恢复会话', async () => {
    await mount()
    await waitFor(() => auth?.status === 'anonymous')
    const pending = deferred()
    loginHandler = () => pending.promise
    const login = auth.signIn({email: 'test@example.com', password: 'test-only'}).catch(error => error)
    auth.signOut()
    pending.resolve(json({access_token: 'test-token', token_type: 'bearer'}))
    const error = await login
    assert(error.name === 'AbortError', '旧登录请求没有被取消')
    await waitFor(() => auth.status === 'anonymous')
    assert(sessionStorage.getItem(tokenKey) === null, '退出后写入了旧令牌')
})

await scenario('关闭操作在 Hook 内拦截，服务端冲突提示保留，普通状态仍能保存', async () => {
    await mount('test-token')
    await waitFor(() => saveStatus)
    let calls = 0
    patchHandler = () => { calls += 1; return json({detail: '需审批'}, 409) }
    assert(await saveStatus('closed') === false, 'Hook 允许直接关闭工单')
    await waitFor(() => document.getElementById('message').textContent.includes('审批'))
    assert(calls === 0, '直接关闭仍发送了 HTTP 请求')
    assert(await saveStatus('resolved') === false, '冲突响应被误判成功')
    await waitFor(() => document.getElementById('message').textContent.includes('当前操作不符合工单规则'))
    patchHandler = () => { calls += 1; return json({id: 'A', status: 'resolved'}) }
    assert(await saveStatus('resolved') === true, '普通状态无法保存')
    assert(calls === 2, '请求数量不符')
})

await scenario('状态编辑器禁用关闭选项，仍显示已关闭工单并允许保存其他状态', async () => {
    await mount('test-token')
    await waitFor(() => document.getElementById('ticket-status-select'))
    const select = document.getElementById('ticket-status-select')
    assert(select.querySelector('option[value="closed"]').disabled, '关闭选项未禁用')
    document.getElementById('show-closed').click()
    await waitFor(() => select.value === 'closed')
    let submitted
    patchHandler = options => { submitted = JSON.parse(options.body).status; return json({id: 'A', status: 'resolved'}) }
    select.value = 'resolved'
    select.dispatchEvent(new Event('change', {bubbles: true}))
    const saveButton = [...document.querySelectorAll('button')].find(button => button.textContent === '保存状态')
    await waitFor(() => saveButton.getAttribute('aria-disabled') === 'false')
    saveButton.click()
    await waitFor(() => document.getElementById('message').textContent === '状态已保存。')
    assert(submitted === 'resolved', '普通状态没有正确提交')
})

await originalFetch('/__state_lifecycle_result', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(results),
})
