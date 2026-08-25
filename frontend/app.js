const state = {
  username: null,
  items: [],
};

const elements = {
  loginView: document.querySelector("#login-view"),
  workspaceView: document.querySelector("#workspace-view"),
  loginForm: document.querySelector("#login-form"),
  loginMessage: document.querySelector("#login-message"),
  logoutButton: document.querySelector("#logout-button"),
  userBadge: document.querySelector("#user-badge"),
  connectionState: document.querySelector("#connection-state"),
  searchForm: document.querySelector("#search-form"),
  searchKeyword: document.querySelector("#search-keyword"),
  searchMaxPrice: document.querySelector("#search-max-price"),
  refreshButton: document.querySelector("#refresh-button"),
  newItemButton: document.querySelector("#new-item-button"),
  workspaceMessage: document.querySelector("#workspace-message"),
  catalogList: document.querySelector("#catalog-list"),
  resultCount: document.querySelector("#result-count"),
  lastUpdated: document.querySelector("#last-updated"),
  itemDialog: document.querySelector("#item-dialog"),
  itemForm: document.querySelector("#item-form"),
  itemMode: document.querySelector("#item-mode"),
  itemId: document.querySelector("#item-id"),
  itemName: document.querySelector("#item-name"),
  itemPrice: document.querySelector("#item-price"),
  itemCategory: document.querySelector("#item-category"),
  itemMessage: document.querySelector("#item-message"),
  dialogTitle: document.querySelector("#dialog-title"),
  closeDialogButton: document.querySelector("#close-dialog-button"),
  cancelDialogButton: document.querySelector("#cancel-dialog-button"),
};

function setMessage(element, message, isSuccess = false) {
  element.textContent = message;
  element.classList.toggle("is-success", isSuccess);
}

function setConnection(isOnline) {
  elements.connectionState.classList.toggle("is-online", isOnline);
  elements.connectionState.lastChild.textContent = isOnline ? "已连接" : "未连接";
}

function formatError(error) {
  if (error instanceof TypeError) {
    return "无法连接服务，请确认 Uvicorn 已启动。";
  }
  return error.message || "请求失败，请稍后重试。";
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) };

  const response = await fetch(path, {
    ...options,
    headers,
    credentials: "include",
  });
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }

  if (!response.ok) {
    const detail = Array.isArray(body.detail)
      ? body.detail.map((item) => item.msg).join("；")
      : body.detail;
    throw new Error(detail || `请求失败（${response.status}）`);
  }

  setConnection(true);
  return body;
}

function showWorkspace(username) {
  state.username = username;
  elements.userBadge.textContent = `${username} · 已登录`;
  elements.userBadge.classList.remove("hidden");
  elements.logoutButton.classList.remove("hidden");
  elements.loginView.classList.add("hidden");
  elements.workspaceView.classList.remove("hidden");
}

function showLogin() {
  state.username = null;
  state.items = [];
  elements.workspaceView.classList.add("hidden");
  elements.loginView.classList.remove("hidden");
  elements.userBadge.classList.add("hidden");
  elements.logoutButton.classList.add("hidden");
  elements.loginForm.reset();
  setConnection(false);
}

async function login(event) {
  event.preventDefault();
  setMessage(elements.loginMessage, "正在验证身份...");

  try {
    const body = await request("/web/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.querySelector("#login-username").value.trim(),
        password: document.querySelector("#login-password").value,
      }),
    });

    showWorkspace(body.username);
    setMessage(elements.loginMessage, "", true);
    await loadItems();
  } catch (error) {
    setConnection(false);
    setMessage(elements.loginMessage, formatError(error));
  }
}

async function restoreSession() {
  try {
    const body = await request("/web/me");
    showWorkspace(body.username);
    await loadItems();
  } catch {
    // 首次打开页面没有 Cookie 时，保持登录页即可。
    showLogin();
  }
}

