/**
 * 文件职责：展示上传表单，校验输入并提交文件入库任务。
 * 作用与配合：调用 useSubmitFileIngestionJob，接收成功后通知父页面。
 * 使用位置：由 IngestionPage 组合在任务列表上方。
 * 边界：不执行 OCR；网络中断不能证明后台没有创建任务。
 */

import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, SubmitEvent } from 'react'

import { ApiError } from '../../../shared/api/http'
import { useSubmitFileIngestionJob } from '../hooks/useSubmitFileIngestionJob'
import type { FileIngestionJobInput } from '../types'

import styles from './FileUploadForm.module.css'

/** 父页面传给上传表单的参数。 */
type FileUploadFormProps = {
  // 接收任务成功后，通知父页面显示全部状态的第一页。
  onSubmitted: () => void
}

/** 页面提示的状态和文字。 */
type Feedback = {
  // uncertain：没有收到明确结果，需要先核对任务列表。
  kind: 'idle' | 'success' | 'error' | 'uncertain'
  message: string
}

/**
 * 读取并校验表单，返回可以提交的数据。
 *
 * form：用户刚刚提交的 HTML 表单。
 * 校验失败时抛出 Error，由组件显示原因。
 */
function readUploadInput(
  form: HTMLFormElement,
): FileIngestionJobInput {
  // 根据控件的 name 读取表单数据。
  // 必须在等待网络之前读取。
  const values = new FormData(form)

  const file = values.get('file')
  const rawTitle = values.get('title')
  const visibility = values.get('visibility')

  // instanceof 检查对象类型。
  // file.size 的单位是字节。
  if (
    !(file instanceof File) ||
    !file.name ||
    file.size === 0
  ) {
    throw new Error('请选择一个内容不为空的文件。')
  }

  // 与后端的 5 MiB 限制保持一致。
  if (file.size > 5 * 1024 * 1024) {
    throw new Error('文件不能超过 5 MiB。')
  }

  // some()：数组中只要有一项匹配，就返回 true。
  // suffix：本次检查的扩展名。
  // toLowerCase()：转小写，兼容 .PDF 等大写扩展名。
  // endsWith()：判断文件名是否以这个扩展名结尾。
  const supported = [
    '.txt',
    '.md',
    '.markdown',
    '.pdf',
    '.docx',
  ].some((suffix) =>
    file.name.toLowerCase().endsWith(suffix),
  )

  if (!supported) {
    throw new Error(
      '仅支持 TXT、Markdown、PDF 和 DOCX 文件。',
    )
  }

  // trim() 去掉首尾空白，保留标题中间的文字。
  const title =
    typeof rawTitle === 'string'
      ? rawTitle.trim()
      : ''

  // Array.from() 按 Unicode 码点拆分字符串，再统计长度。
  // 避免把一个常见 emoji 按两个 UTF-16 单元计算。
  if (!title || Array.from(title).length > 200) {
    throw new Error(
      '标题去除首尾空白后，需要为 1～200 个字符。',
    )
  }

  // 浏览器读到的是字符串，需要检查是否属于允许值。
  if (
    visibility !== 'internal' &&
    visibility !== 'restricted'
  ) {
    throw new Error('请选择有效的文档可见范围。')
  }

  return { file, title, visibility }
}

