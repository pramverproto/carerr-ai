import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Spin, message } from 'antd';
import { Send, Bot, User, Wrench, RotateCcw, Square } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useAuthStore } from '@/store/authStore';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  toolCalls?: { name: string; status: string }[];
}

const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';
const SESSION_PREFIX = 'career-chat-session';

function uuid(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

/** 获取或创建持久化的 session_id（按用户隔离） */
function getSessionId(userId: number | undefined): string {
  const key = userId ? `${SESSION_PREFIX}-${userId}` : SESSION_PREFIX;
  let id = localStorage.getItem(key);
  if (!id) {
    id = uuid().replace(/-/g, '').slice(0, 32);
    localStorage.setItem(key, id);
  }
  return id;
}

function setSessionId(userId: number | undefined, id: string): void {
  const key = userId ? `${SESSION_PREFIX}-${userId}` : SESSION_PREFIX;
  localStorage.setItem(key, id);
}

const STREAM_TIMEOUT_MS = 120_000; // 2 分钟超时

const Chat: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const authUser = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.token);
  const sessionIdRef = useRef<string>(getSessionId(authUser?.user_id));

  // 加载历史消息
  useEffect(() => {
    const sid = sessionIdRef.current;
    if (!token) {
      setLoadingHistory(false);
      return;
    }
    fetch(`${BASE}/chat/history?session_id=${sid}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((r) => (r.ok ? r.json() : { messages: [] }))
      .then((data) => {
        const history: ChatMessage[] = (data.messages || []).map(
          (m: { role: string; content: string }, i: number) => ({
            id: `hist-${i}`,
            role: m.role as 'user' | 'assistant',
            content: m.content,
          }),
        );
        setMessages(history);
      })
      .catch(() => { message.warning('历史消息加载失败'); })
      .finally(() => setLoadingHistory(false));
  }, [token]);

  // 自动滚动到底部
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  /** 新建对话 */
  const handleNewChat = () => {
    const newId = uuid().replace(/-/g, '').slice(0, 32);
    setSessionId(authUser?.user_id, newId);
    sessionIdRef.current = newId;
    setMessages([]);
  };

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput('');

    const userMsg: ChatMessage = {
      id: uuid(),
      role: 'user',
      content: text,
    };

    const assistantId = uuid();
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      toolCalls: [],
    };

    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;
    const timeoutId = setTimeout(() => controller.abort(), STREAM_TIMEOUT_MS);

    try {
      const response = await fetch(`${BASE}/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          message: text,
          session_id: sessionIdRef.current,
          stream: true,
        }),
        signal: controller.signal,
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const jsonStr = line.slice(6).trim();
          if (!jsonStr) continue;

          try {
            const chunk = JSON.parse(jsonStr);

            if (chunk.type === 'error') {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: chunk.content || '抱歉，AI 处理时出现错误，请稍后重试。' }
                    : m,
                ),
              );
            } else if (chunk.type === 'text') {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? { ...m, content: m.content + chunk.content }
                    : m,
                ),
              );
            } else if (chunk.type === 'tool') {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        toolCalls: [
                          ...(m.toolCalls || []),
                          { name: chunk.name, status: chunk.status },
                        ],
                      }
                    : m,
                ),
              );
            }
          } catch {
            /* 忽略解析错误 */
          }
        }
      }
    } catch (err) {
      const isAbort = err instanceof DOMException && err.name === 'AbortError';
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, content: m.content || (isAbort ? '请求超时，请稍后重试。' : '抱歉，发生了网络错误，请稍后重试。') }
            : m,
        ),
      );
    } finally {
      clearTimeout(timeoutId);
      abortRef.current = null;
      setStreaming(false);
    }
  }, [input, streaming, token]);

  if (loadingHistory) {
    return (
      <div className="flex items-center justify-center h-64">
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div className="flex flex-col -m-6" style={{ height: 'calc(100vh - 84px - 48px)' }}>
      {/* 顶部工具栏 */}
      <div className="flex items-center justify-between px-6 py-2 border-b border-gray-100">
        <span className="text-xs text-gray-400">
          {messages.length > 0 ? `${messages.length} 条消息` : '新对话'}
        </span>
        <button
          onClick={handleNewChat}
          className="flex items-center gap-1 text-xs text-gray-400 hover:text-blue-500 transition-colors"
        >
          <RotateCcw size={12} />
          <span>新对话</span>
        </button>
      </div>

      {/* 消息区域 */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-gray-400 mt-20">
            <Bot size={48} className="mx-auto mb-4 text-gray-300" />
            <p className="text-lg font-medium">你好，我是 Career AI</p>
            <p className="text-sm mt-2">
              你可以问我关于个人资料、评估历史、今日任务等问题，
              <br />
              也可以让我帮你发起评估、推荐职业或生成计划。
            </p>
            <div className="flex flex-wrap justify-center gap-2 mt-6">
              {['查看我的个人信息', '我做过哪些评估', '今天有什么任务', '帮我推荐职业'].map(
                (q) => (
                  <button
                    key={q}
                    onClick={() => setInput(q)}
                    className="text-sm px-3 py-1.5 rounded-full border border-gray-300 text-gray-500 hover:bg-gray-50 hover:text-gray-700 transition-colors"
                  >
                    {q}
                  </button>
                ),
              )}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`flex gap-3 max-w-[75%] ${msg.role === 'user' ? 'flex-row-reverse' : ''}`}
            >
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                  msg.role === 'user' ? 'bg-blue-500' : 'bg-gray-700'
                }`}
              >
                {msg.role === 'user' ? (
                  <User size={16} className="text-white" />
                ) : (
                  <Bot size={16} className="text-white" />
                )}
              </div>

              <div>
                {(msg.toolCalls || []).map((tc, i) => (
                  <div
                    key={i}
                    className="flex items-center gap-1.5 text-xs text-gray-400 mb-1"
                  >
                    <Wrench size={12} className="animate-spin" />
                    <span>正在调用 {tc.name} ...</span>
                  </div>
                ))}

                <div
                  className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                    msg.role === 'user'
                      ? 'bg-blue-500 text-white whitespace-pre-wrap'
                      : 'bg-gray-100 text-gray-800'
                  }`}
                >
                  {msg.role === 'user' ? (
                    msg.content
                  ) : msg.content ? (
                    <ReactMarkdown
                      remarkPlugins={[remarkGfm]}
                      components={{
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        h1: (p: any) => <h1 className="text-lg font-bold mt-3 mb-1">{p.children}</h1>,
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        h2: (p: any) => <h2 className="text-base font-bold mt-3 mb-1">{p.children}</h2>,
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        h3: (p: any) => <h3 className="text-sm font-bold mt-2 mb-0.5">{p.children}</h3>,
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        p: (p: any) => <p className="mb-2 last:mb-0">{p.children}</p>,
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        ul: (p: any) => <ul className="list-disc pl-4 mb-2 space-y-0.5">{p.children}</ul>,
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        ol: (p: any) => <ol className="list-decimal pl-4 mb-2 space-y-0.5">{p.children}</ol>,
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        li: (p: any) => <li className="leading-relaxed">{p.children}</li>,
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        strong: (p: any) => <strong className="font-semibold">{p.children}</strong>,
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        code: (p: any) => <code className="bg-gray-200 rounded px-1 py-0.5 text-xs font-mono">{p.children}</code>,
                        // eslint-disable-next-line @typescript-eslint/no-explicit-any
                        pre: (p: any) => <pre className="bg-gray-200 rounded p-3 text-xs font-mono overflow-x-auto mb-2">{p.children}</pre>,
                        hr: () => <hr className="my-2 border-gray-300" />,
                      }}
                    >
                      {msg.content}
                    </ReactMarkdown>
                  ) : streaming ? (
                    <Spin size="small" />
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* 输入区域 - 固定在底部 */}
      <div className="flex-shrink-0 border-t border-gray-200 px-6 py-4 bg-white rounded-b-2xl">
        <div className="flex items-center gap-3">
          <input
            className="flex-1 border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            placeholder="输入消息..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
              }
            }}
            disabled={streaming}
          />
          {streaming ? (
            <button
              onClick={() => abortRef.current?.abort()}
              className="w-10 h-10 rounded-xl bg-red-500 text-white flex items-center justify-center hover:bg-red-600 transition-colors"
              title="停止生成"
            >
              <Square size={16} />
            </button>
          ) : (
            <button
              onClick={sendMessage}
              disabled={!input.trim()}
              className="w-10 h-10 rounded-xl bg-blue-500 text-white flex items-center justify-center hover:bg-blue-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <Send size={18} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default Chat;
