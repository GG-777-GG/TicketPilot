/**
 * 文件职责：定义页面地址、公共布局和登录守卫，按需加载业务页面。
 * 作用与配合：登录页直接加载，工作台页面通过路由 lazy 动态导入。
 * 使用位置：由 App 的 RouterProvider 使用。
 * 边界：只负责页面装配，后端仍独立校验 JWT 与角色。
 */
import {createBrowserRouter, Navigate, Outlet} from 'react-router'

import {WorkspaceLayout} from '../layouts/WorkspaceLayout'
import {AuthLayout} from '../layouts/AuthLayout'
import {LoginPage} from '../features/auth/pages/LoginPage'
import {AuthGuard} from '../features/auth/components/AuthGuard'
import {PageLoading, PageLoadError} from './PageLoadState'

// lazy 的 import() 只在对应路由需要时加载页面代码。
// 创建一次路由配置，放在组件外面，避免重复创建。
export const router = createBrowserRouter([
    {
        // 登录页使用独立布局，与工作台路由并列。
        path: '/login',
        errorElement: <PageLoadError/>,
        element: (
            <AuthLayout>
                {/* 登录页供未登录用户使用，已登录时自动跳转。 */}
                <AuthGuard mode="guest">
                    <LoginPage/>
                </AuthGuard>
            </AuthLayout>
        ),
    },

    {
        // 工作台的根地址。
        path: '/',
        HydrateFallback: PageLoading,
        errorElement: <PageLoadError/>,

        // 工作台页面共用这个布局。
        element: (
            <AuthGuard mode="protected">
                {/* 确认已经登录后，才显示工作台布局及其子页面。 */}
                <WorkspaceLayout>
                    <Outlet/>
                </WorkspaceLayout>
            </AuthGuard>
        ),

        // 属于这个布局的子路由。
        children: [
            {
                // index 表示访问父地址 / 时，默认使用这一项。
                index: true,

                // 自动转到入库页。
                // replace 表示替换当前浏览记录，避免返回键反复回到跳转前。
                element: <Navigate to="/knowledge/ingestion" replace/>,
            },
            {
                // 与父路径组合，完整地址是 /knowledge/ingestion。
                path: 'knowledge/ingestion',

                // 访问这个地址时显示入库页面。
                lazy: async () => ({
                    Component: (await import('../features/ingestion/pages/IngestionPage')).IngestionPage,
                }),
            },
            {
                // :jobId 是动态参数，会被替换成具体的任务 ID。
                path: 'knowledge/ingestion/:jobId',

                // 匹配这个地址时，显示任务详情页面。
                lazy: async () => ({
                    Component: (await import('../features/ingestion/pages/IngestionJobPage')).IngestionJobPage,
                }),
            },
            {
                // 文档列表与入库任务分别使用独立页面。
                path: 'knowledge/documents',
                lazy: async () => ({
                    Component: (await import('../features/knowledge/pages/KnowledgeDocumentsPage')).KnowledgeDocumentsPage,
                }),
            },
            {
                // :documentId 是动态参数，代表准备查看的文档 ID。
                path: 'knowledge/documents/:documentId',

                // 匹配这个地址时显示文档详情页。
                lazy: async () => ({
                    Component: (await import('../features/knowledge/pages/KnowledgeDocumentPage')).KnowledgeDocumentPage,
                }),
            },
            {
                // 同时从地址中取得文档 ID 和历史版本号。
                path: 'knowledge/documents/:documentId/versions/:version',

                // 展示指定历史版本的正文。
                lazy: async () => ({
                    Component: (await import('../features/knowledge/pages/KnowledgeDocumentVersionPage')).KnowledgeDocumentVersionPage,
                }),
            },
            {
                // 与工作台根路径组合，完整地址为 /chat。
                path: 'chat',
                lazy: async () => ({
                    Component: (await import('../features/chat/pages/AgentChatPage')).AgentChatPage,
                }),
            },

            {
                // 复用工作台布局和外层登录守卫。
                path: 'tickets',
                lazy: async () => ({
                    Component: (await import('../features/tickets/pages/TicketsPage')).TicketsPage,
                }),
            },
            {
                // 固定地址，用于填写新工单。
                path: 'tickets/new',
                lazy: async () => ({
                    Component: (await import('../features/tickets/pages/TicketCreatePage')).TicketCreatePage,
                }),
            },
            {
                // 从地址读取工单 ID，独立加载详情。
                path: 'tickets/:ticketId',
                lazy: async () => ({
                    Component: (await import('../features/tickets/pages/TicketPage')).TicketPage,
                }),
            },
            {
                // 其他具体路由没有匹配到时，使用这个页面。
                path: '*',

                lazy: async () => {
                    // router.tsx 与 NotFoundPage.tsx 在同一个目录。
                    const page = await import('./NotFoundPage')

                    // 取出模块导出的组件，交给路由渲染。
                    return {
                        Component: page.NotFoundPage,
                    }
                },
            },
        ],
    },
])
