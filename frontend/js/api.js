const API_BASE = document.querySelector('meta[name="api-base"]')?.content?.replace(/\/$/, '') || '';
const DEMO_MODE = new URLSearchParams(window.location.search).get('demo') === '1';
const DEMO_ORDERS_KEY = 'fluxo:demo-pedidos:v1';
const DEMO_PRODUCTS = [
  { id: 'P01', nome: 'Fone de ouvido Bluetooth', categoria: 'A', preco: 199.90, quantidade: 10 },
  { id: 'P02', nome: 'Teclado mecânico', categoria: 'A', preco: 349.90, quantidade: 6 },
  { id: 'P03', nome: 'Mouse sem fio', categoria: 'A', preco: 89.90, quantidade: 12 },
  { id: 'P04', nome: 'Livro Sistemas Distribuídos', categoria: 'B', preco: 189.90, quantidade: 5 },
  { id: 'P05', nome: 'Livro Redes de Computadores', categoria: 'B', preco: 159.90, quantidade: 7 },
  { id: 'P06', nome: 'Caderno universitário', categoria: 'B', preco: 24.90, quantidade: 20 },
  { id: 'P07', nome: 'Garrafa térmica', categoria: 'C', preco: 79.90, quantidade: 9 },
  { id: 'P08', nome: 'Mochila', categoria: 'C', preco: 149.90, quantidade: 4 },
  { id: 'P09', nome: 'Luminária de mesa', categoria: 'C', preco: 99.90, quantidade: 8 },
];

function demoOrders() {
  try { return JSON.parse(localStorage.getItem(DEMO_ORDERS_KEY)) || []; } catch { return []; }
}

function saveDemoOrders(orders) {
  localStorage.setItem(DEMO_ORDERS_KEY, JSON.stringify(orders));
}

export class ApiError extends Error {
  constructor(message, status = 0, details = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.details = details;
  }
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
  headers.set('Accept', 'application/json');

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  } catch (error) {
    throw new ApiError('Não foi possível acessar o servidor. Verifique se o API Gateway está em execução.', 0, error);
  }

  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json')
    ? await response.json().catch(() => null)
    : await response.text().catch(() => '');

  if (!response.ok) {
    const message = payload?.erro || payload?.error || payload?.mensagem || payload?.message || `Erro HTTP ${response.status}`;
    throw new ApiError(message, response.status, payload);
  }
  return response.status === 204 ? null : payload;
}

function first(object, keys, fallback = undefined) {
  for (const key of keys) {
    if (object?.[key] !== undefined && object[key] !== null) return object[key];
  }
  return fallback;
}

export function normalizeProduct(raw) {
  const quantidade = Number(first(raw, ['quantidade', 'availability', 'estoque', 'stock'], NaN));
  const disponivelInformado = first(raw, ['disponivel', 'available'], undefined);
  return {
    id: String(first(raw, ['id', 'produto_id', 'produtoId'], '')),
    nome: String(first(raw, ['nome', 'name'], 'Produto')),
    categoria: String(first(raw, ['categoria', 'category'], '')).toUpperCase(),
    preco: Number(first(raw, ['preco', 'preco_unitario', 'price'], 0)),
    quantidade: Number.isFinite(quantidade) ? quantidade : null,
    disponivel: disponivelInformado === undefined ? !Number.isFinite(quantidade) || quantidade > 0 : Boolean(disponivelInformado),
  };
}

export function normalizeOrder(raw) {
  const source = raw?.pedido || raw?.order || raw || {};
  const items = first(source, ['Itens', 'itens', 'items'], []);
  return {
    id: String(first(source, ['ID', 'id', 'pedido_id', 'pedidoId'], '')),
    email: String(first(source, ['Cliente', 'cliente', 'Email', 'email'], '')),
    itens: Array.isArray(items) ? items.map((item) => ({
      produto_id: String(first(item, ['ProdutoID', 'produto_id', 'produtoId', 'id'], '')),
      nome: String(first(item, ['Nome', 'nome', 'name'], 'Produto')),
      quantidade: Number(first(item, ['Quantidade', 'quantidade', 'quantity'], 1)),
      preco_unitario: Number(first(item, ['PrecoUnitario', 'preco_unitario', 'precoUnitario', 'preco', 'price'], 0)),
    })) : [],
    valor_total: Number(first(source, ['ValorTotal', 'valor_total', 'valorTotal', 'total'], 0)),
    status: String(first(source, ['Status', 'status', 'estado'], 'aguardando confirmação')),
    ...(typeof source.cancelavel === 'boolean' ? { cancelavel: source.cancelavel } : {}),
    motivo: String(first(source, ['Motivo', 'motivo', 'reason'], '')),
    detalhe: String(first(source, ['Detalhe', 'detalhe', 'details'], '')),
    criado_em: first(source, ['CriadoEm', 'criado_em', 'criadoEm', 'created_at', 'createdAt'], null),
    checkout_url: first(source, ['CheckoutURL', 'checkout_url', 'checkoutUrl', 'url_checkout', 'payment_url', 'url_pagamento'], null),
  };
}

