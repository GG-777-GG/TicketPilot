/** 文件职责：展示登录表单，校验输入并反馈提交状态和错误信息。
 * 作用与配合：通过 useAuth 调用 signIn，取消离开页面时的请求，并使用局部 CSS 展示界面。
 * 使用位置：应用的 /login 路由，由访客模式的 AuthGuard 包裹。
 * 边界：只负责登录交互；身份确认、令牌保存和共享会话由 AuthProvider 管理，跳转由守卫处理。
 */
import styles from './LoginPage.module.css'

// React 提供的状态、引用和生命周期工具。
import { useEffect, useRef, useState } from 'react'

// 表单提交事件的类型。
import type { SubmitEvent } from 'react'

// 公共 HTTP 错误类型。
import { ApiError } from '../../../shared/api/http'

// 刚才编写的登录接口函数。
// 读取 AuthProvider 提供的登录操作。
import { useAuth } from '../hooks/useAuth'

export function LoginPage() {
    // 从共享登录状态中取出 signIn 方法。
    // 必须放在组件函数内部、顶层，不能放进 handleSubmit。
    const {signIn} = useAuth()

    /** 显示登录表单，后面接入登录请求和状态反馈。 */
    {
        // 当前状态：未提交、提交中、成功、失败。
        const [status, setStatus] =
            useState<'idle' | 'submitting' | 'success' | 'error'>('idle')

        // 页面上显示的提示文字。
        const [message, setMessage] = useState('请输入已注册的账号信息。')

        // 保存当前请求的控制器，也用于立即阻止重复提交。
        const requestRef = useRef<AbortController | null>(null)

        // 根据状态判断是否正在提交。
        const isSubmitting = status === 'submitting'

        // 返回的函数是清理函数：组件离开页面时，取消仍在等待的请求。
        useEffect(() => {
            return () => {
                // ?. 表示存在控制器时，才调用 abort()。
                requestRef.current?.abort()
                requestRef.current = null
            }
        }, [])

        /**
         * 处理登录表单提交。
         * event：本次提交事件，currentTarget 就是绑定事件的表单。
         * * 读取表单，调用共享登录操作，并显示提交结果。
         * * 令牌保存和身份确认由 AuthProvider 负责。
         */
        async function handleSubmit(
            event: SubmitEvent<HTMLFormElement>,
        ): Promise<void> {
            // 阻止浏览器默认提交，避免刷新页面。
            event.preventDefault()

            // 已有请求在处理时，不再发送第二次。
            if (requestRef.current) return

            // FormData 是浏览器提供的工具，按输入框的 name 读取数据。
            // 必须在 await 前读取，不能等请求结束后再取事件中的表单。
            const formData = new FormData(event.currentTarget)
            const email = formData.get('email')
            const password = formData.get('password')

            // get() 不保证一定返回字符串，因此先检查类型和空值。
            if (
                typeof email !== 'string' ||
                typeof password !== 'string' ||
                !email.trim() ||
                !password
            ) {
                setStatus('error')
                setMessage('请填写邮箱和密码。')
                return
            }

            // 创建本次请求的取消控制器，并记录“已有请求”。
            const controller = new AbortController()
            requestRef.current = controller

            setStatus('submitting')
            setMessage('正在验证账号，请稍候。')

            try {
                // 执行完整登录流程：
                // 请求令牌 → 查询当前用户 → 保存令牌 → 更新共享状态。
                await signIn(
                    {
                        // 去掉邮箱两端的空白。
                        email: email.trim(),

                        // 密码可能包含空格，因此保留用户的原始输入。
                        password,
                    },

                    // 页面离开时，可以取消本次登录等待。
                    controller.signal,
                )

                // 页面已经离开，就不再更新页面提示。
                if (controller.signal.aborted) return

                // signIn 正常完成，表示会话已建立。
                setStatus('success')
                setMessage('登录成功，身份已确认。')
            } catch (error: unknown) {
                // 页面或 AuthProvider 都可能主动取消请求。
                // 主动取消不作为登录失败展示。
                if (
                    controller.signal.aborted ||
                    (
                        error instanceof DOMException &&
                        error.name === 'AbortError'
                    )
                ) {
                    return
                }

                setStatus('error')

                // instanceof 判断错误属于哪种类型。
                if (error instanceof ApiError) {
                    setMessage(error.message)
                } else if (
                    error instanceof DOMException &&
                    error.name === 'TimeoutError'
                ) {
                    setMessage('登录请求超时，请稍后重试。')
                } else {
                    setMessage('暂时无法登录，请检查后端服务或网络。')
                }
            } finally {
                // 成功或失败后都释放请求，允许再次提交。
                // 不修改 status，以免覆盖刚设置的成功或失败状态。
                if (requestRef.current === controller) {
                    requestRef.current = null
                }
            }
        }

        return (
            <section aria-labelledby="login-title">
                <h2 id="login-title" className={styles.title}>
                    欢迎回来
                </h2>

                <p className={styles.description}>
                    使用你的企业账号登录 TicketPilot。
                </p>

                {/* 暂时阻止浏览器默认提交，下一步在这里发送登录请求。 */}
                <form
                    className={styles.form}
                    onSubmit={handleSubmit}
                    aria-busy={isSubmitting}
                >
                    <div className={styles.field}>
                        {/* htmlFor 对应输入框的 id，点击标签也能选中输入框。 */}
                        <label htmlFor="login-email">邮箱地址</label>

                        <input
                            id="login-email"
                            name="email"
                            type="email"
                            autoComplete="username"
                            placeholder="请输入注册时的邮箱"
                            required
                            disabled={isSubmitting}
                        />
                    </div>

                    <div className={styles.field}>
                        <label htmlFor="login-password">登录密码</label>

                        <input
                            id="login-password"
                            name="password"
                            type="password"
                            autoComplete="current-password"
                            placeholder="请输入密码"
                            disabled={isSubmitting}
                            maxLength={128}
                            required
                        />
                    </div>

                    {/* 请求期间禁用按钮，并显示等待文字。 */}
                    <button
                        className={styles.submitButton}
                        type="submit"
                        disabled={isSubmitting}
                        aria-describedby="login-status"
                    >
                        {isSubmitting ? '正在登录…' : '登录工作台'}
                    </button>
                </form>

                {/* 提示区域保持存在，文字变化时让读屏软件获知。 */}
                <p
                    id="login-status"
                    className={styles.note}
                    data-status={status}
                    role="status"
                >
                    {message}
                </p>
            </section>
        )
    }
}
