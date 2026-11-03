"""Teste de ponta a ponta contra os contêineres em execução (sem dependências)."""
import json
import os
import time
import urllib.error
import urllib.request
from uuid import uuid4

GATEWAY = os.getenv('GATEWAY_TEST_URL', 'http://localhost:3001')
MOCK = os.getenv('MOCK_TEST_URL', 'http://localhost:8083')
STOCK = os.getenv('STOCK_TEST_URL', 'http://estoque:3000')
PAYMENT = os.getenv('PAYMENT_TEST_URL', 'http://localhost:8082')
EMAIL = 'teste-' + uuid4().hex + '@example.invalid'


def request(path, method='GET', data=None, base=GATEWAY, expected=200):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(base + path, data=body, method=method,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            status, payload = response.status, response.read()
    except urllib.error.HTTPError as error:
        status, payload = error.code, error.read()
    assert status == expected, (method, path, status, payload.decode())
    return json.loads(payload) if payload else None


def wait_ready(base):
    for _ in range(90):
        try:
            result = request('/health', base=base)
            assert result['transporte'] == 'rabbitmq', result
            return
        except (OSError, AssertionError):
            time.sleep(1)
    raise AssertionError('Service did not start: ' + base)


def wait_catalog(timeout=90):
    """Espera a integração Gateway -> Estoque, além da saúde dos processos."""
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            products = request('/produtos')
        except (OSError, AssertionError) as error:
            last_error = error
            time.sleep(1)
        else:
            assert isinstance(products, list) and products, ('Empty or invalid catalog', products)
            return products
    raise AssertionError(f'Gateway cannot read stock after {timeout}s: {last_error}')


def wait_order(pid, status):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        orders = request('/pedidos?email=' + EMAIL)
        order = next(p for p in orders if p['id'] == pid)
        if order['status'] == status and (status != 'aguardando pagamento' or order['checkout_url']):
            return order
        time.sleep(.2)
    raise AssertionError(('Order did not reach expected status', status, order))


def quantity():
    return next(p['quantidade'] for p in request('/produtos') if p['id'] == 'P01')


def wait_quantity(expected):
    for _ in range(50):
        if quantity() == expected:
            return
        time.sleep(.2)
    raise AssertionError(('Unexpected stock', expected, quantity()))


def create(qty):
    return request('/pedidos', 'POST', {'cliente': EMAIL,
        'itens': [{'produto_id': 'P01', 'quantidade': qty}]}, expected=201)


def main():
    wait_ready(GATEWAY)
    wait_ready(MOCK)
    wait_ready(STOCK)
    wait_ready(PAYMENT)
    wait_catalog()
    baseline = quantity()
    assert baseline > 0, 'P01 needs at least one unit for this test'
    cancelled = create(1)
    request('/pedidos/' + cancelled['id'], 'DELETE', expected=202)
    assert wait_order(cancelled['id'], 'excluído')['motivo'] == 'usuario'
    wait_quantity(baseline)
    print('PASS: early cancellation', flush=True)

    rejected = wait_order(create(1)['id'], 'aguardando pagamento')
    cid = rejected['checkout_url'].rsplit('/', 1)[-1]
    request('/api/cobrancas/' + cid + '/resultado', 'POST', {'status': 'RECUSADO'}, base=MOCK)
    assert wait_order(rejected['id'], 'excluído')['motivo'] == 'pagamento_recusado'
    wait_quantity(baseline)
    # Retentativa da mesma decisão não pode devolver a reserva novamente.
    request('/api/cobrancas/' + cid + '/resultado', 'POST', {'status': 'RECUSADO'}, base=MOCK)
    wait_quantity(baseline)
    print('PASS: rejected payment, duplicate result, stock returned', flush=True)

    missing = create(baseline + 1)
    assert wait_order(missing['id'], 'excluído')['motivo'] == 'falta_estoque'
    wait_quantity(baseline)
    print('PASS: insufficient stock without partial reservation', flush=True)

    approved = wait_order(create(1)['id'], 'aguardando pagamento')
    cid = approved['checkout_url'].rsplit('/', 1)[-1]
    request('/webhooks/pagamento', 'POST', {'pedido_id': approved['id'],
        'cobranca_id': cid, 'status': 'APROVADO'}, base=PAYMENT, expected=401)
    request('/api/cobrancas/' + cid + '/resultado', 'POST', {'status': 'APROVADO'}, base=MOCK)
    final = wait_order(approved['id'], 'enviado')
    assert final['numero_nota_fiscal'] and final['codigo_rastreio']
    wait_quantity(baseline - 1)
    request('/pedidos/' + approved['id'], 'DELETE', expected=409)
    print('PASS: authenticated payment, delivery, invoice, tracking, cancellation blocked', flush=True)
    print(json.dumps({'result': 'PASS', 'transport': 'rabbitmq', 'email': EMAIL,
        'stock_before': baseline, 'stock_after': quantity(), 'approved_order': approved['id']}, indent=2), flush=True)


if __name__ == '__main__':
    main()
