"use client";

/** 智能分析助手：右下角悬浮球 + 右侧抽屉对话；AI 未配置时整个入口不出现。 */

import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

type ChatMessage = { role: "user" | "assistant"; content: string; error?: boolean };

/** 悬浮球与视口边缘的最小留白 */
const FAB_EDGE = 12;
/** 气泡尚未渲染时的兜底尺寸（首次夹取用），实际以量到的渲染尺寸为准 */
const FAB_FALLBACK_SIZE = { width: 42, height: 42 };

const SUGGESTED_QUESTIONS = [
  "最近采集情况怎么样？",
  "哪些站点或渠道现在有异常？",
  "现在输入价格最便宜的是哪个站点？",
];

function errorText(data: Record<string, unknown>): string {
  const detail = data.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) return detail.map((item) => String((item as Record<string, unknown>).msg ?? "")).join("；");
  return "服务暂时不可用，稍后再试试。";
}

export function AssistantDock() {
  const [available, setAvailable] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetch("/api/assistant/status")
      .then((res) => (res.ok ? res.json() : { available: false }))
      .then((data) => {
        setAvailable(Boolean(data.available));
        setModel(typeof data.model === "string" && data.model ? data.model : null);
      })
      .catch(() => setAvailable(false));
  }, []);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, pending]);

  // 悬浮球可拖拽：位置存 localStorage，拖动超过阈值算拖拽、否则算点击
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const fabRef = useRef<HTMLButtonElement>(null);
  const dragRef = useRef<{ startX: number; startY: number; posX: number; posY: number; moved: boolean } | null>(null);

  // 夹取按气泡的真实渲染尺寸算：可用宽度是 clientWidth（不含滚动条），
  // 差几个像素标签就会折行、气泡被压窄压高
  function clampPos(x: number, y: number): { x: number; y: number } {
    const rect = fabRef.current?.getBoundingClientRect();
    const width = rect?.width || FAB_FALLBACK_SIZE.width;
    const height = rect?.height || FAB_FALLBACK_SIZE.height;
    const maxX = Math.max(FAB_EDGE, document.documentElement.clientWidth - width - FAB_EDGE);
    const maxY = Math.max(FAB_EDGE, document.documentElement.clientHeight - height - FAB_EDGE);
    return {
      x: Math.min(Math.max(x, FAB_EDGE), maxX),
      y: Math.min(Math.max(y, FAB_EDGE), maxY),
    };
  }

  useEffect(() => {
    if (!available) return;
    const raw = localStorage.getItem("ai-fab-pos");
    if (raw) {
      try {
        const saved = JSON.parse(raw) as { x: number; y: number };
        setPos(clampPos(saved.x, saved.y));
      } catch {
        localStorage.removeItem("ai-fab-pos");
      }
    }
    // 窗口变小后老位置可能被挤出可见区域，这里跟着收回来
    const onResize = () => setPos((current) => (current ? clampPos(current.x, current.y) : current));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [available]);

  function onFabPointerDown(event: React.PointerEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const rect = event.currentTarget.getBoundingClientRect();
    dragRef.current = {
      startX: event.clientX,
      startY: event.clientY,
      posX: pos?.x ?? rect.left,
      posY: pos?.y ?? rect.top,
      moved: false,
    };
  }

  function onFabPointerMove(event: React.PointerEvent<HTMLButtonElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) < 4) return;
    drag.moved = true;
    setPos(clampPos(drag.posX + dx, drag.posY + dy));
  }

  function onFabPointerUp() {
    const drag = dragRef.current;
    dragRef.current = null;
    if (!drag) return;
    if (drag.moved) {
      setPos((current) => {
        if (current) localStorage.setItem("ai-fab-pos", JSON.stringify(current));
        return current;
      });
    } else {
      setOpen(true);
    }
  }

  if (!available) return null;

  async function ask(question: string) {
    const text = question.trim();
    if (!text || pending) return;
    setInput("");
    // 带上最近几轮有内容的对话（报错占位不算），后端才能理解"我刚才问了什么"这类指代
    const history = messages.filter((message) => message.content.trim() && !message.error).slice(-6);
    setMessages((prev) => [...prev, { role: "user", content: text }, { role: "assistant", content: "" }]);
    setPending(true);
    const appendReply = (chunk: string, isError = false) =>
      setMessages((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        next[next.length - 1] = { role: "assistant", content: last.content + chunk, error: last.error || isError };
        return next;
      });
    try {
      const res = await fetch("/api/assistant/ask/stream", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: text, history }),
      });
      if (!res.ok || !res.body) {
        const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
        appendReply(res.ok ? "服务没有返回内容，稍后再试试。" : `没答上来：${errorText(data)}`, true);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let received = false;
      let broken = false;
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const line = frame.trim();
            if (!line.startsWith("data:")) continue;
            let payload: { delta?: string; error?: string };
            try {
              payload = JSON.parse(line.slice(5).trim()) as { delta?: string; error?: string };
            } catch {
              continue; // 单帧损坏直接跳过，不影响其余内容
            }
            if (payload.delta) {
              received = true;
              appendReply(payload.delta);
            }
            if (payload.error) appendReply(`没答上来：${payload.error}`, true);
          }
        }
      } catch {
        broken = true; // 流中断：保留已收到的部分，提示可重试
      }
      if (broken) {
        appendReply(received ? "\n\n（连接中断了，以上回答可能不完整，可以重新问一次）" : "网络不太顺畅，稍后再试试。", true);
      }
    } catch {
      appendReply("网络不太顺畅，稍后再试试。", true);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <button
        type="button"
        ref={fabRef}
        className={`ai-fab${pos ? " ai-fab-moved" : ""}`}
        style={pos ? { left: pos.x, top: pos.y } : undefined}
        aria-label="智能分析助手"
        title="问一问"
        onPointerDown={onFabPointerDown}
        onPointerMove={onFabPointerMove}
        onPointerUp={onFabPointerUp}
      >
        <svg
          className="ai-fab-icon"
          width="19"
          height="19"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden
        >
          <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
        </svg>
      </button>
      {open && (
        <div
          className={`ai-drawer${pending || messages.length > 0 ? " ai-drawer-tall" : ""}${pos && pos.x < window.innerWidth / 2 ? " ai-drawer-left" : ""}`}
          role="dialog"
          aria-label="智能分析助手"
        >
          <div className="ai-drawer-head">
            <span className="ai-orb" aria-hidden />
            <div className="ai-drawer-title">
              <strong>智能分析助手</strong>
              <span>{model ?? "基于站点、价格与访问数据回答"}</span>
            </div>
            <button type="button" className="ai-drawer-close" aria-label="收起" onClick={() => setOpen(false)}>
              ✕
            </button>
          </div>
          <div className="ai-drawer-list" ref={listRef}>
            {messages.length === 0 && (
              <div className="ai-suggest">
                <p>可以从这些问题开始：</p>
                {SUGGESTED_QUESTIONS.map((question) => (
                  <button key={question} type="button" onClick={() => void ask(question)}>
                    {question}
                  </button>
                ))}
              </div>
            )}
            {messages.map((message, index) => (
              <div key={index} className={`ai-msg ai-msg-${message.role}`}>
                {message.role === "assistant" ? (
                  message.content ? (
                    <div className="ai-markdown">
                      <Markdown remarkPlugins={[remarkGfm]}>{message.content}</Markdown>
                    </div>
                  ) : pending && index === messages.length - 1 ? (
                    <span className="ai-typing" aria-label="正在思考" />
                  ) : null
                ) : (
                  message.content
                )}
              </div>
            ))}
          </div>
          <form
            className="ai-drawer-input"
            onSubmit={(event) => {
              event.preventDefault();
              void ask(input);
            }}
          >
            <input
              value={input}
              placeholder="输入你的问题…"
              onChange={(event) => setInput(event.target.value)}
              disabled={pending}
            />
            <button type="submit" disabled={pending || !input.trim()} aria-label="发送">
              发送
            </button>
          </form>
        </div>
      )}
    </>
  );
}
