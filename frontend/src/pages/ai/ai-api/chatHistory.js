function storageKey(userId) {
  return `campus:api-chat:v1:${userId}`;
}

export function createConversation(model = "") {
  return { id: crypto.randomUUID(), title: "", model, messages: [] };
}

export function loadChatHistory(userId) {
  if (!userId) return { conversations: [], activeId: null };
  const raw = localStorage.getItem(storageKey(userId));
  if (!raw) return { conversations: [], activeId: null };
  const data = JSON.parse(raw);
  if (!Array.isArray(data?.conversations)) throw new Error("Invalid chat history");
  const conversations = data.conversations.map((conversation) => {
    if (typeof conversation?.id !== "string" || typeof conversation.title !== "string"
      || typeof conversation.model !== "string" || !Array.isArray(conversation.messages)) {
      throw new Error("Invalid conversation");
    }
    return {
      id: conversation.id, title: conversation.title, model: conversation.model,
      messages: conversation.messages.map((message) => {
        if (typeof message?.id !== "string" || !["user", "assistant"].includes(message.role)
          || typeof message.content !== "string" || typeof message.createdAt !== "string") {
          throw new Error("Invalid message");
        }
        return {
          id: message.id, role: message.role, content: message.content, createdAt: message.createdAt,
          ...(message.role === "assistant" && typeof message.model === "string" ? { model: message.model } : {}),
        };
      }),
    };
  });
  return { conversations, activeId: conversations.some((item) => item.id === data.activeId) ? data.activeId : conversations[0]?.id ?? null };
}

export function saveChatHistory(userId, history) {
  if (userId) localStorage.setItem(storageKey(userId), JSON.stringify(history));
}
