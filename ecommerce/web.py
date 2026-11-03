import asyncio
import copy
import json
import logging
import re
import secrets
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import config
from .bus import LocalBroker, LocalBus, RabbitBus
from .events import generate_keys
from .services import Principal, Estoque, Pagamento, Entrega, Promocoes, Consumidor

log = logging.getLogger(__name__)
CLASSES = {'principal': Principal, 'estoque': Estoque, 'pagamento': Pagamento,
           'entrega': Entrega, 'promocoes': Promocoes, 'c1': Consumidor, 'c2': Consumidor}


def email_value(value):
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', value):
        raise ValueError('E-mail inválido')
    return value


class Item(BaseModel):
    produto_id: str
    quantidade: Annotated[int, Field(strict=True, gt=0, le=100000)]


class OrderInput(BaseModel):
    cliente: str
    itens: Annotated[list[Item], Field(min_length=1, max_length=100)]
    _email = field_validator('cliente')(email_value)


class InterestInput(BaseModel):
    email: str
    _email = field_validator('email')(email_value)


class ChargeInput(BaseModel):
    pedido_id: Annotated[str, Field(min_length=1)]
    valor_total: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    webhook_url: str
    webhook_token: Annotated[str, Field(min_length=1)]


class ResultInput(BaseModel):
    status: Literal['APROVADO', 'RECUSADO']


class WebhookInput(ResultInput):
    pedido_id: str
    cobranca_id: str


class Mock:
    def __init__(self):
        self.charges, self.by_order = {}, {}
        self.lock = asyncio.Lock()
        self.callback = self.remote_callback

    async def create(self, data):
        async with self.lock:
            if data['pedido_id'] in self.by_order:
                current = self.charges[self.by_order[data['pedido_id']]]
                if current['valor_total'] != data['valor_total']:
                    raise ValueError('Pedido já possui outra cobrança')
                # Uma tentativa HTTP anterior pode ter criado a cobrança sem resposta.
                current.update(webhook_token=data['webhook_token'], webhook_url=data['webhook_url'])
                return self.public(current)
            if data['webhook_url'] != config.WEBHOOK_URL:
                raise ValueError('URL de webhook não autorizada')
            cid = str(uuid4())
            charge = {**data, 'id': cid, 'checkout_url': config.MOCK_PUBLIC_URL + '/checkout/' + cid,
                      'status': 'PENDENTE', 'notificado': False}
            self.charges[cid] = charge
            self.by_order[data['pedido_id']] = cid
            return self.public(charge)

    @staticmethod
    def public(charge):
        return {k: v for k, v in charge.items() if k not in ('webhook_url', 'webhook_token')}

    async def remote_callback(self, charge):
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(charge['webhook_url'],
                headers={'Authorization': 'Bearer ' + charge['webhook_token']},
                json={'pedido_id': charge['pedido_id'], 'cobranca_id': charge['id'], 'status': charge['status']})
            response.raise_for_status()

    async def choose(self, cid, status):
        async with self.lock:
            charge = self.charges[cid]
            if charge['status'] not in ('PENDENTE', status):
                raise ValueError('A cobrança já possui outro resultado')
            charge['status'] = status
            if not charge['notificado']:
                await self.callback(charge)
                charge['notificado'] = True
            return self.public(charge)


