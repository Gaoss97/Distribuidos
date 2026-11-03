import {
  ApiError,
  cancelOrder,
  createOrder,
  eventStreamUrl,
  listOrders,
  listProducts,
  normalizeOrder,
  normalizeSseEvent,
  subscribePromotions,
  unsubscribePromotions,
} from './api.js?v=demo1';

const money = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
const dateTime = new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
const CART_KEY = 'fluxo:carrinho:v1';
const ORDERS_KEY = 'fluxo:pedidos:v1';
const CLIENT_EMAIL_KEY = 'fluxo:cliente-email:v1';

const statusByEvent = {
  'pedido.estoque_ok': 'aguardando pagamento',
  'pagamento.criado': 'aguardando pagamento',
  'estoque.indisponivel': 'excluído',
  'pagamento.aprovado': 'pagamento aprovado',
  'pagamento.recusado': 'excluído',
  'pedido.enviado': 'enviado',
};

const state = {
  products: [],
  orders: [],
  cart: restore(CART_KEY, []),
  email: restore(CLIENT_EMAIL_KEY, ''),
  category: 'todas',
  search: '',
  eventSource: null,
  checkoutTabs: new Map(),
};

const elements = {
  catalogFeedback: document.querySelector('#catalog-feedback'),
  productGrid: document.querySelector('#product-grid'),
  productTemplate: document.querySelector('#product-template'),
  search: document.querySelector('#product-search'),
  categoryFilters: document.querySelector('#category-filters'),
  cartCount: document.querySelector('#cart-count'),
  cartTotal: document.querySelector('#cart-total'),
  cartItems: document.querySelector('#cart-items'),
  cartDrawer: document.querySelector('#cart-drawer'),
  drawerBackdrop: document.querySelector('#drawer-backdrop'),
  checkout: document.querySelector('#checkout-button'),
  checkoutEmail: document.querySelector('#checkout-email'),
  ordersList: document.querySelector('#orders-list'),
  refreshOrders: document.querySelector('#refresh-orders'),
  promoForm: document.querySelector('#promo-form'),
  promoFeedback: document.querySelector('#promo-feedback'),
  unsubscribe: document.querySelector('#unsubscribe'),
  toastRegion: document.querySelector('#toast-region'),
};

function restore(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}

function persist(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* armazenamento indisponível */ }
}

function ordersKey() {
  return `${ORDERS_KEY}:${state.email}`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[char]);
}

function toast(message, type = 'success') {
  const item = document.createElement('div');
  item.className = 'toast';
  item.dataset.type = type;
  item.textContent = message;
  elements.toastRegion.append(item);
  window.setTimeout(() => item.remove(), 4200);
}

function messageFrom(error) {
  return error instanceof ApiError ? error.message : 'Algo não saiu como esperado. Tente novamente.';
}

function setBusy(button, busy, busyLabel = 'Aguarde…') {
  if (!button.dataset.label) button.dataset.label = button.textContent.trim();
  button.disabled = busy;
  button.textContent = busy ? busyLabel : button.dataset.label;
}

async function loadCatalog() {
  elements.catalogFeedback.hidden = false;
  elements.catalogFeedback.innerHTML = '<span class="spinner" aria-hidden="true"></span> Carregando produtos…';
  try {
    state.products = await listProducts();
    elements.catalogFeedback.hidden = true;
    renderProducts();
    renderCart();
  } catch (error) {
    elements.catalogFeedback.hidden = false;
    elements.catalogFeedback.textContent = messageFrom(error);
  }
}

function renderProducts() {
  const term = state.search.toLocaleLowerCase('pt-BR');
  const visible = state.products.filter((product) => {
    const inCategory = state.category === 'todas' || product.categoria === state.category;
    const inSearch = !term || `${product.nome} ${product.id}`.toLocaleLowerCase('pt-BR').includes(term);
    return inCategory && inSearch;
  });

  elements.productGrid.replaceChildren();
  if (!visible.length) {
    elements.productGrid.innerHTML = '<div class="empty-state">Nenhum produto encontrado com esses filtros.</div>';
    return;
  }

  for (const product of visible) {
    const fragment = elements.productTemplate.content.cloneNode(true);
    const card = fragment.querySelector('.product-card');
    card.dataset.category = product.categoria;
    fragment.querySelector('.product-code').textContent = product.id;
    fragment.querySelector('.product-name').textContent = product.nome;
    fragment.querySelector('.product-price').textContent = money.format(product.preco);
    const stock = fragment.querySelector('.stock-label');
    stock.textContent = product.disponivel
      ? (product.quantidade === null ? 'Disponível' : `${product.quantidade} em estoque`)
      : 'Indisponível';
    stock.classList.toggle('is-out', !product.disponivel);
    const button = fragment.querySelector('.add-button');
    button.disabled = !product.disponivel;
    button.setAttribute('aria-label', `Adicionar ${product.nome} ao carrinho`);
    button.addEventListener('click', () => addToCart(product));
    elements.productGrid.append(fragment);
  }
}

