"""Regressão da espera pelo Estoque na inicialização do teste distribuído."""
import importlib.util
from pathlib import Path

import pytest


def smoke_module():
    path = Path(__file__).with_name('docker_smoke.py')
    spec = importlib.util.spec_from_file_location('docker_smoke', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_waits_for_stock(monkeypatch):
    smoke = smoke_module()
    attempts = []

    def request(path):
        attempts.append(path)
        if len(attempts) < 3:
            raise AssertionError(('GET', path, 502, 'stock still starting'))
        return [{'id': 'P01', 'quantidade': 7}]

    monkeypatch.setattr(smoke, 'request', request)
    monkeypatch.setattr(smoke.time, 'sleep', lambda _: None)
    assert smoke.wait_catalog() == [{'id': 'P01', 'quantidade': 7}]
    assert attempts == ['/produtos'] * 3


def test_catalog_failure_is_not_hidden(monkeypatch):
    smoke = smoke_module()
    ticks = iter([0, 0, 2])
    monkeypatch.setattr(smoke.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(smoke.time, 'sleep', lambda _: None)

    def request(path):
        raise AssertionError(('GET', path, 502, 'stock unavailable'))

    monkeypatch.setattr(smoke, 'request', request)
    with pytest.raises(AssertionError, match='stock unavailable'):
        smoke.wait_catalog(timeout=1)


def test_smoke_routes_webhook_to_payment(monkeypatch):
    """Exercita o roteiro inteiro sem contêineres, verificando o destino HTTP."""
    smoke = smoke_module()
    monkeypatch.setattr(smoke, 'PAYMENT', 'http://pagamento:8082')
    ids = iter(['cancel', 'reject', 'missing', 'approve'])
    quantities = iter([7, 6])
    ready, calls = [], []
    monkeypatch.setattr(smoke, 'create', lambda qty: {'id': next(ids)})
    monkeypatch.setattr(smoke, 'quantity', lambda: next(quantities))
    monkeypatch.setattr(smoke, 'wait_ready', ready.append)
    monkeypatch.setattr(smoke, 'wait_catalog', lambda: [])
    monkeypatch.setattr(smoke, 'wait_quantity', lambda expected: None)

    def wait_order(pid, status):
        return {'id': pid, 'checkout_url': smoke.MOCK + '/checkout/' + pid,
                'motivo': {'cancel': 'usuario', 'reject': 'pagamento_recusado',
                           'missing': 'falta_estoque'}.get(pid, ''),
                'numero_nota_fiscal': '123', 'codigo_rastreio': 'BR123'}

    def request(path, method='GET', data=None, base=smoke.GATEWAY, expected=200):
        calls.append((path, base, expected))
        if path == '/webhooks/pagamento':
            assert base == 'http://pagamento:8082'
            assert expected == 401
        elif path.startswith('/api/cobrancas/'):
            assert base == smoke.MOCK
        else:
            assert base == smoke.GATEWAY
        return {}

    monkeypatch.setattr(smoke, 'wait_order', wait_order)
    monkeypatch.setattr(smoke, 'request', request)
    smoke.main()
    assert smoke.PAYMENT in ready
    assert ('/webhooks/pagamento', smoke.PAYMENT, 401) in calls


async def test_payment_route_rejects_unauthenticated_webhook():
    import httpx
    from ecommerce.services import Pagamento
    from ecommerce.web import create_app

    app = create_app('pagamento')
    app.state.services['pagamento'] = Pagamento(None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://pagamento:8082') as client:
        response = await client.post('/webhooks/pagamento', json={
            'pedido_id': 'teste', 'cobranca_id': 'teste', 'status': 'APROVADO'})
    assert response.status_code == 401
    assert response.json()['erro'] == 'Webhook não autorizado'
