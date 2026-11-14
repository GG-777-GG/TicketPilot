/**
 * 文件职责：验证工单缓存刷新范围和账号、角色隔离。
 * 作用与配合：使用真实 QueryClient，调用实际 cache.ts。
 * 使用位置：由 npm test 执行。
 * 边界：只操作内存缓存，没有查询函数，不发送网络请求。
 */
import assert from 'node:assert/strict'
import test from 'node:test'
import { QueryClient } from '@tanstack/react-query'
import { invalidateTicketQueries } from '../src/features/tickets/cache.ts'

// 不同分页、详情、账号、角色和业务域同时存在时，才能检验隔离。
const keys = {
    firstPage: ['tickets', 'actor-a', 'admin', { limit: 11, offset: 0 }],
    secondPage: ['tickets', 'actor-a', 'admin', { limit: 11, offset: 10 }],
    target: ['ticket-detail', 'actor-a', 'admin', 'ticket-1'],
    otherDetail: ['ticket-detail', 'actor-a', 'admin', 'ticket-2'],
    otherAccount: ['tickets', 'actor-b', 'admin'],
    otherRole: ['tickets', 'actor-a', 'agent'],
    otherFeature: ['knowledge-documents', 'actor-a', 'admin'],
}

/** 每个测试独立创建缓存，并在结束后清除计时器与数据。 */
function createFixture(t) {
    const client = new QueryClient()
    for (const key of Object.values(keys)) client.setQueryData(key, { example: true })
    t.after(() => client.clear())
    return client
}

/** 逐项检查应该失效和应该保留的缓存。 */
function assertInvalidated(client, expectedNames) {
    for (const [name, key] of Object.entries(keys)) {
        assert.equal(client.getQueryState(key)?.isInvalidated, expectedNames.includes(name), name)
    }
}

test('创建后只刷新操作者的所有列表分页', async (t) => {
    const client = createFixture(t)
    await invalidateTicketQueries(client, 'actor-a', 'admin')
    assertInvalidated(client, ['firstPage', 'secondPage'])
})

test('状态和处理人修改可刷新操作者的列表及全部工单详情', async (t) => {
    const client = createFixture(t)
    await invalidateTicketQueries(client, 'actor-a', 'admin', 'all')
    assertInvalidated(client, ['firstPage', 'secondPage', 'target', 'otherDetail'])
})

test('删除只刷新列表及目标详情，保留其他详情和其他身份缓存', async (t) => {
    const client = createFixture(t)
    await invalidateTicketQueries(client, 'actor-a', 'admin', { ticketId: 'ticket-1' })
    assertInvalidated(client, ['firstPage', 'secondPage', 'target'])
})

test('缓存刷新失败不会把已确认的业务写操作改判为失败', async () => {
    const client = {
        invalidateQueries: async () => { throw new Error('模拟刷新失败') },
    }
    await assert.doesNotReject(invalidateTicketQueries(client, 'actor-a', 'admin'))
})
