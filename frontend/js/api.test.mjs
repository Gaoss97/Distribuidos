import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.document = { querySelector: () => null };
globalThis.window = { location: { search: '' } };
globalThis.localStorage = { getItem: () => null, setItem: () => {} };
globalThis.fetch = () => { throw new Error('Requisição de rede não permitida no teste'); };
const source = await readFile(new URL('./api.js', import.meta.url), 'utf8');
const { normalizeSseEvent, normalizeOrder, normalizeProduct, listProducts, listOrders, createOrder, cancelOrder, subscribePromotions, unsubscribePromotions, eventStreamUrl, ApiError } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const event = (type, data) => ({ type, data: JSON.stringify(data) });

const pedidoGo = { ID: 'p-1', Cliente: 'a+b@example.com', Itens: [{ ProdutoID: 'P01', Nome: 'Fone', Quantidade: 2, PrecoUnitario: 19.5 }], ValorTotal: 39, Status: 'aguardando pagamento', Motivo: 'estoque', Detalhe: 'detalhe', CriadoEm: '2026-01-01T00:00:00Z', CheckoutURL: 'https://checkout.example/p-1' };
const pedidoNormalizado = { id: 'p-1', email: 'a+b@example.com', itens: [{ produto_id: 'P01', nome: 'Fone', quantidade: 2, preco_unitario: 19.5 }], valor_total: 39, status: 'aguardando pagamento', motivo: 'estoque', detalhe: 'detalhe', criado_em: '2026-01-01T00:00:00Z', checkout_url: 'https://checkout.example/p-1' };

function fakeFetch(t, respond) {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, ...options });
    return respond(url, options);
  });
  return calls;
}

const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } });

test('normaliza todos os campos do Pedido Go e envelope', () => {
  assert.deepEqual(normalizeOrder(pedidoGo), pedidoNormalizado);
  assert.deepEqual(normalizeOrder({ pedido: pedidoGo }), pedidoNormalizado);
});

test('normaliza estados reais do snapshot SSE, checkout e rejeição', () => {
  for (const status of ['aguardando confirmação', 'aguardando estoque', 'aguardando pagamento', 'pagamento aprovado', 'enviado', 'excluído']) {
    const raw = { ...pedidoGo, Status: status };
    assert.deepEqual(normalizeSseEvent(event('message', raw)), {
      type: 'message', pedido_id: 'p-1', status, motivo: 'estoque', detalhe: 'detalhe', checkout_url: pedidoGo.CheckoutURL, raw,
    });
  }
  const rejeitado = { ...pedidoGo, Status: 'excluído', Motivo: 'pagamento rejeitado', Detalhe: 'cartão recusado', CheckoutURL: '' };
  assert.deepEqual(normalizeSseEvent(event('message', rejeitado)), {
    type: 'message', pedido_id: 'p-1', status: 'excluído', motivo: 'pagamento rejeitado', detalhe: 'cartão recusado', checkout_url: '', raw: rejeitado,
  });
});

test('catálogo enriquecido é normalizado e produtos sem ID são descartados', async (t) => {
  const calls = fakeFetch(t, () => json({ produtos: [{ id: 'P01', nome: 'Fone', categoria: 'a', preco: 19.5, quantidade: 2 }, { produto_id: 'P02', name: 'Livro', category: 'b', price: '10', stock: 0 }, { nome: 'sem id' }] }));
  assert.deepEqual(await listProducts(), [
    { id: 'P01', nome: 'Fone', categoria: 'A', preco: 19.5, quantidade: 2, disponivel: true },
    { id: 'P02', nome: 'Livro', categoria: 'B', preco: 10, quantidade: 0, disponivel: false },
  ]);
  assert.equal(calls[0].url, '/produtos');
  assert.equal(calls[0].method, undefined);
  assert.equal(calls[0].headers.get('accept'), 'application/json');
  assert.deepEqual(normalizeProduct({ id: 3, disponivel: false }), { id: '3', nome: 'Produto', categoria: '', preco: 0, quantidade: null, disponivel: false });
});

test('lista pedidos por email codificado e normaliza snapshots', async (t) => {
  const calls = fakeFetch(t, () => json({ pedidos: [pedidoGo, { Cliente: 'sem ID' }] }));
  assert.deepEqual(await listOrders('a+b@example.com'), [pedidoNormalizado]);
  assert.equal(calls[0].url, '/pedidos?email=a%2Bb%40example.com');
  assert.equal(calls[0].method, undefined);
  assert.equal(calls[0].headers.get('accept'), 'application/json');
  assert.equal(eventStreamUrl('a+b@example.com'), '/notificacoes?email=a%2Bb%40example.com');
});

test('cria pedido com cliente e itens e normaliza resposta', async (t) => {
  const calls = fakeFetch(t, () => json(pedidoGo, 201));
  assert.deepEqual(await createOrder([{ id: 'P01', quantidade: 2, preco: 19.5 }], pedidoGo.Cliente), pedidoNormalizado);
  assert.equal(calls[0].url, '/pedidos');
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].headers.get('content-type'), 'application/json');
  assert.equal(calls[0].headers.get('accept'), 'application/json');
  assert.deepEqual(JSON.parse(calls[0].body), { cliente: pedidoGo.Cliente, itens: [{ produto_id: 'P01', quantidade: 2 }] });
});

