"use client";

/** 智能分析助手：右下角猫脸悬浮球 + 右侧全高抽屉对话；AI 未配置时整个入口不出现。 */

import { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAuthPage } from "@/lib/useAuthPage";
import { IconClose, IconHistory, IconPlus } from "./icons";
import { DajuPeek } from "./DajuArt";

type ChatMessage = { role: "user" | "assistant"; content: string; error?: boolean };

/** 本地会话档案：会话只存在访客自己的浏览器里（localStorage），服务端不留副本 */
type ChatSession = { id: string; title: string; updatedAt: number; messages: ChatMessage[] };

/** 悬浮球与视口边缘的最小留白 */
const FAB_EDGE = 12;
/** 气泡尚未渲染时的兜底尺寸（首次夹取用），实际以量到的渲染尺寸为准 */
const FAB_FALLBACK_SIZE = { width: 42, height: 42 };

/** 空态建议问题：按能力准备的三类示范，每批展示 3 条，可换一批 */
const SUGGESTED_QUESTIONS = [
  "最近一周哪些模型降价了？",
  "哪些站点或渠道现在有异常？",
  "现在输入价最便宜的是哪个站点？",
  "gpt-5.6-sol 全站比价，谁最便宜？",
  "AIHub365 哪个渠道可用率最低？",
  "sudocode 最近有什么活动？",
  "最近有哪些新模型上线？",
  "哪个站的 Claude 最便宜？",
  "我刚才问了什么？",
];
/** 空态每批展示的建议问题数 */
const SUGGEST_BATCH_SIZE = 3;

/** 本地会话历史的 localStorage key 与容量上限：超出后最旧的会话被挤掉 */
const SESSIONS_KEY = "ai-chat-sessions";
const MAX_SESSIONS = 30;

/** 从 localStorage 读本地会话：只收结构完整的记录，空会话与发送中的占位气泡直接丢弃 */
function loadSessions(): ChatSession[] {
  try {
    const raw = localStorage.getItem(SESSIONS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const sessions: ChatSession[] = [];
    for (const item of parsed) {
      if (typeof item !== "object" || item === null) continue;
      const record = item as Record<string, unknown>;
      if (typeof record.id !== "string" || !Array.isArray(record.messages)) continue;
      const messages = (record.messages as unknown[]).filter(
        (message): message is ChatMessage =>
          typeof message === "object" &&
          message !== null &&
          ((message as ChatMessage).role === "user" || (message as ChatMessage).role === "assistant") &&
          typeof (message as ChatMessage).content === "string" &&
          (message as ChatMessage).content.trim().length > 0,
      );
      if (messages.length === 0) continue;
      sessions.push({
        id: record.id,
        title: typeof record.title === "string" && record.title ? record.title : messages[0].content.slice(0, 20),
        updatedAt: typeof record.updatedAt === "number" ? record.updatedAt : 0,
        messages: messages.map((message) => ({ ...message })),
      });
    }
    return sessions.sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_SESSIONS);
  } catch {
    return [];
  }
}

function persistSessions(sessions: ChatSession[]) {
  try {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions));
  } catch {
    // 隐私模式或超额时存不进去就算了，不影响当前对话
  }
}

function newSessionId(): string {
  return typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `s-${Date.now()}-${Math.random()}`;
}

/** 会话列表的时间标签：今天给时分，昨天标"昨天"，更早给月日 */
function sessionTimeLabel(ts: number): string {
  const date = new Date(ts);
  const now = new Date();
  const sameDay = (a: Date, b: Date) =>
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const pad = (n: number) => String(n).padStart(2, "0");
  if (sameDay(date, now)) return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(date, yesterday)) return "昨天";
  return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function errorText(data: Record<string, unknown>): string {
  const detail = data.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) return detail.map((item) => String((item as Record<string, unknown>).msg ?? "")).join("；");
  return "服务暂时不可用，稍后再试试。";
}

