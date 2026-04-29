import { FitAddon } from "@xterm/addon-fit"
import { WebLinksAddon } from "@xterm/addon-web-links"
import { WebglAddon } from "@xterm/addon-webgl"
import { Terminal } from "@xterm/xterm"
import { useCallback, useEffect, useRef, useState } from "react"
import { useTranslation } from "react-i18next"
import "@xterm/xterm/css/xterm.css"

interface XTermDisplayProps {
  vmid: number
  onDisconnect?: () => void
}

type ConnectionStatus = "connecting" | "connected" | "disconnected" | "error"

export default function useXTermDisplay({
  vmid,
  onDisconnect,
}: XTermDisplayProps) {
  const { t } = useTranslation("console")
  const [terminalElement, setTerminalElement] = useState<HTMLDivElement | null>(
    null,
  )
  const termRef = useRef<Terminal | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const fitAddonRef = useRef<FitAddon | null>(null)
  const [status, setStatus] = useState<ConnectionStatus>("connecting")
  const [error, setError] = useState("")

  // Use callback ref to capture when div is mounted
  const terminalRef = useCallback((node: HTMLDivElement | null) => {
    setTerminalElement(node)
  }, [])

  useEffect(() => {
    if (!vmid || !terminalElement) {
      return
    }

    setStatus("connecting")
    setError("")

    let term: Terminal | null = null
    let ws: WebSocket | null = null
    let fitAddon: FitAddon | null = null
    let isSubscribed = true
    let pingInterval: ReturnType<typeof setInterval> | null = null
    let isTerminalReady = false // Track if we received "OK" from server

    const initTerminal = async () => {
      try {
        // Create terminal instance
        term = new Terminal({
          cursorBlink: true,
          fontSize: 14,
          fontFamily: 'Menlo, Monaco, "Courier New", monospace',
          theme: {
            background: "#1e1e1e",
            foreground: "#ffffff",
            cursor: "#ffffff",
            selectionBackground: "rgba(255, 255, 255, 0.3)",
          },
          scrollback: 5000,
          convertEol: true,
          smoothScrollDuration: 0,
        })

        // Add fit addon for responsive sizing
        fitAddon = new FitAddon()
        term.loadAddon(fitAddon)

        // Add web links addon
        const webLinksAddon = new WebLinksAddon()
        term.loadAddon(webLinksAddon)

        // Open terminal in container
        if (terminalElement) {
          term.open(terminalElement)

          // Enable GPU-accelerated WebGL renderer
          try {
            const webglAddon = new WebglAddon()
            webglAddon.onContextLoss(() => {
              webglAddon.dispose()
            })
            term.loadAddon(webglAddon)
          } catch {
            // WebGL not available, fall back to default canvas renderer
          }

          // Delay fit to ensure container is fully rendered
          setTimeout(() => {
            try {
              fitAddon?.fit()
            } catch {
              // Ignore fit errors
            }
          }, 100)
        }

        termRef.current = term
        fitAddonRef.current = fitAddon

        // Connect to WebSocket
        const apiUrl = new URL(
          import.meta.env.VITE_API_URL ||
            `${window.location.protocol}//${window.location.host}`,
        )
        const protocol = apiUrl.protocol === "https:" ? "wss:" : "ws:"
        const accessToken = localStorage.getItem("access_token") || ""
        const wsUrl = `${protocol}//${apiUrl.host}/ws/terminal/${vmid}?token=${encodeURIComponent(accessToken)}`

        ws = new WebSocket(wsUrl)
        wsRef.current = ws

        ws.binaryType = "arraybuffer"

        ws.onopen = () => {
          if (!isSubscribed) return

          // Start ping interval (every 30 seconds)
          pingInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN && isTerminalReady) {
              ws.send("2")
            }
          }, 30000)
        }

        ws.onmessage = (event) => {
          if (!term || !isSubscribed) return

          try {
            if (typeof event.data === "string") {
              // Check for "OK" response on initial connection
              if (!isTerminalReady && event.data.startsWith("OK")) {
                isTerminalReady = true
                setStatus("connected")
                setError("")

                // Write the rest of the message after "OK"
                const remainingData = event.data.slice(2)
                if (remainingData) {
                  term.write(remainingData)
                }

                // Focus and fit terminal after connection
                requestAnimationFrame(() => {
                  requestAnimationFrame(() => {
                    term?.focus()
                    if (fitAddon && ws && ws.readyState === WebSocket.OPEN) {
                      try {
                        fitAddon.fit()
                        // Send the correct terminal size to server
                        const cols = term?.cols || 80
                        const rows = term?.rows || 24
                        const resizeMessage = `1:${cols}:${rows}:`
                        ws.send(resizeMessage)
                      } catch {
                        // Ignore fit errors
                      }
                    }
                  })
                })
              } else {
                term.write(event.data)
              }
            } else if (event.data instanceof ArrayBuffer) {
              const uint8Array = new Uint8Array(event.data)

              // Check for "OK" in binary format (ASCII 79, 75)
              if (
                !isTerminalReady &&
                uint8Array.length >= 2 &&
                uint8Array[0] === 79 &&
                uint8Array[1] === 75
              ) {
                isTerminalReady = true
                setStatus("connected")
                setError("")

                // Write the rest of the data after "OK"
                if (uint8Array.length > 2) {
                  term.write(uint8Array.slice(2))
                }

                // Focus and fit terminal after connection
                requestAnimationFrame(() => {
                  requestAnimationFrame(() => {
                    term?.focus()
                    if (fitAddon && ws && ws.readyState === WebSocket.OPEN) {
                      try {
                        fitAddon.fit()
                        // Send the correct terminal size to server
                        const cols = term?.cols || 80
                        const rows = term?.rows || 24
                        const resizeMessage = `1:${cols}:${rows}:`
                        ws.send(resizeMessage)
                      } catch {
                        // Ignore fit errors
                      }
                    }
                  })
                })
              } else {
                term.write(uint8Array)
              }
            }
          } catch (err) {
            console.error("Error writing to terminal:", err)
          }
        }

        ws.onerror = (wsError) => {
          console.error("❌ Backend WebSocket error:", wsError)
          if (!isSubscribed) return
          setStatus("error")
          setError(t("terminal.backendConnectFailed"))
        }

        ws.onclose = (event) => {
          if (!isSubscribed) return
          setStatus("disconnected")
          if (event.code === 1000) {
            setError(t("terminal.normalClosure"))
          } else {
            setError(
              t("terminal.closedWithReason", {
                reason: event.reason || t("shared.unknownReason"),
              }),
            )
          }
        }

        // Handle terminal input using Proxmox termproxy protocol
        term.onData((data) => {
          if (ws && ws.readyState === WebSocket.OPEN && isTerminalReady) {
            // Format: 0:LENGTH:MSG
            const bytes = new TextEncoder().encode(data).length
            const message = `0:${bytes}:${data}`
            ws.send(message)
          }
        })

        // Handle terminal resize using Proxmox termproxy protocol
        term.onResize((size) => {
          if (ws && ws.readyState === WebSocket.OPEN && isTerminalReady) {
            // Format: 1:COLS:ROWS:
            const message = `1:${size.cols}:${size.rows}:`
            ws.send(message)
          }
        })

        // Handle window resize (debounced)
        let resizeTimer: ReturnType<typeof setTimeout> | null = null
        const handleResize = () => {
          if (resizeTimer) clearTimeout(resizeTimer)
          resizeTimer = setTimeout(() => {
            if (fitAddon && term) {
              try {
                fitAddon.fit()
              } catch {
                // Silently ignore fit errors
              }
            }
          }, 50)
        }

        window.addEventListener("resize", handleResize)

        // Use ResizeObserver to watch for container size changes
        let resizeObserver: ResizeObserver | null = null
        if (terminalElement) {
          resizeObserver = new ResizeObserver(() => {
            handleResize()
          })
          resizeObserver.observe(terminalElement)
        }

        // Cleanup
        return () => {
          window.removeEventListener("resize", handleResize)
          if (resizeObserver && terminalElement) {
            resizeObserver.unobserve(terminalElement)
            resizeObserver.disconnect()
          }
          if (pingInterval) {
            clearInterval(pingInterval)
          }
        }
      } catch (err) {
        if (!isSubscribed) return
        setStatus("error")
        setError(t("terminal.initFailed", { message: (err as Error).message }))
        console.error("Terminal initialization error:", err)
      }
    }

    initTerminal()

    return () => {
      isSubscribed = false
      if (pingInterval) {
        clearInterval(pingInterval)
      }
      if (termRef.current) {
        termRef.current.dispose()
        termRef.current = null
      }
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [vmid, terminalElement, t])

  const handleDisconnect = () => {
    if (wsRef.current) {
      wsRef.current.close()
    }
    if (termRef.current) {
      termRef.current.dispose()
    }
    onDisconnect?.()
  }

  const handleClear = () => {
    termRef.current?.clear()
  }

  const handleReset = () => {
    termRef.current?.reset()
  }

  const fitTerminal = () => {
    fitAddonRef.current?.fit()
  }

  return {
    status,
    error,
    terminalRef,
    handleDisconnect,
    handleClear,
    handleReset,
    fitTerminal,
  }
}

export function XTermDisplayComponent({
  vmid,
  onDisconnect,
}: XTermDisplayProps) {
  const { status, error, terminalRef } = useXTermDisplay({
    vmid,
    onDisconnect,
  })

  return (
    <div className="relative w-full h-full bg-[#1e1e1e]">
      {status === "connecting" && !error && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-gray-900 z-20">
          <div className="relative">
            <div className="w-16 h-16 border-4 border-green-500 border-t-transparent rounded-full animate-spin" />
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="w-8 h-8 bg-green-500 rounded-full animate-pulse" />
            </div>
          </div>
          <div className="mt-6 text-white text-lg font-medium">
            正在連接終端...
          </div>
          <div className="mt-2 text-gray-400 text-sm">請稍候</div>
        </div>
      )}

      {error && (
        <div className="absolute top-4 left-4 right-4 bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded z-10">
          {error}
        </div>
      )}

      <div
        ref={terminalRef}
        className="w-full h-full p-2"
        style={{
          minHeight: "600px",
        }}
      />
    </div>
  )
}
