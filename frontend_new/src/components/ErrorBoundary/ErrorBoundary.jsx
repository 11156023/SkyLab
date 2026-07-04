import { Component } from "react";
import styles from "./ErrorBoundary.module.scss";
import MIcon from "../MIcon";

/**
 * React error boundary：攔截子樹 render / lifecycle 錯誤，
 * 顯示友善的錯誤畫面並提供「重試」（重新掛載子樹）與「重新整理」。
 * 注意：async callback（事件、setTimeout、fetch）內丟出的錯誤不會被攔截，
 * 需在呼叫處自行處理或以 toast 呈現。
 */
export default class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("[ErrorBoundary] Uncaught error:", error, info);
  }

  reset = () => {
    this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div role="alert" className={styles.wrap}>
        <span className={styles.icon}>
          <MIcon name="error_outline" size={40} />
        </span>
        <h2 className={styles.title}>頁面發生錯誤</h2>
        <p className={styles.desc}>這個區塊出了點問題，你可以重試或重新整理頁面。</p>
        <details className={styles.details}>
          <summary>錯誤詳情</summary>
          <pre>{error?.message ?? String(error)}</pre>
        </details>
        <div className={styles.actions}>
          <button type="button" className={styles.btnPrimary} onClick={this.reset}>
            <MIcon name="refresh" size={16} />
            重試
          </button>
          <button
            type="button"
            className={styles.btnSecondary}
            onClick={() => window.location.reload()}
          >
            重新整理頁面
          </button>
        </div>
      </div>
    );
  }
}