test('cancelamento aceita resposta 202 em texto simples', async (t) => {
  const calls = fakeFetch(t, () => new Response('cancelamento solicitado', { status: 202, headers: { 'content-type': 'text/plain' } }));
  assert.equal(await cancelOrder('p/1'), 'cancelamento solicitado');
  assert.equal(calls[0].url, '/pedidos/p%2F1');
  assert.equal(calls[0].method, 'DELETE');
  assert.equal(calls[0].headers.get('accept'), 'application/json');
});

test('interesses usam PUT com corpo e DELETE com email na query', async (t) => {
  const calls = fakeFetch(t, () => new Response(null, { status: 204 }));
  assert.deepEqual(await subscribePromotions('a+b@example.com', ['A', 'B/C']), { email: 'a+b@example.com', categorias: ['A', 'B/C'] });
  assert.deepEqual(calls.map(({ url, method }) => [url, method]), [['/interesses/A', 'PUT'], ['/interesses/B%2FC', 'PUT']]);
  for (const call of calls) {
    assert.equal(call.headers.get('content-type'), 'application/json');
    assert.equal(call.headers.get('accept'), 'application/json');
    assert.deepEqual(JSON.parse(call.body), { email: 'a+b@example.com' });
  }
  calls.length = 0;
  assert.deepEqual(await unsubscribePromotions('a+b@example.com'), { email: 'a+b@example.com', categorias: ['A', 'B', 'C'] });
  assert.deepEqual(calls.map(({ url, method }) => [url, method]), ['A', 'B', 'C'].map((category) => [`/interesses/${category}?email=a%2Bb%40example.com`, 'DELETE']));
});

test('erros JSON e HTTP preservam mensagem, status e detalhes', async (t) => {
  let response = json({ erro: 'Pedido inválido' }, 400);
  fakeFetch(t, () => response);
  await assert.rejects(createOrder([], 'x'), (error) => error instanceof ApiError && error.message === 'Pedido inválido' && error.status === 400 && error.details.erro === 'Pedido inválido');
  response = new Response('indisponível', { status: 503 });
  await assert.rejects(listProducts(), (error) => error instanceof ApiError && error.message === 'Erro HTTP 503' && error.status === 503 && error.details === 'indisponível');
});

test('normaliza snapshot completo do pedido Go', () => {
  const pedido = { ID: '123', Cliente: 'a@example.com', Itens: [], ValorTotal: 10, Status: 'confirmado', Motivo: 'teste', Detalhe: 'detalhe', CriadoEm: '2026-01-01T00:00:00Z', CheckoutURL: 'https://example.com', CobrancaID: 'c1' };
  assert.deepEqual(normalizeSseEvent(event('message', pedido)), {
    type: 'message', pedido_id: '123', status: 'confirmado', motivo: 'teste', detalhe: 'detalhe', checkout_url: 'https://example.com', raw: pedido,
  });
});

test('preserva evento tipado de pagamento', () => {
  const payload = { pedido_id: '1', checkout_url: 'https://example.com', status: 'pendente' };
  assert.deepEqual(normalizeSseEvent(event('pagamento.criado', payload)), {
    type: 'pagamento.criado', pedido_id: '1', status: 'pendente', motivo: '', detalhe: '', checkout_url: 'https://example.com', raw: payload,
  });
});

test('combina envelope data e preserva prioridade dos aliases existentes', () => {
  const payload = { event_type: 'pedido.atualizado', id: 'externo', ID: 'go', status: 'externo', data: { id: 'interno', Status: 'go', motivo: 'minúsculo', Motivo: 'go', detalhe: 'minúsculo', Detalhe: 'go', checkout_url: 'existente', CheckoutURL: 'go' } };
  const result = normalizeSseEvent(event('message', payload));
  assert.deepEqual(result, { type: 'pedido.atualizado', pedido_id: 'interno', status: 'externo', motivo: 'minúsculo', detalhe: 'minúsculo', checkout_url: 'existente', raw: { ...payload, ...payload.data } });
});

test('aceita campos lowercase existentes', () => {
  const payload = { tipo: 'pedido', pedidoId: 42, estado: 'novo', reason: 'motivo', details: 'detalhe', payment_url: 'url' };
  assert.deepEqual(normalizeSseEvent(event('message', payload)), { type: 'pedido', pedido_id: '42', status: 'novo', motivo: 'motivo', detalhe: 'detalhe', checkout_url: 'url', raw: payload });
});

test('mantém fallback para JSON malformado e pedido sem ID', () => {
  assert.deepEqual(normalizeSseEvent({ type: 'message', data: '{' }), { type: 'message', pedido_id: '', status: null, motivo: '', detalhe: '', checkout_url: null, raw: { mensagem: '{' } });
  assert.deepEqual(normalizeSseEvent(event('message', { Status: 'novo' })), { type: 'message', pedido_id: '', status: 'novo', motivo: '', detalhe: '', checkout_url: null, raw: { Status: 'novo' } });
});