function addToCart(product) {
  const existing = state.cart.find((item) => item.id === product.id);
  const limit = product.quantidade ?? Number.POSITIVE_INFINITY;
  if (existing) {
    if (existing.quantidade >= limit) return toast('Quantidade máxima disponível atingida.', 'error');
    existing.quantidade += 1;
  } else {
    state.cart.push({ id: product.id, nome: product.nome, preco: product.preco, categoria: product.categoria, quantidade: 1 });
  }
  saveCart();
  toast(`${product.nome} adicionado ao carrinho.`);
}

function updateCartItem(id, delta) {
  const item = state.cart.find((entry) => entry.id === id);
  if (!item) return;
  const product = state.products.find((entry) => entry.id === id);
  const limit = product?.quantidade ?? Number.POSITIVE_INFINITY;
  item.quantidade = Math.max(0, Math.min(item.quantidade + delta, limit));
  if (item.quantidade === 0) state.cart = state.cart.filter((entry) => entry.id !== id);
  saveCart();
}

function saveCart() {
  persist(CART_KEY, state.cart);
  renderCart();
}

function renderCart() {
  const count = state.cart.reduce((sum, item) => sum + item.quantidade, 0);
  const total = state.cart.reduce((sum, item) => sum + item.preco * item.quantidade, 0);
  elements.cartCount.textContent = String(count);
  elements.cartTotal.textContent = money.format(total);
  elements.checkout.disabled = state.cart.length === 0;
  elements.cartItems.replaceChildren();

  if (!state.cart.length) {
    elements.cartItems.innerHTML = '<div class="cart-empty"><p>Seu carrinho está vazio</p><small>Escolha algo no catálogo para começar.</small></div>';
    return;
  }

  for (const item of state.cart) {
    const row = document.createElement('div');
    row.className = 'cart-item';
    row.innerHTML = `
      <div class="cart-item-info"><strong>${escapeHtml(item.nome)}</strong><small>${money.format(item.preco)} cada</small><div class="quantity-control"><button type="button" data-action="minus" aria-label="Diminuir quantidade">&minus;</button><span>${item.quantidade}</span><button type="button" data-action="plus" aria-label="Aumentar quantidade">+</button></div></div>
      <button class="remove-item" type="button" aria-label="Remover ${escapeHtml(item.nome)}">×</button>`;
    row.querySelector('[data-action="minus"]').addEventListener('click', () => updateCartItem(item.id, -1));
    row.querySelector('[data-action="plus"]').addEventListener('click', () => updateCartItem(item.id, 1));
    row.querySelector('.remove-item').addEventListener('click', () => {
      state.cart = state.cart.filter((entry) => entry.id !== item.id);
      saveCart();
    });
    elements.cartItems.append(row);
  }
}

function openCart(open) {
  elements.cartDrawer.classList.toggle('is-open', open);
  elements.cartDrawer.setAttribute('aria-hidden', String(!open));
  document.querySelector('#open-cart').setAttribute('aria-expanded', String(open));
  elements.drawerBackdrop.hidden = !open;
  document.body.style.overflow = open ? 'hidden' : '';
  if (open) document.querySelector('#close-cart').focus();
}

async function checkout() {
  if (!state.cart.length) return;
  if (!elements.checkoutEmail.checkValidity()) {
    elements.checkoutEmail.reportValidity();
    return;
  }
  state.email = elements.checkoutEmail.value.trim().toLocaleLowerCase('pt-BR');
  persist(CLIENT_EMAIL_KEY, state.email);
  connectEvents();
  const checkoutTab = window.open('', '_blank');
  if (checkoutTab) {
    checkoutTab.document.title = 'Preparando checkout…';
    checkoutTab.document.body.innerHTML = '<p style="font:16px system-ui;padding:32px">Preparando seu checkout…</p>';
  }
  setBusy(elements.checkout, true, 'Criando pedido…');
  try {
    const order = await createOrder(state.cart, state.email);
    state.orders = mergeOrder(state.orders, order);
    persist(ordersKey(), state.orders);
    state.cart = [];
    saveCart();
    renderOrders();
    openCart(false);
    toast(`Pedido ${order.id} criado com sucesso.`);
    document.querySelector('#pedidos').scrollIntoView({ behavior: 'smooth' });
    if (order.checkout_url) {
      if (checkoutTab) checkoutTab.location.replace(order.checkout_url);
      else toast('O navegador bloqueou a nova aba. Use o botão de pagamento no pedido.', 'error');
    } else {
      if (checkoutTab) {
        state.checkoutTabs.set(order.id, checkoutTab);
        checkoutTab.document.body.innerHTML = '<p style="font:16px system-ui;padding:32px">Aguardando a confirmação do estoque para abrir o pagamento…</p>';
      }
      toast('Aguardando o estoque confirmar para liberar o pagamento.');
    }
  } catch (error) {
    checkoutTab?.close();
    toast(messageFrom(error), 'error');
  } finally {
    setBusy(elements.checkout, false);
    elements.checkout.disabled = state.cart.length === 0;
  }
}

