const elements = {
  form: document.querySelector("#chat-form"),
  input: document.querySelector("#message-input"),
  help: document.querySelector("#message-help"),
  count: document.querySelector("#character-count"),
  send: document.querySelector("#send-button"),
  sendLabel: document.querySelector(".send-button__label"),
  messages: document.querySelector("#messages"),
  empty: document.querySelector("#empty-state"),
  turnCount: document.querySelector("#turn-count"),
  session: document.querySelector("#session-id"),
  caller: document.querySelector("#caller"),
  callee: document.querySelector("#callee"),
  newSession: document.querySelector("#new-session"),
  responseSession: document.querySelector("#response-session-value"),
  business: document.querySelector("#business-value"),
  intent: document.querySelector("#intent-value"),
  action: document.querySelector("#action-value"),
  latency: document.querySelector("#latency"),
  responseJson: document.querySelector("#response-json"),
  requestJson: document.querySelector("#request-json"),
  copy: document.querySelector("#copy-response"),
  commandTrigger: document.querySelector("#command-trigger"),
  commandDialog: document.querySelector("#command-dialog"),
  commandSearch: document.querySelector("#command-search"),
  commandList: document.querySelector("#command-list"),
};

const state = {
  turns: 0,
  response: null,
  commandIndex: 0,
  loading: false,
  history: [],
  callStartTime: "",
};

const DEFAULTS = {
  tenantId: 1002,
  systemPrompt: "你是会员业务客服，请识别用户意图并生成回复话术",
  config: { maxReplyLen: 60, temperature: 0.1 },
};

const createSessionId = () => {
  if (window.crypto?.randomUUID) {
    return `CALL_${window.crypto.randomUUID().replaceAll("-", "").slice(0, 12).toUpperCase()}`;
  }
  const randomPart = Math.random().toString(36).slice(2, 10);
  return `CALL_${Date.now().toString(36).toUpperCase()}_${randomPart.toUpperCase()}`;
};

function formatDateTime(date = new Date()) {
  const parts = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
}

state.callStartTime = formatDateTime();

function setSending(isSending) {
  state.loading = isSending;
  elements.send.disabled = isSending;
  elements.send.dataset.state = isSending ? "loading" : "default";
  elements.sendLabel.textContent = isSending ? "请求处理中" : "发送请求";
  elements.input.setAttribute("aria-busy", String(isSending));
}

function formatTime() {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date());
}

function addMessage(role, text, meta = "") {
  elements.empty?.remove();
  const article = document.createElement("article");
  article.className = `message message--${role}`;

  const header = document.createElement("div");
  header.className = "message__meta";
  const actor = document.createElement("span");
  actor.textContent = role === "user" ? "YOU" : role === "error" ? "REQUEST ERROR" : "ENGINE";
  const detail = document.createElement("span");
  detail.textContent = meta || formatTime();
  header.append(actor, detail);

  const body = document.createElement("p");
  body.className = "message__body";
  body.textContent = text;
  article.append(header, body);
  elements.messages.append(article);
  elements.messages.scrollTo({ top: elements.messages.scrollHeight, behavior: "smooth" });
  return article;
}

function updateInspector(payload, elapsed) {
  state.response = payload;
  elements.business.textContent = payload.business || "—";
  elements.intent.textContent = payload.intent || "—";
  elements.action.textContent = payload.action || "—";
  elements.responseSession.textContent = payload.session_id || "—";
  elements.latency.textContent = `${Math.round(elapsed)} ms`;
  elements.responseJson.textContent = JSON.stringify(payload, null, 2);
  elements.copy.disabled = false;
}

function setInputError(message) {
  const hasError = Boolean(message);
  elements.input.setAttribute("aria-invalid", String(hasError));
  elements.help.textContent = message || "Enter 发送 · Shift + Enter 换行";
}

async function sendMessage(message) {
  const sessionId = elements.session.value.trim() || createSessionId();
  elements.session.value = sessionId;
  setSending(true);
  addMessage("user", message);
  state.turns += 1;
  elements.turnCount.textContent = `${state.turns} 轮请求`;
  const startedAt = performance.now();
  const requestTime = formatDateTime();
  const requestPayload = {
    sessionId,
    tenantId: DEFAULTS.tenantId,
    callInfo: {
      caller: elements.caller.value.trim(),
      callee: elements.callee.value.trim(),
      callStartTime: state.callStartTime,
    },
    systemPrompt: DEFAULTS.systemPrompt,
    historyContext: state.history,
    currentUserText: message,
    config: DEFAULTS.config,
  };
  elements.requestJson.textContent = JSON.stringify(requestPayload, null, 2);

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestPayload),
    });
    const payload = await response.json();
    if (!response.ok) {
      const detail = typeof payload.detail === "string" ? payload.detail : "服务器没有接受这次请求";
      throw new Error(detail);
    }
    const elapsed = performance.now() - startedAt;
    addMessage("assistant", payload.reply, `${payload.business} · ${payload.intent}`);
    state.history.push({
      round: state.turns,
      userText: message,
      aiText: payload.reply,
      time: requestTime,
    });
    updateInspector(payload, elapsed);
    elements.input.value = "";
    elements.count.textContent = "0 / 4000";
    elements.input.focus();
  } catch (error) {
    const messageText = error instanceof Error ? error.message : "无法连接到聊天接口";
    addMessage("error", `${messageText}。请确认服务仍在运行，然后重试。`);
    elements.send.dataset.state = "error";
    setTimeout(() => {
      if (!state.loading) elements.send.dataset.state = "default";
    }, 1200);
  } finally {
    setSending(false);
  }
}

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = elements.input.value.trim();
  if (!message) {
    setInputError("消息为空。输入一条业务请求后再发送。");
    elements.input.focus();
    return;
  }
  if (!elements.caller.value.trim() || !elements.callee.value.trim()) {
    setInputError("请先填写主叫号码和被叫号码。");
    (!elements.caller.value.trim() ? elements.caller : elements.callee).focus();
    return;
  }
  setInputError("");
  void sendMessage(message);
});

