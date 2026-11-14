/**
 * 文件职责：提供路由模块加载中的提示和加载失败后的恢复入口。
 * 作用与配合：由 router.tsx 配置为初次加载占位和路由错误界面。
 * 使用位置：应用路由层。
 * 边界：不请求业务接口，不展示异常堆栈，不自动反复刷新。
 */
import styles from './PageLoadState.module.css'

/** 初次直接打开业务地址时显示加载状态。 */
export function PageLoading() {
    return <section className={styles.panel}><p role="status">正在加载页面……</p></section>
}

/** 路由加载异常时，由用户明确重新加载当前地址。 */
export function PageLoadError() {
    return (
        <section className={styles.panel} role="alert">
            <h1>页面暂时无法加载</h1>
            <p>请检查网络后重新加载页面。</p>
            <button className={styles.button} type="button" onClick={() => window.location.reload()}>
                重新加载
            </button>
        </section>
    )
}
