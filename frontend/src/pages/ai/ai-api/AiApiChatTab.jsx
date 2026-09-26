import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import { useAuth } from "../../../contexts/AuthContext";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import MIcon from "../../../components/MIcon";
import { AiApiChatService, stripThinkingContent } from "../../../services/aiApiChat";
import { formatShortDateTime } from "../../../utils/formatDate";
import { createConversation, loadChatHistory, saveChatHistory } from "./chatHistory";
import styles from "./AiApiChatTab.module.scss";

function errorKey(error) {
  if (error?.code === "missing_api_key") return "notConfigured";
  if (error?.code === "empty_reply" || error?.code === "invalid_response") return "invalidReply";
  if (error?.status === 401 || error?.status === 403) return "keyInvalid";
  if (error?.status === 429) return "rateLimited";
  if (error?.status === 413) return "tooLong";
  if (error?.status === 408) return "timedOut";
  if (error?.status === 400 || error?.status === 404 || error?.status === 422) return "requestRejected";
  return "unavailable";
}

function Message({ message, t, streaming = false }) {
  const user = message.role === "user";
  const visibleContent = user ? message.content : stripThinkingContent(message.content);
  return <article className={`${styles.message} ${user ? styles.userMessage : ""}`}>
    <div className={`${styles.avatar} ${user ? styles.userAvatar : styles.assistantAvatar}`} aria-hidden="true">
      <MIcon name={user ? "person" : "auto_awesome"} size={17} />
    </div>
    <div className={styles.messageBody}>
      <div className={styles.messageMeta}>
        <strong>{user ? t("AiApiChat.you") : message.model}</strong>
        {!streaming && <time dateTime={message.createdAt}>{formatShortDateTime(message.createdAt)}</time>}
        {streaming && <span className={styles.streamStatus} role="status">
          <span className={styles.statusDot} />
          {t(visibleContent ? "AiApiChat.streaming" : "AiApiChat.thinking")}
        </span>}
      </div>
      <div className={user ? styles.userContent : `${styles.markdown} ${streaming && visibleContent ? styles.markdownStreaming : ""}`}>
        {user ? visibleContent : visibleContent
          ? <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>{visibleContent}</ReactMarkdown>
          : <div className={styles.thinking} aria-hidden="true"><span /><span /><span /></div>}
      </div>
    </div>
  </article>;
}

