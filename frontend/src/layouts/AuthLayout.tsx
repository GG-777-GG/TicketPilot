/**
 * 文件职责：提供登录区域的品牌介绍和表单容器布局。
 * 作用与配合：接收 children，把登录表单或身份检查提示放入右侧内容区。
 * 使用位置：由 router.tsx 为 /login 页面装配，样式来自同名 CSS Module。
 * 边界：只负责布局，不验证密码、保存令牌或决定登录跳转。
 */
import type {ReactNode} from 'react'
import styles from './AuthLayout.module.css'

// 登录布局接收的参数。
type AuthLayoutProps = {
    // 放在右侧的页面内容，后面会传入登录表单。
    children: ReactNode
}

/** 提供登录页面的品牌介绍区和表单容器。 */
export function AuthLayout({
                               children,
                           }: AuthLayoutProps) {
    return (
        <div className={styles.shell}>
            {/* 左侧品牌介绍。 */}
            <aside className={styles.brandPanel}>
                <p className={styles.brand}>TicketPilot</p>

                <div className={styles.message}>
                    <p className={styles.eyebrow}>企业知识工作台</p>

                    <h1 className={styles.headline}>
                        让知识，
                        <br/>
                        有据可查。
                    </h1>

                    <p className={styles.description}>
                        从文档上传到知识检索，
                        <br/>
                        跟进知识入库的每一步。
                    </p>
                </div>

                <p className={styles.caption}>
                    文档入库 · OCR 识别 · 来源追溯
                </p>
            </aside>

            {/* 右侧内容区域，children 后面会是登录表单。 */}
            <main className={styles.main}>
                <div className={styles.formContainer}>
                    {children}
                </div>
            </main>
        </div>
    )
}
