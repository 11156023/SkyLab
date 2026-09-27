import { Component } from "react";
import CrashState from "../ErrorState/CrashState";
import { reportError } from "../../utils/sentry";

/**
 * React error boundary：攔截子樹 render / lifecycle 錯誤，
 * 顯示 CrashState 畫面並提供「重試」（重新掛載子樹）與「重新整理」。
 * 注意：async callback（事件、setTimeout、fetch）內丟出的錯誤不會被攔截，
 * 需在呼叫處自行處理或以 toast 呈現。
 *
 * @param {boolean} [fullPage] 根層 boundary 用：錯誤畫面撐滿整個視窗
 */
export default class ErrorBoundary extends Component {
  state = { error: null, componentStack: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("[ErrorBoundary] Uncaught error:", error, info);
    reportError(error, { componentStack: info?.componentStack });
    this.setState({ componentStack: info?.componentStack ?? null });
  }

  reset = () => {
    this.setState({ error: null, componentStack: null });
  };

  render() {
    const { error, componentStack } = this.state;
    if (!error) return this.props.children;

    return (
      <CrashState
        error={error}
        componentStack={componentStack}
        onRetry={this.reset}
        fullPage={this.props.fullPage}
      />
    );
  }
}