async function loadItems(event) {
  if (event) event.preventDefault();
  const params = new URLSearchParams();
  const keyword = elements.searchKeyword.value.trim();
  const maxPrice = elements.searchMaxPrice.value.trim();
  if (keyword) params.set("keyword", keyword);
  if (maxPrice) params.set("max_price", maxPrice);

  const query = params.toString();
  try {
    const body = await request(`/items/search${query ? `?${query}` : ""}`);
    state.items = body.data;
    renderItems();
    elements.lastUpdated.textContent = `最近刷新：${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
    setMessage(elements.workspaceMessage, "目录已更新", true);
  } catch (error) {
    setConnection(false);
    setMessage(elements.workspaceMessage, formatError(error));
  }
}

function renderItems() {
  elements.resultCount.textContent = state.items.length;
  if (!state.items.length) {
    elements.catalogList.innerHTML = '<div class="empty-state">没有找到匹配的商品。</div>';
    return;
  }

  elements.catalogList.innerHTML = state.items.map((item) => `
    <article class="catalog-row" data-testid="catalog-row" data-item-id="${item.id}">
      <div class="item-title">
        <span class="item-id">#${item.id}</span>
        <span class="item-name" title="${escapeHtml(item.name)}">${escapeHtml(item.name)}</span>
      </div>
      <div class="item-value"><strong>¥ ${Number(item.price).toFixed(2)}</strong><small>当前价格</small></div>
      <div class="item-value"><strong>${escapeHtml(item.category)}</strong><small>商品分类</small></div>
      <div class="item-value"><strong>正常</strong><small>目录状态</small></div>
      <div class="row-actions">
        <button class="button button-secondary" type="button" data-testid="edit-item" data-action="edit" data-id="${item.id}">编辑</button>
        <button class="button button-danger" type="button" data-testid="delete-item" data-action="delete" data-id="${item.id}">删除</button>
      </div>
    </article>
  `).join("");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function openCreateDialog() {
  elements.itemMode.value = "create";
  elements.dialogTitle.textContent = "新增商品";
  elements.itemForm.reset();
  elements.itemMode.value = "create";
  elements.itemId.disabled = false;
  setMessage(elements.itemMessage, "");
  elements.itemDialog.showModal();
}

function openEditDialog(item) {
  elements.itemMode.value = "edit";
  elements.dialogTitle.textContent = `编辑商品 #${item.id}`;
  elements.itemId.value = item.id;
  elements.itemId.disabled = true;
  elements.itemName.value = item.name;
  elements.itemPrice.value = item.price;
  elements.itemCategory.value = item.category;
  setMessage(elements.itemMessage, "");
  elements.itemDialog.showModal();
}

async function saveItem(event) {
  event.preventDefault();
  const mode = elements.itemMode.value;
  const id = Number(elements.itemId.value);
  const payload = {
    name: elements.itemName.value.trim(),
    price: Number(elements.itemPrice.value),
  };

  try {
    if (mode === "create") {
      await request("/items", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, ...payload, category: elements.itemCategory.value.trim() }),
      });
      setMessage(elements.workspaceMessage, "商品已创建", true);
    } else {
      await request(`/items/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      setMessage(elements.workspaceMessage, "商品已更新", true);
    }
    elements.itemDialog.close();
    await loadItems();
  } catch (error) {
    setMessage(elements.itemMessage, formatError(error));
  }
}

async function handleCatalogAction(event) {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const item = state.items.find((entry) => entry.id === Number(button.dataset.id));
  if (!item) return;

  if (button.dataset.action === "edit") {
    openEditDialog(item);
    return;
  }

  if (button.dataset.action === "delete") {
    const confirmed = window.confirm(`确定删除“${item.name}”吗？`);
    if (!confirmed) return;
    try {
      await request(`/items/${item.id}`, { method: "DELETE" });
      await loadItems();
      setMessage(elements.workspaceMessage, `商品 #${item.id} 已删除`, true);
    } catch (error) {
      setMessage(elements.workspaceMessage, formatError(error));
    }
  }
}

async function logout() {
  try {
    await request("/web/logout", { method: "POST" });
  } finally {
    showLogin();
  }
}

elements.loginForm.addEventListener("submit", login);
elements.searchForm.addEventListener("submit", loadItems);
elements.refreshButton.addEventListener("click", loadItems);
elements.newItemButton.addEventListener("click", openCreateDialog);
elements.itemForm.addEventListener("submit", saveItem);
elements.catalogList.addEventListener("click", handleCatalogAction);
elements.logoutButton.addEventListener("click", logout);
elements.closeDialogButton.addEventListener("click", () => elements.itemDialog.close());
elements.cancelDialogButton.addEventListener("click", () => elements.itemDialog.close());

restoreSession();
