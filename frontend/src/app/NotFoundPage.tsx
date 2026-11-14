/**
 * 文件职责：展示没有匹配路由的地址，并提供返回入口。
 * 作用与配合：由 router.tsx 的通配路由按需加载。
 * 使用位置：已登录工作台内的未知页面地址。
 * 边界：不查询业务接口，也不修改服务器的 HTTP 状态码。
 */
import { useEffect, useRef } from 'react'
import { Link } from 'react-router'

import styles from './NotFoundPage.module.css'

/** 展示未知地址提示，进入页面时聚焦标题。 */
export function NotFoundPage() {
    const titleRef = useRef<HTMLHeadingElement | null>(null)

    useEffect(() => {
        const previousTitle = document.title
        document.title = '页面未找到 · TicketPilot'
        titleRef.current?.focus()

        return () => {
            document.title = previousTitle
        }
    }, [])

    return (
        <section className={styles.panel}>
            <p className={styles.code}>404</p>

            <h1 ref={titleRef} tabIndex={-1}>
                页面未找到
            </h1>

            <p className={styles.description}>
                这个地址没有对应页面，请检查地址或返回工单列表。
            </p>

            <Link to="/tickets" className={styles.button}>
                返回工单列表
            </Link>
        </section>
    )
}