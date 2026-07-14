import React, { useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Message = {
  role: "user" | "assistant" | "system";
  text: string;
};

const API_URL = "http://127.0.0.1:8000/api/chat";

function App() {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: "system",
      text: "AI Studio Enterprise v0.4: React frontend подключён к FastAPI backend.",
    },
  ]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState("universal");
  const [loading, setLoading] = useState(false);

  async function sendMessage() {
    const text = input.trim();
    if (!text || loading) return;

    setInput("");
    setMessages((prev) => [...prev, { role: "user", text }]);
    setLoading(true);

    try {
      const response = await fetch(API_URL, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message: text,
          mode,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Ошибка backend");
      }

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: data.answer,
        },
      ]);
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          text: `Ошибка: ${error instanceof Error ? error.message : String(error)}`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo">AI Studio</div>
        <div className="version">Enterprise v0.4</div>

        <nav className="nav">
          <button className="active">Чат</button>
          <button>AI Council</button>
          <button>Crypto AI</button>
          <button>Документы</button>
          <button>Код</button>
          <button>Настройки</button>
        </nav>

        <div className="hint">
          Backend: FastAPI<br />
          Frontend: React + Vite<br />
          Desktop: Tauri позже
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <h1>Чат</h1>
            <p>Запросы идут в FastAPI backend: <code>/api/chat</code></p>
          </div>

          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="universal">Универсальный</option>
            <option value="crypto">Криптотрейдинг</option>
            <option value="code">Программирование</option>
            <option value="documents">Документы</option>
          </select>
        </header>

        <section className="chat">
          {messages.map((msg, index) => (
            <div key={index} className={`message ${msg.role}`}>
              <div className="role">
                {msg.role === "user"
                  ? "Вы"
                  : msg.role === "assistant"
                  ? "AI"
                  : "System"}
              </div>
              <div className="text">{msg.text}</div>
            </div>
          ))}
          {loading && (
            <div className="message assistant">
              <div className="role">AI</div>
              <div className="text">Думаю...</div>
            </div>
          )}
        </section>

        <footer className="composer">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Введите запрос..."
            onKeyDown={(e) => {
              if (e.key === "Enter" && e.ctrlKey) {
                sendMessage();
              }
            }}
          />
          <button onClick={sendMessage} disabled={loading}>
            {loading ? "Ждём..." : "Отправить"}
          </button>
        </footer>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
