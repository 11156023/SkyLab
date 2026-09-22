import * as monaco from "monaco-editor";
import Editor, { loader } from "@monaco-editor/react";

// Use the same locally bundled Monaco runtime as the gateway configuration editor.
loader.config({ monaco });

const OPTIONS = {
  readOnly: true,
  domReadOnly: true,
  minimap: { enabled: false },
  fontSize: 12,
  lineHeight: 20,
  fontFamily: '"Cascadia Code", Consolas, "Courier New", monospace',
  lineNumbers: "on",
  lineNumbersMinChars: 3,
  glyphMargin: false,
  folding: false,
  renderLineHighlight: "none",
  overviewRulerLanes: 0,
  hideCursorInOverviewRuler: true,
  scrollBeyondLastLine: false,
  wordWrap: "off",
  tabSize: 2,
  automaticLayout: true,
  contextmenu: false,
  stickyScroll: { enabled: false },
  padding: { top: 8, bottom: 8 },
  scrollbar: { alwaysConsumeMouseWheel: false, horizontalScrollbarSize: 8 },
};

export default function ReadOnlyCode({ code, language, label, fallback, height = 360 }) {
  return (
    <Editor
      height={height}
      language={language === "bash" ? "shell" : language}
      theme="vs-dark"
      value={code}
      options={{ ...OPTIONS, ariaLabel: label }}
      loading={fallback}
    />
  );
}