function mergeOrder(orders, incoming) {
  const normalized = normalizeOrder(incoming);
  const index = orders.findIndex((order) => order.id === normalized.id);
  if (index < 0) return [normalized, ...orders];
  const copy = [...orders];
  copy[index] = { ...copy[index], ...normalized, itens: normalized.itens.length ? normalized.itens : copy[index].itens };
  return copy;
}

async function loadOrders(showFeedback = false) {
  if (!state.email) {
    state.orders = [];
    renderOrders();
    return;
  }
  if (showFeedback) setBusy(elements.refreshOrders, true, 'Atualizando…');
  try {
    const remote = await listOrders(state.email);
    state.orders = remote;
    persist(ordersKey(), state.orders);
    renderOrders();
  } catch (error) {
    renderOrders();
    if (showFeedback || !state.orders.length) toast(messageFrom(error), 'error');
  } finally {
    if (showFeedback) setBusy(elements.refreshOrders, false);
  }
}

function statusInfo(status) {
  const value = status.toLocaleLowerCase('pt-BR');
  if (value.includes('exclu') || value.includes('recus') || value.includes('indispon')) return { label: status, tone: 'danger' };
  if (value.includes('aguard')) return { label: status, tone: 'waiting' };
  return { label: status, tone: 'success' };
}

function canCancel(order) {
  if (order.cancelavel === false) return false;
  const status = order.status.toLocaleLowerCase('pt-BR');
  return !status.includes('aprovado') && !status.includes('enviado') && !status.includes('exclu');
}

function renderOrders() {
  elements.ordersList.replaceChildren();
  if (!state.orders.length) {
    elements.ordersList.innerHTML = '<div class="empty-state">Seus pedidos aparecerão aqui assim que forem criados.</div>';
    return;
  }

  for (const order of state.orders) {
    const status = statusInfo(order.status);
    const card = document.createElement('article');
    card.className = 'order-card';
    const itemSummary = order.itens.length
      ? order.itens.map((item) => `${item.quantidade}× ${escapeHtml(item.nome)}`).join(' · ')
      : 'Itens do pedido';
    const created = order.criado_em ? dateTime.format(new Date(order.criado_em)) : 'agora';
    card.innerHTML = `
      <div class="order-identity"><small>${escapeHtml(created)}</small><strong>Pedido #${escapeHtml(order.id)}</strong><span class="status-pill" data-tone="${status.tone}">${escapeHtml(status.label)}</span></div>
      <div class="order-details"><p>${itemSummary}</p>${order.detalhe ? `<p>${escapeHtml(order.detalhe)}</p>` : ''}${order.motivo ? `<p>Motivo: ${escapeHtml(order.motivo)}</p>` : ''}</div>
      <div class="order-actions"><span class="order-price">${money.format(order.valor_total)}</span>${order.checkout_url ? '<button class="order-button pay" type="button">Ir para pagamento</button>' : ''}${canCancel(order) ? '<button class="order-button danger cancel" type="button">Cancelar</button>' : ''}</div>`;
    card.querySelector('.pay')?.addEventListener('click', () => window.open(order.checkout_url, '_blank', 'noopener,noreferrer'));
    card.querySelector('.cancel')?.addEventListener('click', (event) => handleCancelOrder(order, event.currentTarget));
    elements.ordersList.append(card);
  }
}

async function handleCancelOrder(order, button) {
  if (!window.confirm(`Cancelar o pedido #${order.id}?`)) return;
  setBusy(button, true, 'Cancelando…');
  try {
    await cancelOrder(order.id);
    order.status = 'excluído';
    order.motivo = 'usuario';
    persist(ordersKey(), state.orders);
    renderOrders();
    toast(`Pedido ${order.id} cancelado.`);
  } catch (error) {
    toast(messageFrom(error), 'error');
    setBusy(button, false);
  }
}