def create_app(name='principal', demo=False):
    services = {}
    mock = Mock()

    @asynccontextmanager
    async def lifespan(app):
        tasks = []
        try:
            if demo:
                generate_keys(config.KEYS)
                broker = LocalBroker()
                app.state.broker = broker
                for role, cls in CLASSES.items():
                    service = cls(LocalBus(role, broker, config.KEYS))
                    services[role] = service
                    await service.bus.start(service.handle)
                services['pagamento'].create_charge = mock.create

                async def callback(charge):
                    await services['pagamento'].webhook({'pedido_id': charge['pedido_id'],
                        'cobranca_id': charge['id'], 'status': charge['status']}, charge['webhook_token'])

                mock.callback = callback
                tasks.append(asyncio.create_task(broker.run()))
                tasks.append(asyncio.create_task(services['promocoes'].run()))
            elif name != 'mock-pagamento':
                service = CLASSES[name](RabbitBus(name))
                services[name] = service
                await service.bus.start(service.handle)
                if name == 'promocoes':
                    tasks.append(asyncio.create_task(service.run()))
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for service in services.values():
                await service.close()
                await service.bus.close()

    app = FastAPI(title='E-commerce distribuído · Python', lifespan=lifespan)
    app.state.services, app.state.mock = services, mock

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse({'erro': error.detail}, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        return JSONResponse({'erro': 'Dados inválidos', 'detalhes': [e['msg'] for e in error.errors()]}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(request, error):
        return JSONResponse({'erro': str(error)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({'erro': 'Registro não encontrado'}, status_code=404)

    @app.exception_handler(PermissionError)
    async def unauthorized(request, error):
        return JSONResponse({'erro': str(error)}, status_code=401)

    @app.exception_handler(httpx.HTTPError)
    async def upstream_error(request, error):
        log.error('Falha na integração HTTP: %s', error)
        return JSONResponse({'erro': 'Serviço temporariamente indisponível; tente novamente'}, status_code=502)

    @app.get('/health')
    async def health():
        return {'status': 'ok', 'servico': name, 'transporte': 'memoria' if demo else 'rabbitmq'}

    if name == 'principal' or demo:
        @app.get('/produtos')
        async def products():
            if demo:
                stock = services['estoque'].stock
            else:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.get(config.STOCK_URL + '/list/storage')
                    response.raise_for_status()
                    stock = {p['id']: p['availability'] for p in response.json()}
            return [{**p, 'quantidade': stock.get(p['id'], 0)} for p in services['principal'].products.values()]

        @app.post('/pedidos', status_code=201)
        async def create_order(body: OrderInput):
            return await services['principal'].create(body.cliente, [i.model_dump() for i in body.itens])

        @app.get('/pedidos')
        async def list_orders(email: str = ''):
            if email:
                email = email_value(email)
            return [copy.deepcopy(p) for p in reversed(list(services['principal'].orders.values()))
                    if not email or p['cliente'] == email]

        @app.delete('/pedidos/{order_id}', status_code=202)
        async def delete_order(order_id: str):
            await services['principal'].cancel(order_id)
            return {'mensagem': 'Pedido cancelado'}

        async def interest(category, email, cancel=False):
            category = category.strip().upper()
            if category not in ('A', 'B', 'C'):
                raise HTTPException(400, 'Categoria inválida')
            await services['principal'].bus.publish('cancelar.interesse' if cancel else 'interesse.promocao',
                {'email': email_value(email), 'categoria': category})
            return {'mensagem': 'Interesse atualizado'}

        @app.put('/interesses/{category}', status_code=202)
        async def subscribe(category: str, body: InterestInput):
            return await interest(category, body.email)

        @app.delete('/interesses/{category}', status_code=202)
        async def unsubscribe(category: str, email: str):
            return await interest(category, email, True)

        @app.get('/notificacoes')
        async def notifications(request: Request, email: str):
            email = email_value(email)
            principal = services['principal']

            async def stream():
                queue = asyncio.Queue(maxsize=100)
                principal.listeners.setdefault(email, set()).add(queue)
                try:
                    yield ': conectado\n\n'
                    while not await request.is_disconnected():
                        try:
                            payload = await asyncio.wait_for(queue.get(), timeout=15)
                            yield 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'
                        except asyncio.TimeoutError:
                            yield ': ping\n\n'
                finally:
                    principal.listeners[email].discard(queue)
                    if not principal.listeners[email]:
                        del principal.listeners[email]

            return StreamingResponse(stream(), media_type='text/event-stream',
                headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    if name == 'estoque' or demo:
        @app.get('/list/storage')
        async def storage():
            return [{'id': p, 'availability': q} for p, q in services['estoque'].stock.items()]

    if name == 'pagamento' or demo:
        @app.post('/webhooks/pagamento')
        async def webhook(body: WebhookInput, request: Request):
            token = request.headers.get('Authorization', '').removeprefix('Bearer ')
            await services['pagamento'].webhook(body.model_dump(), token)
            return {'status': 'ok'}

    if name == 'mock-pagamento' or demo:
        @app.post('/api/cobrancas', status_code=201)
        async def new_charge(body: ChargeInput, request: Request):
            if not secrets.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + config.API_TOKEN):
                raise PermissionError('Credencial inválida')
            return await mock.create(body.model_dump())

        @app.get('/api/cobrancas/{cid}')
        async def charge(cid: str):
            return mock.public(mock.charges[cid])

        @app.post('/api/cobrancas/{cid}/resultado')
        async def result(cid: str, body: ResultInput, request: Request):
            origin = request.headers.get('origin')
            if origin and origin != config.MOCK_PUBLIC_URL:
                raise HTTPException(403, 'Origem inválida')
            return await mock.choose(cid, body.status)

        @app.get('/checkout/{cid}')
        async def checkout(cid: str):
            if cid not in mock.charges:
                raise HTTPException(404, 'Cobrança não encontrada')
            return FileResponse(config.ROOT / 'frontend' / 'checkout.html',
                headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})

    if name == 'principal' or demo:
        app.mount('/', StaticFiles(directory=config.ROOT / 'frontend', html=True), name='frontend')
    return app