function ChatWorkspace({ userId }) {
  const { t } = useTranslation("ai");
  const confirm = useConfirm();
  const [initial] = useState(() => {
    try { return { ...loadChatHistory(userId), warning: false }; }
    catch { return { conversations: [], activeId: null, warning: true }; }
  });
  const [conversations, setConversations] = useState(initial.conversations);
  const [activeId, setActiveId] = useState(initial.activeId);
  const [storageWarning, setStorageWarning] = useState(initial.warning);
  const [models, setModels] = useState([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [modelError, setModelError] = useState(null);
  const [replyError, setReplyError] = useState(null);
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState(null);
  const [streamedReply, setStreamedReply] = useState("");
  const [selectedModel, setSelectedModel] = useState("");
  const modelRequestRef = useRef(null);
  const chatRequestRef = useRef(null);
  const inputRef = useRef(null);
  const logRef = useRef(null);
  const lastSavedRef = useRef({ conversations, activeId });
  const configured = AiApiChatService.isConfigured();
  const conversation = conversations.find((item) => item.id === activeId);
  const model = conversation?.model || selectedModel;
  const modelAvailable = models.includes(model);

  useEffect(() => {
    if (lastSavedRef.current.conversations === conversations && lastSavedRef.current.activeId === activeId) return;
    lastSavedRef.current = { conversations, activeId };
    try {
      saveChatHistory(userId, { conversations, activeId });
      setStorageWarning(false);
    } catch { setStorageWarning(true); }
  }, [conversations, activeId, userId]);

  const loadModels = useCallback(async () => {
    modelRequestRef.current?.abort();
    const controller = new AbortController();
    modelRequestRef.current = controller;
    setLoadingModels(true);
    setModelError(null);
    try {
      const nextModels = await AiApiChatService.listModels({ signal: controller.signal });
      if (controller.signal.aborted) return;
      setModels(nextModels);
      setSelectedModel((current) => nextModels.includes(current) ? current : nextModels[0] ?? "");
    } catch (error) {
      if (!controller.signal.aborted) { setModels([]); setModelError(errorKey(error)); }
    } finally {
      if (!controller.signal.aborted) setLoadingModels(false);
    }
  }, []);

  useEffect(() => {
    if (configured) loadModels();
    return () => {
      modelRequestRef.current?.abort();
      chatRequestRef.current?.abort();
    };
  }, [configured, loadModels]);

  useEffect(() => {
    const log = logRef.current;
    if (log) log.scrollTop = log.scrollHeight;
  }, [activeId, conversation?.messages.length, pending, streamedReply]);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }, [draft]);

  function chooseModel(value) {
    setSelectedModel(value);
    setReplyError(null);
    if (conversation) setConversations((items) => items.map((item) => item.id === activeId ? { ...item, model: value } : item));
  }

  function newChat() {
    if (chatRequestRef.current) return;
    setActiveId(null);
    setDraft("");
    setReplyError(null);
    setSelectedModel(modelAvailable ? model : models[0] ?? "");
    inputRef.current?.focus();
  }

  function openChat(id) {
    if (chatRequestRef.current) return;
    setActiveId(id);
    setDraft("");
    setReplyError(null);
  }

  async function deleteChat(id) {
    if (chatRequestRef.current) return;
    const ok = await confirm({
      title: t("AiApiChat.deleteTitle"), message: t("AiApiChat.deleteMessage"),
      confirmText: t("AiApiChat.delete"), danger: true,
    });
    if (!ok || chatRequestRef.current) return;
    setConversations((items) => items.filter((item) => item.id !== id));
    if (id === activeId) newChat();
  }

  async function send(event) {
    event.preventDefault();
    const content = draft.trim();
    if (!content || !modelAvailable || chatRequestRef.current || !configured || loadingModels) return;
    const target = conversation ?? createConversation(model);
    const userMessage = { id: crypto.randomUUID(), role: "user", content, createdAt: new Date().toISOString() };
    const controller = new AbortController();
    chatRequestRef.current = controller;
    setPending(userMessage);
    setStreamedReply("");
    setDraft("");
    setReplyError(null);
    try {
      const reply = await AiApiChatService.chat(model, [...target.messages, userMessage], {
        signal: controller.signal,
        onDelta: setStreamedReply,
      });
      if (controller.signal.aborted) return;
      const updated = {
        ...target, model, title: target.title || content.replace(/\s+/g, " ").slice(0, 40),
        messages: [...target.messages, userMessage, {
          id: crypto.randomUUID(), role: "assistant", content: reply, model, createdAt: new Date().toISOString(),
        }],
      };
      setConversations((items) => [updated, ...items.filter((item) => item.id !== target.id)]);
      setActiveId(target.id);
    } catch (error) {
      if (!controller.signal.aborted) { setReplyError(errorKey(error)); setDraft(content); }
    } finally {
      if (chatRequestRef.current === controller && !controller.signal.aborted) {
        chatRequestRef.current = null;
        setPending(null);
        setStreamedReply("");
        inputRef.current?.focus();
      }
    }
  }

  function stop() {
    chatRequestRef.current?.abort();
    chatRequestRef.current = null;
    setDraft(pending?.content ?? "");
    setPending(null);
    setStreamedReply("");
    setReplyError(null);
    inputRef.current?.focus();
  }

  return <section className={styles.workspace} aria-label={t("AiApiPage.tabChat")}>
    <aside className={styles.history} aria-label={t("AiApiChat.history")}>
      <button type="button" className={styles.newChat} onClick={newChat} disabled={Boolean(pending)}>
        <MIcon name="add" size={18} />{t("AiApiChat.newChat")}
      </button>
      <h2>{t("AiApiChat.history")}</h2>
      {conversations.length === 0 ? <p className={styles.historyEmpty}>{t("AiApiChat.noHistory")}</p>
        : <ul className={styles.historyList}>{conversations.map((item) => <li key={item.id}>
          <button type="button" className={`${styles.historyEntry} ${item.id === activeId ? styles.activeEntry : ""}`}
            aria-current={item.id === activeId ? "true" : undefined} onClick={() => openChat(item.id)} disabled={Boolean(pending)}>
            <span>{item.title || t("AiApiChat.newChat")}</span><small>{item.model}</small>
          </button>
          <button type="button" className={styles.deleteChat} onClick={() => deleteChat(item.id)} disabled={Boolean(pending)}
            aria-label={t("AiApiChat.deleteConversation", { title: item.title })} title={t("AiApiChat.delete")}>
            <MIcon name="delete_outline" size={17} />
          </button>
        </li>)}</ul>}
      <p className={styles.localNotice}>{t("AiApiChat.localNotice")}</p>
      {storageWarning && <p className={styles.warning} role="status">{t("AiApiChat.storageWarning")}</p>}
    </aside>
    <div className={styles.chat}>
      <div className={styles.toolbar}>
        <div className={styles.modelControl}>
          <div className={styles.modelIcon} aria-hidden="true"><MIcon name="smart_toy" size={19} /></div>
          <div className={styles.modelField}>
            <label htmlFor="api-chat-model">{t("AiApiChat.model")}</label>
            <select id="api-chat-model" value={model} onChange={(event) => chooseModel(event.target.value)}
              disabled={loadingModels || Boolean(pending) || models.length === 0}>
              {!model && <option value="">{t(loadingModels ? "AiApiChat.loadingModels" : "AiApiChat.chooseModel")}</option>}
              {model && !modelAvailable && <option value={model}>{model} — {t("AiApiChat.modelMissing")}</option>}
              {models.map((id) => <option key={id} value={id}>{id}</option>)}
            </select>
          </div>
        </div>
        <button type="button" className={styles.reload} onClick={loadModels} disabled={!configured || loadingModels || Boolean(pending)}>
          <MIcon name="refresh" size={18} /><span className={styles.reloadLabel}>{t("AiApiChat.reloadModels")}</span>
        </button>
      </div>
      {!configured && <p className={styles.error} role="alert">{t("AiApiChat.notConfigured")}</p>}
      {modelError && <p className={styles.error} role="alert">{t(`AiApiChat.${modelError}`)}</p>}
      {configured && !loadingModels && !modelError && models.length === 0 && <p className={styles.warning} role="status">{t("AiApiChat.noModels")}</p>}
      <div ref={logRef} className={styles.log} role="log" aria-label={t("AiApiChat.messages")} aria-live="polite" aria-busy={Boolean(pending)}>
        {!conversation?.messages.length && !pending ? <div className={styles.empty}>
          <div className={styles.emptyIcon} aria-hidden="true"><MIcon name="auto_awesome" size={26} /></div>
          <h2>{t("AiApiChat.emptyTitle")}</h2><p>{t("AiApiChat.emptyDescription")}</p>
        </div> : <div className={styles.messages}>
          {conversation?.messages.map((message) => <Message key={message.id} message={message} t={t} />)}
          {pending && <>
            <Message message={pending} t={t} />
            <Message message={{
              id: "streaming-reply", role: "assistant", content: streamedReply, model, createdAt: pending.createdAt,
            }} t={t} streaming />
          </>}
        </div>}
      </div>
      {replyError && <p className={styles.error} role="alert">{t(`AiApiChat.${replyError}`)}</p>}
      <form className={styles.composer} onSubmit={send}>
        <label className={styles.inputLabel} htmlFor="api-chat-input">{t("AiApiChat.inputLabel")}</label>
        <div className={styles.composerSurface}>
          <textarea id="api-chat-input" ref={inputRef} value={draft} onChange={(event) => setDraft(event.target.value)}
            rows={1} disabled={Boolean(pending)} placeholder={t("AiApiChat.placeholder")}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) send(event);
            }} />
          <div className={styles.composerActions}>
            <span>{pending ? t("AiApiChat.streamingHint") : t("AiApiChat.keyboardHint")}</span>
            {pending ? <button type="button" className={`${styles.send} ${styles.stop}`} onClick={stop}><MIcon name="stop" size={17} />{t("AiApiChat.stop")}</button>
              : <button type="submit" className={styles.send} disabled={!draft.trim() || !modelAvailable || !configured || loadingModels}>
                <MIcon name="arrow_upward" size={18} />{t("AiApiChat.send")}
              </button>}
          </div>
        </div>
      </form>
    </div>
  </section>;
}

export default function AiApiChatTab() {
  const { user } = useAuth();
  return <ChatWorkspace key={user?.id ?? "anonymous"} userId={user?.id} />;
}
