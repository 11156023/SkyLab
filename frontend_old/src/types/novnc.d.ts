declare module "@novnc/novnc/lib/rfb" {
  interface RFBOptions {
    shared?: boolean
    credentials?: {
      username?: string
      password?: string
      target?: string
    }
    repeaterID?: string
    wsProtocols?: string[]
  }

  class RFB {
    constructor(
      target: HTMLElement,
      urlOrChannel: string | WebSocket,
      options?: RFBOptions,
    )

    viewOnly: boolean
    scaleViewport: boolean
    resizeSession: boolean
    showDotCursor: boolean
    clipViewport: boolean
    dragViewport: boolean
    qualityLevel: number
    compressionLevel: number

    disconnect(): void
    sendCtrlAltDel(): void
    clipboardPasteFrom(text: string): void
    addEventListener(event: string, callback: (e: CustomEvent) => void): void
    removeEventListener(event: string, callback: (e: CustomEvent) => void): void
  }

  export default RFB
}