export async function listProducts() {
  if (DEMO_MODE) return DEMO_PRODUCTS.map(normalizeProduct);
  const payload = await request('/produtos');
  const list = Array.isArray(payload) ? payload : payload?.produtos || payload?.products || [];
  return list.map(normalizeProduct).filter((product) => product.id);
}

export async function listOrders(email) {
  if (DEMO_MODE) return demoOrders().map(normalizeOrder).filter((order) => !email || order.email === email);
  const payload = await request(`/pedidos?email=${encodeURIComponent(email)}`);
  const list = Array.isArray(payload) ? payload : payload?.pedidos || payload?.orders || [];
  return list.map(normalizeOrder).filter((order) => order.id);
}

export async function createOrder(items, email) {
  if (DEMO_MODE) {
    const orderItems = items.map((item) => {
      const product = DEMO_PRODUCTS.find((candidate) => candidate.id === item.id);
      return {
        produto_id: item.id,
        nome: product?.nome || item.nome,
        quantidade: item.quantidade,
        preco_unitario: product?.preco || item.preco,
      };
    });
    const order = normalizeOrder({
      id: crypto.randomUUID().slice(0, 8),
      email,
      itens: orderItems,
      valor_total: orderItems.reduce((total, item) => total + item.quantidade * item.preco_unitario, 0),
      status: 'aguardando confirmação',
      criado_em: new Date().toISOString(),
    });
    saveDemoOrders([order, ...demoOrders()]);
    return order;
  }
  const payload = await request('/pedidos', {
    method: 'POST',
    body: JSON.stringify({
      cliente: email,
      itens: items.map((item) => ({ produto_id: item.id, quantidade: item.quantidade })),
    }),
  });
  return normalizeOrder(payload);
}

export async function cancelOrder(id) {
  if (DEMO_MODE) {
    const orders = demoOrders();
    const order = orders.find((candidate) => candidate.id === id);
    if (order) {
      order.status = 'excluído';
      order.motivo = 'usuario';
      saveDemoOrders(orders);
    }
    return null;
  }
  return request(`/pedidos/${encodeURIComponent(id)}`, { method: 'DELETE' });
}

export async function subscribePromotions(email, categories) {
  if (DEMO_MODE) return { email, categorias: categories };
  await Promise.all(categories.map((category) => request(`/interesses/${encodeURIComponent(category)}`, {
    method: 'PUT',
    body: JSON.stringify({ email }),
  })));
  return { email, categorias: categories };
}

export async function unsubscribePromotions(email, categories = []) {
  if (DEMO_MODE) return { email, categorias: categories };
  const targets = categories.length ? categories : ['A', 'B', 'C'];
  await Promise.all(targets.map((category) => request(`/interesses/${encodeURIComponent(category)}?email=${encodeURIComponent(email)}`, {
    method: 'DELETE',
  })));
  return { email, categorias: targets };
}

export function eventStreamUrl(email) {
  if (DEMO_MODE) return null;
  return `${API_BASE}/notificacoes?email=${encodeURIComponent(email)}`;
}

export function normalizeSseEvent(event) {
  let payload = {};
  try { payload = JSON.parse(event.data || '{}'); } catch { payload = { mensagem: event.data }; }
  const nested = payload.data && typeof payload.data === 'object' ? payload.data : {};
  const type = event.type === 'message'
    ? first(payload, ['event_type', 'eventType', 'tipo'], 'message')
    : event.type;
  const merged = { ...payload, ...nested };
  return {
    type,
    pedido_id: String(first(merged, ['pedido_id', 'pedidoId', 'id', 'ID'], '')),
    status: first(merged, ['status', 'estado', 'Status'], null),
    motivo: first(merged, ['motivo', 'reason', 'Motivo'], ''),
    detalhe: first(merged, ['detalhe', 'details', 'Detalhe'], ''),
    checkout_url: first(merged, ['checkout_url', 'checkoutUrl', 'url_checkout', 'payment_url', 'url_pagamento', 'CheckoutURL'], null),
    raw: merged,
  };
}