async function handlePromotionSubmit(event) {
  event.preventDefault();
  const form = new FormData(elements.promoForm);
  const email = String(form.get('email') || '').trim();
  const categories = form.getAll('categorias');
  if (!categories.length) {
    elements.promoFeedback.textContent = 'Selecione pelo menos uma categoria.';
    return;
  }
  const button = elements.promoForm.querySelector('[type="submit"]');
  setBusy(button, true, 'Salvando…');
  try {
    await subscribePromotions(email, categories);
    elements.promoFeedback.textContent = 'Tudo certo! Seus interesses foram registrados.';
    toast('Preferências de promoções salvas.');
  } catch (error) {
    elements.promoFeedback.textContent = messageFrom(error);
  } finally {
    setBusy(button, false);
  }
}

async function handleUnsubscribe() {
  const form = new FormData(elements.promoForm);
  const email = String(form.get('email') || '').trim();
  if (!email || !elements.promoForm.querySelector('#promo-email').checkValidity()) {
    elements.promoFeedback.textContent = 'Informe o e-mail da inscrição que deseja cancelar.';
    elements.promoForm.querySelector('#promo-email').focus();
    return;
  }
  setBusy(elements.unsubscribe, true, 'Cancelando…');
  try {
    await unsubscribePromotions(email, form.getAll('categorias'));
    elements.promoFeedback.textContent = 'Inscrição cancelada para este e-mail.';
    toast('Inscrição de promoções cancelada.');
  } catch (error) {
    elements.promoFeedback.textContent = messageFrom(error);
  } finally {
    setBusy(elements.unsubscribe, false);
  }
}

function connectEvents() {
  state.eventSource?.close();
  if (!state.email) {
    return;
  }
  const url = eventStreamUrl(state.email);
  if (!url) {
    return;
  }
  const source = new EventSource(url);
  state.eventSource = source;
  source.onmessage = handleSse;
  for (const type of Object.keys(statusByEvent)) source.addEventListener(type, handleSse);
}

function handleSse(event) {
  const update = normalizeSseEvent(event);
  if (!update.pedido_id) return;
  const order = state.orders.find((item) => item.id === update.pedido_id);
  if (!order) {
    loadOrders(false);
    return;
  }
  order.status = update.status || statusByEvent[update.type] || order.status;
  if (typeof update.raw.cancelavel === 'boolean') order.cancelavel = update.raw.cancelavel;
  order.motivo = update.motivo || order.motivo;
  order.detalhe = update.detalhe || order.detalhe;
  order.checkout_url = update.checkout_url || order.checkout_url;
  const checkoutTab = state.checkoutTabs.get(order.id);
  if (checkoutTab && order.checkout_url) {
    checkoutTab.location.replace(order.checkout_url);
    state.checkoutTabs.delete(order.id);
  } else if (checkoutTab && order.status === 'excluído') {
    checkoutTab.close();
    state.checkoutTabs.delete(order.id);
  }
  persist(ordersKey(), state.orders);
  renderOrders();
  toast(`Pedido ${order.id}: ${order.status}.`);
}

function bindEvents() {
  document.querySelector('#open-cart').addEventListener('click', () => openCart(true));
  document.querySelector('#close-cart').addEventListener('click', () => openCart(false));
  elements.drawerBackdrop.addEventListener('click', () => openCart(false));
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') openCart(false); });
  elements.checkout.addEventListener('click', checkout);
  elements.checkoutEmail.addEventListener('change', () => {
    if (!elements.checkoutEmail.checkValidity()) return;
    state.email = elements.checkoutEmail.value.trim().toLocaleLowerCase('pt-BR');
    persist(CLIENT_EMAIL_KEY, state.email);
    state.orders = restore(ordersKey(), []);
    renderOrders();
    loadOrders(false);
    connectEvents();
  });
  elements.refreshOrders.addEventListener('click', () => loadOrders(true));
  elements.promoForm.addEventListener('submit', handlePromotionSubmit);
  elements.unsubscribe.addEventListener('click', handleUnsubscribe);
  elements.search.addEventListener('input', () => { state.search = elements.search.value.trim(); renderProducts(); });
  elements.categoryFilters.addEventListener('click', (event) => {
    const button = event.target.closest('[data-category]');
    if (!button) return;
    state.category = button.dataset.category;
    elements.categoryFilters.querySelectorAll('.filter-chip').forEach((chip) => chip.classList.toggle('is-active', chip === button));
    renderProducts();
  });
  document.querySelectorAll('.nav-link').forEach((link) => link.addEventListener('click', () => {
    document.querySelectorAll('.nav-link').forEach((item) => item.classList.toggle('is-active', item === link));
  }));
}

bindEvents();
elements.checkoutEmail.value = state.email;
state.orders = state.email ? restore(ordersKey(), []) : [];
renderCart();
renderOrders();
loadCatalog();
loadOrders(false);
connectEvents();
