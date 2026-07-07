import {
  Component,
  type ErrorInfo,
  type ReactElement,
  type ReactNode,
} from "react"
import { useTranslation } from "react-i18next"

interface Props {
  children: ReactNode
  /** Optional custom fallback. Receives the caught error + a reset callback. */
  fallback?: (error: Error, reset: () => void) => ReactNode
  /** Optional callback fired once after each caught error (for telemetry). */
  onError?: (error: Error, info: ErrorInfo) => void
  /**
   * Short label included in the default fallback heading, e.g. "VNC Console".
   * When provided, falls back to `errorBoundary.scopedTitle`; otherwise the
   * generic `errorBoundary.title` is used.
   */
  scope?: string
}

interface State {
  error: Error | null
}

/**
 * React error boundary. Catches render-time and lifecycle errors anywhere in
 * its subtree, displays a friendly fallback UI, and exposes a "Try again"
 * button that re-mounts the children. Errors thrown inside async callbacks
 * (event handlers, setTimeout, fetch resolutions) are NOT caught — those must
 * be handled at their call site or surfaced via `useCustomToast`.
 */
class ErrorBoundaryClass extends Component<
  Props & { t: (key: string, opts?: Record<string, unknown>) => string },
  State
> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Always log so the developer can diagnose; bubble up to optional sink.
    console.error("[ErrorBoundary] Uncaught error:", error, info)
    this.props.onError?.(error, info)
  }

  reset = (): void => {
    this.setState({ error: null })
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children

    if (this.props.fallback) {
      return this.props.fallback(error, this.reset)
    }

    const { t, scope } = this.props
    const title = scope
      ? t("errorBoundary.scopedTitle", { scope })
      : t("errorBoundary.title")

    return (
      <div
        role="alert"
        className="flex min-h-50 w-full flex-col items-center justify-center gap-4 rounded-md border border-destructive/30 bg-destructive/5 p-6 text-center"
      >
        <h2 className="text-lg font-semibold text-destructive">{title}</h2>
        <p className="max-w-prose text-sm text-muted-foreground">
          {t("errorBoundary.description")}
        </p>
        <details className="w-full max-w-prose text-left text-xs text-muted-foreground">
          <summary className="cursor-pointer">
            {t("errorBoundary.details")}
          </summary>
          <pre className="mt-2 overflow-auto whitespace-pre-wrap wrap-break-word rounded bg-muted p-2 font-mono">
            {error.message}
          </pre>
        </details>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={this.reset}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
          >
            {t("errorBoundary.retry")}
          </button>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center justify-center rounded-md border border-input bg-background px-4 py-2 text-sm font-medium hover:bg-accent"
          >
            {t("errorBoundary.reload")}
          </button>
        </div>
      </div>
    )
  }
}

export function ErrorBoundary(props: Props): ReactElement {
  const { t } = useTranslation()
  return <ErrorBoundaryClass {...props} t={t} />
}

export default ErrorBoundary
