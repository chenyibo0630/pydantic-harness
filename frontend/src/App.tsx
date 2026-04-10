import { useRef, useState } from "react";
import styles from "./App.module.css";

interface Message {
  role: "user" | "assistant";
  content: string;
  toolCalls?: string[];
}

interface UsageInfo {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
}

export function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [usage, setUsage] = useState<UsageInfo | null>(null);
  const [conversationId, setConversationId] = useState(() => crypto.randomUUID());
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = input.trim();
    if (!text || streaming) return;

    const userMsg: Message = { role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setStreaming(true);
    setUsage(null);

    const assistantMsg: Message = { role: "assistant", content: "" };
    setMessages((prev) => [...prev, assistantMsg]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const resp = await fetch("/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: conversationId }),
        signal: controller.signal,
      });

      if (!resp.ok) {
        const err = await resp.json();
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = {
            role: "assistant",
            content: `Error: ${err.detail || err.error}`,
          };
          return updated;
        });
        setStreaming(false);
        return;
      }

      const reader = resp.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let currentEvent = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.trim()) continue;

          if (line.startsWith("event: ")) {
            currentEvent = line.slice(7).trim();
            continue;
          }

          if (line.startsWith("data: ") && currentEvent) {
            const data = JSON.parse(line.slice(6));
            const event = currentEvent;
            currentEvent = "";

            console.debug(`[SSE] ${event}`, data);

            if (event === "text_delta") {
              setMessages((prev) => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                updated[updated.length - 1] = {
                  ...last,
                  content: last.content + data.text,
                };
                return updated;
              });
              scrollToBottom();
            }

            if (event === "tool_call") {
              setMessages((prev) => {
                const updated = [...prev];
                const last = updated[updated.length - 1];
                updated[updated.length - 1] = {
                  ...last,
                  toolCalls: [...(last.toolCalls || []), data.tool_name],
                };
                return updated;
              });
              scrollToBottom();
            }

            if (event === "message_end" && data.usage) {
              setUsage(data.usage);
            }

            if (event === "error") {
              setMessages((prev) => {
                const updated = [...prev];
                updated[updated.length - 1] = {
                  role: "assistant",
                  content: `Error: ${data.message}`,
                };
                return updated;
              });
            }
          }
        }
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = {
            role: "assistant",
            content: `Connection error: ${err.message}`,
          };
          return updated;
        });
      }
    } finally {
      setStreaming(false);
      abortRef.current = null;
      scrollToBottom();
    }
  };

  const handleStop = () => {
    abortRef.current?.abort();
  };

  return (
    <div className={styles.container}>
      <header className={styles.header}>
        <h1>pydantic-harness</h1>
        {usage && (
          <span className={styles.usage}>
            tokens: {usage.total_tokens ?? "?"}
          </span>
        )}
      </header>

      <div className={styles.messages}>
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`${styles.message} ${styles[msg.role]}`}
          >
            <span className={styles.role}>{msg.role}</span>
            {msg.toolCalls && msg.toolCalls.length > 0 && (
              <div className={styles.toolCalls}>
                {msg.toolCalls.map((name, j) => (
                  <span key={j} className={styles.toolBadge}>{name}</span>
                ))}
              </div>
            )}
            <div className={styles.content}>{msg.content}</div>
          </div>
        ))}
        {streaming && (
          <div className={styles.typing}>
            <span />
            <span />
            <span />
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form className={styles.inputBar} onSubmit={handleSubmit}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message..."
          disabled={streaming}
          autoFocus
        />
        {streaming ? (
          <button type="button" onClick={handleStop} className={styles.stop}>
            Stop
          </button>
        ) : (
          <button type="submit" disabled={!input.trim()}>
            Send
          </button>
        )}
      </form>
    </div>
  );
}