elements.input.addEventListener("input", () => {
  elements.count.textContent = `${elements.input.value.length} / 4000`;
  if (elements.input.getAttribute("aria-invalid") === "true") setInputError("");
});

elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    elements.form.requestSubmit();
  }
});

elements.session.addEventListener("change", () => {
  state.turns = 0;
  state.history = [];
  state.callStartTime = formatDateTime();
  elements.turnCount.textContent = "尚未发送消息";
});

elements.newSession.addEventListener("click", () => {
  elements.session.value = createSessionId();
  state.turns = 0;
  state.response = null;
  state.history = [];
  state.callStartTime = formatDateTime();
  elements.messages.replaceChildren();
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.id = "empty-state";
  const glyph = document.createElement("div");
  glyph.className = "empty-state__glyph";
  glyph.setAttribute("aria-hidden", "true");
  glyph.textContent = ">_";
  const title = document.createElement("h3");
  title.textContent = "新会话已就绪";
  const copy = document.createElement("p");
  copy.textContent = "上下文已切换。发送一条消息开始新的业务流程。";
  empty.append(glyph, title, copy);
  elements.messages.append(empty);
  elements.turnCount.textContent = "尚未发送消息";
  elements.business.textContent = "—";
  elements.intent.textContent = "—";
  elements.action.textContent = "—";
  elements.responseSession.textContent = "—";
  elements.latency.textContent = "— ms";
  elements.responseJson.textContent = JSON.stringify({ status: "waiting" }, null, 2);
  elements.requestJson.textContent = JSON.stringify({ status: "waiting" }, null, 2);
  elements.copy.disabled = true;
  elements.input.focus();
});

elements.copy.addEventListener("click", async () => {
  if (!state.response) return;
  try {
    await navigator.clipboard.writeText(JSON.stringify(state.response, null, 2));
    elements.copy.dataset.state = "success";
    elements.copy.querySelector("span").textContent = "已复制";
    setTimeout(() => {
      elements.copy.dataset.state = "default";
      elements.copy.querySelector("span").textContent = "复制 JSON";
    }, 2500);
  } catch {
    elements.copy.dataset.state = "error";
    elements.copy.querySelector("span").textContent = "复制失败";
  }
});

function visibleCommands() {
  return [...elements.commandList.querySelectorAll(".command-item")].filter(
    (item) => !item.hidden,
  );
}

function setActiveCommand(index) {
  const commands = visibleCommands();
  if (!commands.length) return;
  state.commandIndex = (index + commands.length) % commands.length;
  commands.forEach((item, itemIndex) => {
    const active = itemIndex === state.commandIndex;
    item.classList.toggle("is-active", active);
    item.setAttribute("aria-selected", String(active));
  });
  commands[state.commandIndex].scrollIntoView({ block: "nearest" });
}

function openCommands() {
  if (elements.commandDialog.open) return;
  elements.commandDialog.showModal();
  elements.commandSearch.value = "";
  elements.commandList.querySelectorAll(".command-item").forEach((item) => {
    item.hidden = false;
  });
  setActiveCommand(0);
  elements.commandSearch.focus();
}

function chooseCommand(item) {
  if (!item) return;
  elements.input.value = item.dataset.prompt || "";
  elements.count.textContent = `${elements.input.value.length} / 4000`;
  elements.commandDialog.close();
  elements.input.focus();
}

elements.commandTrigger.addEventListener("click", openCommands);

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    if (elements.commandDialog.open) elements.commandDialog.close();
    else openCommands();
  }
});

elements.commandDialog.addEventListener("click", (event) => {
  if (event.target === elements.commandDialog) elements.commandDialog.close();
});

elements.commandSearch.addEventListener("input", () => {
  const query = elements.commandSearch.value.trim().toLocaleLowerCase("zh-CN");
  elements.commandList.querySelectorAll(".command-item").forEach((item) => {
    item.hidden = !item.textContent.toLocaleLowerCase("zh-CN").includes(query);
  });
  setActiveCommand(0);
});

elements.commandSearch.addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown") {
    event.preventDefault();
    setActiveCommand(state.commandIndex + 1);
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    setActiveCommand(state.commandIndex - 1);
  } else if (event.key === "Enter") {
    event.preventDefault();
    chooseCommand(visibleCommands()[state.commandIndex]);
  }
});

elements.commandList.addEventListener("click", (event) => {
  chooseCommand(event.target.closest(".command-item"));
});
