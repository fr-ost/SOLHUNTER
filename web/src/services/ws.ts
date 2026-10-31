import type { LiveEvent } from "../types/api";

export type LiveStatus = "disabled" | "connecting" | "live" | "offline";
type Listener = (event: LiveEvent) => void;

/** WebSocket client with exponential-backoff reconnect and keep-alive pings. */
export class LiveClient {
  private socket: WebSocket | null = null;
  private listeners = new Set<Listener>();
  private statusListeners = new Set<(s: LiveStatus) => void>();
  private attempt = 0;
  private reconnectTimer: number | null = null;
  private pingTimer: number | null = null;
  private url: string | null = null;
  private stopped = true;
  status: LiveStatus = "disabled";
  lastEventAt: number | null = null;

  start(url: string | null): void {
    this.stop();
    this.url = url;
    this.stopped = false;
    if (!url) {
      this.setStatus("disabled");
      return;
    }
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.reconnectTimer) window.clearTimeout(this.reconnectTimer);
    if (this.pingTimer) window.clearInterval(this.pingTimer);
    this.reconnectTimer = null;
    this.pingTimer = null;
    if (this.socket) {
      this.socket.onclose = null;
      this.socket.close();
      this.socket = null;
    }
    this.setStatus("disabled");
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  onStatus(listener: (s: LiveStatus) => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  private setStatus(status: LiveStatus): void {
    this.status = status;
    this.statusListeners.forEach((l) => l(status));
  }

  private connect(): void {
    if (!this.url || this.stopped) return;
    this.setStatus("connecting");
    let socket: WebSocket;
    try {
      socket = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      this.attempt = 0;
      this.setStatus("live");
      this.pingTimer = window.setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send("ping");
      }, 25_000);
    };
    socket.onmessage = (msg) => {
      try {
        const data = JSON.parse(String(msg.data)) as LiveEvent;
        this.lastEventAt = Date.now();
        if (data.type === "pong" || data.type === "heartbeat" || data.type === "hello") return;
        this.listeners.forEach((l) => l(data));
      } catch {
        /* ignore malformed frames */
      }
    };
    socket.onerror = () => {
      /* onclose follows */
    };
    socket.onclose = () => {
      if (this.pingTimer) window.clearInterval(this.pingTimer);
      this.pingTimer = null;
      this.socket = null;
      this.setStatus("offline");
      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    if (this.stopped) return;
    const delay = Math.min(30_000, 1000 * 2 ** this.attempt) * (0.7 + Math.random() * 0.6);
    this.attempt = Math.min(this.attempt + 1, 6);
    this.reconnectTimer = window.setTimeout(() => this.connect(), delay);
  }
}
