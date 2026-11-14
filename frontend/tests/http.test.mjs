/**
 * 文件职责：验证公共 HTTP 工具的成功、错误和取消响应。
 * 作用与配合：替换测试进程中的 fetch，直接调用实际 http.ts。
 * 使用位置：由 npm test 执行；使用 Node 自带测试工具。
 * 边界：不访问真实后端，不创建、修改或删除业务数据。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { ApiError, requestJson, requestNoContent } from '../src/shared/api/http.ts'

test('204 空响应成功时，不尝试读取 JSON', async (t) => {
    let jsonReads = 0
    const response = new Response(null, { status: 204 })
    response.json = async () => {
        jsonReads++
        throw new Error('空响应不能读取 JSON')
    }
    t.mock.method(globalThis, 'fetch', async () => response)

    await requestNoContent('/v1/tickets/demo', { method: 'DELETE' })
    assert.equal(jsonReads, 0)
})

test('成功响应返回解析后的业务数据', async (t) => {
    const expected = { id: 'demo', status: 'open' }
    t.mock.method(globalThis, 'fetch', async () => Response.json(expected))
    assert.deepEqual(await requestJson('/v1/tickets/demo'), expected)
})

test('401 保留状态码和后端说明', async (t) => {
    t.mock.method(globalThis, 'fetch', async () =>
        Response.json({ detail: '登录已失效' }, { status: 401 }))

    await assert.rejects(requestJson('/v1/tickets'), (error) => {
        assert.ok(error instanceof ApiError)
        assert.equal(error.status, 401)
        assert.equal(error.message, '登录已失效')
        return true
    })
})

test('非 JSON 的服务错误仍转换为可识别的 ApiError', async (t) => {
    t.mock.method(globalThis, 'fetch', async () =>
        new Response('<html>服务错误</html>', { status: 500 }))

    await assert.rejects(requestJson('/v1/tickets'), (error) => {
        assert.ok(error instanceof ApiError)
        assert.equal(error.status, 500)
        assert.equal(error.message, '请求失败（HTTP 500）')
        return true
    })
})

test('422 的结构化校验信息不会被错误地当作字符串显示', async (t) => {
    t.mock.method(globalThis, 'fetch', async () =>
        Response.json({ detail: [{ msg: '字段无效' }] }, { status: 422 }))

    await assert.rejects(requestJson('/v1/tickets'), {
        name: 'ApiError',
        status: 422,
        message: '请求失败（HTTP 422）',
    })
})

test('写请求网络失败后不自动重试', async (t) => {
    const failure = new TypeError('模拟网络断开')
    const fetchMock = t.mock.method(globalThis, 'fetch', async () => {
        throw failure
    })

    await assert.rejects(
        requestJson('/v1/tickets', { method: 'POST', body: '{}' }),
        (error) => error === failure,
    )
    assert.equal(fetchMock.mock.callCount(), 1)
})

test('调用方取消信号可以传到 fetch，且保留取消错误', async (t) => {
    const controller = new AbortController()
    controller.abort()
    t.mock.method(globalThis, 'fetch', async (_path, options) => {
        options.signal.throwIfAborted()
        throw new Error('已取消请求不应继续')
    })

    await assert.rejects(
        requestJson('/v1/tickets', { signal: controller.signal }),
        { name: 'AbortError' },
    )
})

test('删除接口返回403时，应抛出权限错误', async (t) => {
    // 模拟后端拒绝删除，不访问真实服务器。
    t.mock.method(
        globalThis,
        'fetch',
        async () => Response.json(
            { detail: '无权删除工单' },
            { status: 403 },
        ),
    )

    // 预期请求失败，并检查抛出的错误。
    await assert.rejects(
        requestNoContent('/v1/tickets/demo', {
            method: 'DELETE',
        }),
        {
            name: 'ApiError',
            status: 403,
            message: '无权删除工单',
        },
    )
})