/** 展示上传控件，管理请求等待、取消和结果提示。 */
export function FileUploadForm({
  onSubmitted,
}: FileUploadFormProps) {
  // 标题使用受控输入，选文件后可以自动填写文件名。
  const [title, setTitle] = useState('')

  const [feedback, setFeedback] = useState<Feedback>({
    kind: 'idle',
    message: '提交后由后台处理，进度会显示在下方任务列表。',
  })

  const mutation = useSubmitFileIngestionJob()

  // 立即记录当前请求，防止按钮更新前发生连续提交。
  const requestRef =
    useRef<AbortController | null>(null)

  const isSubmitting = mutation.isPending
  const needsReview = feedback.kind === 'uncertain'

  // 提交期间，以及尚未核对不确定结果时，禁止再次提交。
  const controlsDisabled = isSubmitting || needsReview

  // 组件卸载时，停止等待旧请求。
  // 取消浏览器等待，不会撤销后端已经创建的任务。
  useEffect(() => {
    return () => {
      requestRef.current?.abort()
      requestRef.current = null
    }
  }, [])

  /**
   * 选文件后，默认使用文件名作为标题。
   * event：文件选择框本次发生的变化事件。
   */
  function handleFileChange(
    event: ChangeEvent<HTMLInputElement>,
  ): void {
    // files 是文件列表；?.[0] 表示存在时取第一个文件。
    const file = event.currentTarget.files?.[0]

    // 没有选中文件时，将标题清空。
    setTitle(file?.name ?? '')
  }

  /**
   * 校验表单并提交一次任务。
   *
   * event：本次表单提交事件。
   * currentTarget：绑定这个事件的 form 元素。
   */
  async function handleSubmit(
    event: SubmitEvent<HTMLFormElement>,
  ): Promise<void> {
    // 阻止浏览器默认提交，避免整页刷新。
    event.preventDefault()

    // 同时防止快速连点和未核对结果时的重复提交。
    if (
      requestRef.current ||
      isSubmitting ||
      needsReview
    ) return

    // 在 await 前保存表单和读取数据。
    const form = event.currentTarget
    let input: FileIngestionJobInput

    try {
      input = readUploadInput(form)
    } catch (error: unknown) {
      setFeedback({
        kind: 'error',
        message:
          error instanceof Error
            ? error.message
            : '请检查表单。',
      })
      return
    }

    // 为这一次请求创建独立的取消控制器。
    const controller = new AbortController()
    requestRef.current = controller

    setFeedback({
      kind: 'idle',
      message: '正在上传并提交任务，请稍候……',
    })

    try {
      // mutateAsync() 真正开始提交。
      // 返回 Promise，因此可以使用 await 等待结果。
      const job = await mutation.mutateAsync({
        input,
        signal: controller.signal,
      })

      // 已离开页面，或请求已经不属于当前组件时，
      // 不再修改界面，也不通知父页面。
      if (
        controller.signal.aborted ||
        requestRef.current !== controller
      ) return

      // 清空原生文件选择框。
      form.reset()

      // 标题由 React 状态控制，需要单独清空。
      setTitle('')

      setFeedback({
        kind: 'success',
        message:
          `任务已接收，ID：${job.id}。` +
          '请在下方查看处理进度。',
      })

      // 通知父页面：显示全部状态的第一页。
      onSubmitted()
    } catch (error: unknown) {
      // 取消后，不再显示旧请求的错误。
      if (
        controller.signal.aborted ||
        requestRef.current !== controller
      ) return

      // 常规 4xx 表示明确拒绝。
      // 408 表示超时，仍按结果不确定处理。
      if (
        error instanceof ApiError &&
        error.status < 500 &&
        error.status !== 408
      ) {
        setFeedback({
          kind: 'error',
          message:
            error.status === 401
              ? '登录已失效，请通过右上角退出登录后重新登录。'
              : error.message,
        })
      } else {
        // 超时、断网或 5xx 可能发生在保存任务之后。
        const detail =
          error instanceof ApiError
            ? `${error.message}。`
            : ''

        setFeedback({
          kind: 'uncertain',
          message:
            `${detail}未收到明确提交结果，任务可能已经创建。` +
            '请先将下方列表切到“全部状态”的第一页，' +
            '刷新后按标题和提交时间核对；' +
            '确认没有对应任务后，再决定是否重交。',
        })
      }
    } finally {
      // 只释放本次请求，避免旧请求清掉新的引用。
      if (requestRef.current === controller) {
        requestRef.current = null
      }
    }
  }

  /**
   * 用户自行核对列表后，允许再次提交。
   * 这里只解除限制，不自动发送请求。
   */
  function allowAnotherSubmission(): void {
    if (requestRef.current) return

    // 清空 Hook 的提交状态，不会清空文件或发送请求。
    mutation.reset()

    setFeedback({
      kind: 'idle',
      message: '已解除提交限制，请确认任务没有重复后再提交。',
    })
  }

  return (
    <section
      className={styles.panel}
      aria-labelledby="upload-title"
    >
      <h2 id="upload-title" className={styles.heading}>
        上传文档
      </h2>

      <p className={styles.description}>
        提交后进入后台队列，支持文字 PDF 和扫描 PDF。
      </p>

      <form
        className={styles.form}
        onSubmit={handleSubmit}
        aria-busy={isSubmitting}
      >
        <div className={styles.field}>
          <label htmlFor="upload-file">选择文件</label>

          {/* accept 只提示文件类型，仍需要前后端校验。 */}
          <input
            id="upload-file"
            name="file"
            type="file"
            className={styles.input}
            accept=".txt,.md,.markdown,.pdf,.docx"
            onChange={handleFileChange}
            aria-describedby="upload-file-help"
            disabled={controlsDisabled}
            required
          />

          <p id="upload-file-help" className={styles.help}>
            TXT、Markdown、PDF、DOCX，最大 5 MiB。
          </p>
        </div>

        <div className={styles.fields}>
          <div className={styles.field}>
            <label htmlFor="upload-document-title">
              文档标题
            </label>

            <input
              id="upload-document-title"
              name="title"
              className={styles.input}
              value={title}
              onChange={(event) =>
                setTitle(event.currentTarget.value)
              }
              aria-describedby="upload-title-help"
              disabled={controlsDisabled}
              required
            />

            <p id="upload-title-help" className={styles.help}>
              默认使用文件名，可修改；最多 200 个字符。
            </p>
          </div>

          <div className={styles.field}>
            <label htmlFor="upload-visibility">
              可见范围
            </label>

            <select
              id="upload-visibility"
              name="visibility"
              className={styles.input}
              defaultValue="internal"
              disabled={controlsDisabled}
            >
              <option value="internal">
                内部可见
              </option>
              <option value="restricted">
                主管和管理员可见
              </option>
            </select>
          </div>
        </div>

        <button
          type="submit"
          className={styles.button}
          disabled={controlsDisabled}
          aria-describedby="upload-feedback"
        >
          {isSubmitting ? '正在提交…' : '提交入库任务'}
        </button>
      </form>

      {/* 提示放在忙碌表单之外，方便读屏软件及时播报。 */}
      <p
        id="upload-feedback"
        className={styles.feedback}
        data-kind={feedback.kind}
        role={
          feedback.kind === 'error' || needsReview
            ? 'alert'
            : 'status'
        }
      >
        {feedback.message}
      </p>

      {needsReview && (
        <div className={styles.review}>
          <button
            type="button"
            className={styles.button}
            onClick={allowAnotherSubmission}
          >
            已核对任务列表，允许重新提交
          </button>
        </div>
      )}
    </section>
  )
}