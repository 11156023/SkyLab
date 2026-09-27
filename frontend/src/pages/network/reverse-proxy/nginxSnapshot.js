/**
 * nginx Runtime 面板收合時要保留的快照：成功的快照沿用（後端本來就快取 15 秒），
 * 失敗的（runtime_error）丟掉，下次展開才會重試，不會一直卡在「連線失敗」。
 */
export function snapshotToKeepOnClose(snapshot) {
  return snapshot?.runtime_error ? null : snapshot;
}
