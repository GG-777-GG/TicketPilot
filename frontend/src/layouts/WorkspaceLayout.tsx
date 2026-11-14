/**
 * 文件职责：提供工作台的导航、顶栏、当前用户名、退出按钮和正文容器。
 * 作用与配合：通过 useAuth 读取身份和退出方法，通过 children 显示匹配的业务页面。
 * 使用位置：由 router.tsx 放在受 AuthGuard 保护的工作台路由内。
 * 边界：不查询入库任务或处理上传，业务内容由各 feature 组件负责。
 */
// ReactNode 表示可以显示的内容，例如标签、文字、其他组件。
// import type 表示这里只导入 TypeScript 类型。
import type {ReactNode} from 'react'
import {Fragment} from 'react'

// 读取当前用户和退出登录方法。
import {useAuth} from '../features/auth/hooks/useAuth'

// 导入当前组件专用的样式。
import styles from './WorkspaceLayout.module.css'

// NavLink 用于站内页面切换，并自动标记当前页面。
import {NavLink, useLocation, useNavigation} from 'react-router'

// 定义这个组件接收的参数。
type WorkspaceLayoutProps = {
    // 调用方放在组件内部的页面内容。
    children: ReactNode
}

/** 提供工作台公共布局，children 显示在右侧主内容区。 */
export function WorkspaceLayout({
                                    children,
                                }: WorkspaceLayoutProps) {
    // session 保存已经确认的用户身份。
    // signOut 负责清除当前会话和浏览器中的令牌。
    const {session, signOut} = useAuth()
    const navigation = useNavigation()
    const {pathname} = useLocation()

    // 没有会话时，不显示工作台内容。
    // 外层 AuthGuard 会根据登录状态处理跳转或错误提示。
    if (!session) return null

    return (
        <div className={styles.shell}>
            {/* 左侧导航区域。 */}
            <aside className={styles.sidebar}>
                <p className={styles.brand}>TicketPilot</p>
                <p className={styles.subtitle}>企业知识工作台</p>

                <nav
                    aria-label="主导航"
                    className={styles.navigation}
                >
                    <NavLink to="/knowledge/ingestion">
                        知识入库
                    </NavLink>

                    {/* 所有已登录角色都可以进入问答页。 */}
                    <NavLink to="/chat">
                        Agent 问答
                    </NavLink>
                    {/* 所有已登录角色均可查看工单列表。 */}
                    <NavLink to="/tickets">
                        工单列表
                    </NavLink>

                    {/* 文档管理入口仅对主管和管理员显示。 */}
                    {(session.user.role === 'admin' ||
                        session.user.role === 'supervisor') && (
                        <NavLink to="/knowledge/documents">
                            知识文档
                        </NavLink>
                    )}
                </nav>
            </aside>

            {/* 右侧工作区域。 */}
            <div className={styles.workspace}>
                <header className={styles.header}>
                    <span>企业知识工作台</span>
                    <span role="status">{navigation.state === 'loading' ? '正在加载页面……' : ''}</span>

                    {/* 当前用户信息和退出操作。 */}
                    <div className={styles.account}>
            <span className={styles.userName}>
              {session.user.display_name}
            </span>

                        <button
                            type="button"
                            className={styles.signOutButton}
                            onClick={signOut}
                        >
                            退出登录
                        </button>
                    </div>
                </header>
                {/* children 会显示在这里。 */}
                <main id="main-content" className={styles.content}>
                    {/* 切换页面、业务对象或账号时重建内容；普通数据刷新保留草稿。 */}
                    <Fragment key={JSON.stringify([pathname, session.user.id, session.user.role])}>
                        {children}
                    </Fragment>
                </main>
            </div>
        </div>
    )
}