export function AssistantDock() {
  const isAuthPage = useAuthPage();
  const [available, setAvailable] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  /** 查数提示：后端调工具查库期间显示"正在查询 xx…"，首个回答字出来即清掉 */
  const [statusText, setStatusText] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  /** 本地会话历史：只存这台设备的浏览器，抽屉里可切换/删除 */
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  /** 当前对话所属会话的 id：新对话先占一个空 id，聊出内容后才进历史列表 */
  const [activeId, setActiveId] = useState(newSessionId);
  /** 抽屉主体当前显示对话区还是历史列表 */
  const [view, setView] = useState<"chat" | "history">("chat");
  /** 打开抽屉时悬浮球上指针的位置：抽屉缩放动画从这个点长出来 */
  const [originPoint, setOriginPoint] = useState<{ x: number; y: number } | null>(null);
  /** 建议问题翻页：换一批循环展示 */
  const [suggestPage, setSuggestPage] = useState(0);
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

  // 启动时续上最近一次对话：刷新或重开浏览器后从上次聊到的地方继续
  useEffect(() => {
    const saved = loadSessions();
    if (saved.length === 0) return;
    setSessions(saved);
    setActiveId(saved[0].id);
    setMessages(saved[0].messages);
  }, []);

  // 回合结束后把当前对话落进本地会话历史；流式回答中不落，等回答收尾再写
  useEffect(() => {
    if (pending) return;
    const meaningful = messages.filter((message) => message.content.trim());
    if (meaningful.length === 0) return;
    setSessions((prev) => {
      const updated: ChatSession = {
        id: activeId,
        title: meaningful[0].content.slice(0, 20),
        updatedAt: Date.now(),
        messages: meaningful,
      };
      const next = [updated, ...prev.filter((session) => session.id !== activeId)].slice(0, MAX_SESSIONS);
      persistSessions(next);
      return next;
    });
  }, [messages, pending, activeId]);

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
      // 记下指针在悬浮球上的位置：抽屉的缩放动画从这个点长出来
      setOriginPoint({ x: drag.startX, y: drag.startY });
      setOpen(true);
    }
  }

  /** 新建会话：旧对话已自动进本地历史，这里换一个 id 清出白纸；回答进行中不允许，避免半截回答挂到空会话上 */
  function newChat() {
    if (pending) return;
    setActiveId(newSessionId());
    setMessages([]);
    setStatusText(null);
    setInput("");
    setSuggestPage(0);
    setView("chat");
    listRef.current?.scrollTo({ top: 0 });
  }

  /** 打开一条历史会话：内容回到对话区，接着问也行 */
  function openSession(id: string) {
    if (pending) return;
    const session = sessions.find((item) => item.id === id);
    if (!session) return;
    setActiveId(id);
    setMessages(session.messages);
    setStatusText(null);
    setInput("");
    setSuggestPage(0);
    setView("chat");
    listRef.current?.scrollTo({ top: 0 });
  }

  /** 删掉一条本地会话；删的是正在看的这段对话就回到新会话 */
  function removeSession(id: string) {
    if (pending) return;
    const next = sessions.filter((session) => session.id !== id);
    setSessions(next);
    persistSessions(next);
    if (id === activeId) {
      setActiveId(newSessionId());
      setMessages([]);
    }
  }

  /** 清空本地会话历史 */
  function clearSessions() {
    if (pending) return;
    setSessions([]);
    persistSessions([]);
    setActiveId(newSessionId());
    setMessages([]);
    setView("chat");
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
    setStatusText(null);
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
            let payload: { delta?: string; error?: string; status?: string };
            try {
              payload = JSON.parse(line.slice(5).trim()) as { delta?: string; error?: string; status?: string };
            } catch {
              continue; // 单帧损坏直接跳过，不影响其余内容
            }
            if (payload.status) setStatusText(payload.status);
            if (payload.delta) {
              received = true;
              setStatusText(null);
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
      setStatusText(null);
    }
  }

  // 登录/首次设置页不出现智能助手悬浮球
  if (isAuthPage) return null;

  const suggestStart = (suggestPage * SUGGEST_BATCH_SIZE) % SUGGESTED_QUESTIONS.length;
  const visibleSuggestions = Array.from(
    { length: Math.min(SUGGEST_BATCH_SIZE, SUGGESTED_QUESTIONS.length) },
    (_, index) => SUGGESTED_QUESTIONS[(suggestStart + index) % SUGGESTED_QUESTIONS.length],
  );

  // 抽屉缩放动画的原点：把悬浮球上指针的位置换算成抽屉内的百分比坐标，
  // 让面板看起来是从猫球的位置长出来的；拖到左半屏时抽屉左停靠，换算跟着变
  const leftDocked = Boolean(pos && pos.x < window.innerWidth / 2);
  const drawerWidth = Math.min(380, window.innerWidth);
  const drawerLeft = leftDocked ? 0 : window.innerWidth - drawerWidth;
  const clampPercent = (value: number) => `${Math.min(100, Math.max(0, value))}%`;
  const transformOrigin = originPoint
    ? `${clampPercent(((originPoint.x - drawerLeft) / drawerWidth) * 100)} ${clampPercent((originPoint.y / window.innerHeight) * 100)}`
    : leftDocked
      ? "0% 100%"
      : "100% 100%";

  return (
    <>
      <button
        type="button"
        ref={fabRef}
        className={`ai-fab${pos ? " ai-fab-moved" : ""}`}
        style={pos ? { left: pos.x, top: pos.y } : undefined}
        aria-label="智能分析助手"
        title="问 AI 助手"
        onPointerDown={onFabPointerDown}
        onPointerMove={onFabPointerMove}
        onPointerUp={onFabPointerUp}
      >
        <svg width={42} height={42} viewBox="0 0 64 64" aria-hidden>
          <DajuPeek shape="circle" />
        </svg>
      </button>
      {open && (
        <div
          className={`ai-drawer${leftDocked ? " ai-drawer-left" : ""}`}
          style={{ transformOrigin }}
          role="dialog"
          aria-label="智能分析助手"
        >
          <div className="ai-drawer-head">
            <span className="ai-drawer-avatar" aria-hidden>
              <svg width={28} height={28} viewBox="0 0 64 64">
                <DajuPeek shape="circle" />
              </svg>
            </span>
            <div className="ai-drawer-title">
              <strong>智能分析助手</strong>
              <span>{model ?? "检测价格、查渠道，随时问"}</span>
            </div>
            <button
              type="button"
              className="ai-drawer-btn"
              aria-label={view === "history" ? "返回对话" : "历史会话"}
              title={view === "history" ? "返回对话" : "历史会话"}
              onClick={() => setView((current) => (current === "history" ? "chat" : "history"))}
            >
              <IconHistory size={15} />
            </button>
            <button
              type="button"
              className="ai-drawer-btn"
              aria-label="新建会话"
              title={pending ? "回答中，稍等一下" : "新建会话"}
              disabled={pending}
              onClick={newChat}
            >
              <IconPlus size={15} />
            </button>
            <button type="button" className="ai-drawer-btn" aria-label="收起" title="收起" onClick={() => setOpen(false)}>
              <IconClose size={14} />
            </button>
          </div>
          {view === "history" ? (
            <div className="ai-drawer-list ai-history">
              {sessions.length === 0 ? (
                <div className="ai-history-empty">还没有历史会话，问一句就有了。</div>
              ) : (
                <>
                  <p className="ai-history-tip">会话只保存在这台设备上，清除浏览器数据会一并清掉。</p>
                  {sessions.map((session) => (
                    <div
                      key={session.id}
                      className={`ai-history-item${session.id === activeId ? " ai-history-active" : ""}`}
                    >
                      <button type="button" className="ai-history-open" onClick={() => openSession(session.id)}>
                        <span className="ai-history-title">{session.title}</span>
                        <span className="ai-history-time">{sessionTimeLabel(session.updatedAt)}</span>
                      </button>
                      <button
                        type="button"
                        className="ai-history-del"
                        aria-label="删除这条会话"
                        title="删除这条会话"
                        disabled={pending && session.id === activeId}
                        onClick={() => removeSession(session.id)}
                      >
                        <IconClose size={12} />
                      </button>
                    </div>
                  ))}
                  <button type="button" className="ai-history-clear" onClick={clearSessions}>
                    清空全部历史
                  </button>
                </>
              )}
            </div>
          ) : (
            <div className="ai-drawer-list" ref={listRef}>
            {messages.length === 0 && (
              <div className="ai-suggest">
                <div className="ai-greet">
                  <svg width={44} height={44} viewBox="0 0 64 64" aria-hidden>
                    <DajuPeek shape="circle" />
                  </svg>
                  <p>喵，我是智能分析助手，帮你检测价格变化。</p>
                  <span>比价、看走势、查渠道状态，从这些问题开始：</span>
                </div>
                {visibleSuggestions.map((question) => (
                  <button key={question} type="button" onClick={() => void ask(question)}>
                    {question}
                  </button>
                ))}
                <button type="button" className="ai-suggest-more" onClick={() => setSuggestPage((page) => page + 1)}>
                  🐾 换一批
                </button>
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
                    <span className="ai-typing-line">
                      <span className="ai-paws" aria-hidden>
                        <i>🐾</i>
                        <i>🐾</i>
                        <i>🐾</i>
                      </span>
                      {statusText ?? "思考中…"}
                    </span>
                  ) : null
                ) : (
                  message.content
                )}
              </div>
            ))}
            </div>
          )}
          {view === "chat" && (
          <form
            className="ai-drawer-input"
            onSubmit={(event) => {
              event.preventDefault();
              void ask(input);
            }}
          >
            <input
              value={input}
              placeholder="问问 AI：比价、走势、渠道状态…"
              onChange={(event) => setInput(event.target.value)}
              disabled={pending}
            />
            <button type="submit" disabled={pending || !input.trim()} aria-label="发送">
              发送
            </button>
          </form>
          )}
        </div>
      )}
    </>
  );
}
