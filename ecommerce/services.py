import asyncio
import copy
import json
import logging
import random
import secrets
from decimal import Decimal
from uuid import uuid4

import httpx

from . import config
from .events import now

log = logging.getLogger(__name__)


def catalog():
    return json.loads((config.DATA / 'catalogo.json').read_text(encoding='utf-8-sig'))


class Service:
    def __init__(self, bus):
        self.bus = bus
        self.tasks = set()

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class Principal(Service):
    def __init__(self, bus):
        super().__init__(bus)
        self.orders = {}
        self.listeners = {}
        self.lock = asyncio.Lock()
        self.products = {p['id']: p for p in catalog()}

    def notify(self, order, event_type):
        payload = {'event_type': event_type, **copy.deepcopy(order), 'pedido_id': order['id']}
        for queue in self.listeners.get(order['cliente'], set()).copy():
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(payload)

    async def create(self, email, requested):
        quantities = {}
        for item in requested:
            product_id = item['produto_id'].strip().upper()
            quantity = item['quantidade']
            if product_id not in self.products or type(quantity) is not int or quantity <= 0:
                raise ValueError('Produto ou quantidade inválida')
            quantities[product_id] = quantities.get(product_id, 0) + quantity
        if not quantities:
            raise ValueError('O pedido precisa de itens')
        items = [{'produto_id': pid, 'nome': self.products[pid]['nome'], 'quantidade': qty,
                  'preco_unitario': self.products[pid]['preco']} for pid, qty in quantities.items()]
        total = sum(Decimal(str(i['preco_unitario'])) * i['quantidade'] for i in items)
        order = {'id': str(uuid4()), 'cliente': email, 'itens': items, 'valor_total': float(total),
                 'status': 'aguardando confirmação', 'motivo': '', 'detalhe': '',
                 'criado_em': now(), 'checkout_url': None, 'publicado': False, 'cancelavel': True}
        self.orders[order['id']] = order
        self.spawn(self.confirm(order))
        return copy.deepcopy(order)

    async def confirm(self, order):
        await asyncio.sleep(config.CONFIRM_SECONDS)
        # A trava impede que cancelar e publicar ocorram simultaneamente.
        while True:
            async with self.lock:
                if order['status'] == 'excluído':
                    return
                try:
                    await self.bus.publish('pedido.criado', {'pedido_id': order['id'],
                        'itens': order['itens'], 'valor_total': order['valor_total']})
                except Exception:
                    log.exception('Falha ao publicar pedido; nova tentativa em 2 s')
                else:
                    order.update(publicado=True, cancelavel=False, status='aguardando estoque')
                    self.notify(order, 'pedido.criado')
                    return
            await asyncio.sleep(2)

    async def cancel(self, order_id):
        async with self.lock:
            order = self.orders[order_id]
            if order['status'] == 'excluído':
                return
            if order['publicado']:
                raise ValueError('O cancelamento é permitido somente nos 10 segundos de confirmação')
            order.update(status='excluído', motivo='usuario', cancelavel=False)
            self.notify(order, 'pedido.excluido')

    async def handle(self, event):
        async with self.lock:
            data, kind = event['data'], event['event_type']
            order = self.orders.get(data['pedido_id'])
            if not order or order['status'] == 'excluído':
                return
            if kind in ('estoque.indisponivel', 'pagamento.recusado'):
                if order['status'] in ('pagamento aprovado', 'enviado'):
                    return
                reason = 'falta_estoque' if kind == 'estoque.indisponivel' else 'pagamento_recusado'
                await self.bus.publish('pedido.excluido', {'pedido_id': order['id'], 'motivo': reason})
                order.update(status='excluído', motivo=reason, cancelavel=False)
            elif kind == 'pedido.enviado':
                order.update(status='enviado', numero_nota_fiscal=data['numero_nota_fiscal'],
                    codigo_rastreio=data['codigo_rastreio'],
                    detalhe=f"NF {data['numero_nota_fiscal']} · Rastreio {data['codigo_rastreio']}")
            elif kind == 'pagamento.aprovado' and order['status'] != 'enviado':
                order['status'] = 'pagamento aprovado'
            elif kind in ('pedido.estoque_ok', 'pagamento.criado'):
                if order['status'] not in ('pagamento aprovado', 'enviado'):
                    order['status'] = 'aguardando pagamento'
                    if kind == 'pagamento.criado':
                        order['checkout_url'] = data['checkout_url']
            self.notify(order, kind)


class Estoque(Service):
    def __init__(self, bus):
        super().__init__(bus)
        self.stock = {p['id']: p['availability'] for p in json.loads(
            (config.DATA / 'estoque.json').read_text(encoding='utf-8-sig'))}
        self.reservations, self.responses, self.deleted = {}, {}, set()

    async def handle(self, event):
        data, kind = event['data'], event['event_type']
        pid = data['pedido_id']
        if kind == 'pedido.excluido':
            self.deleted.add(pid)
            for product_id, qty in self.reservations.pop(pid, {}).items():
                self.stock[product_id] += qty
            return
        if pid in self.deleted:
            return
        if pid not in self.responses:
            quantities = {}
            for item in data['itens']:
                qty = item['quantidade']
                if type(qty) is not int or qty <= 0:
                    raise ValueError('Quantidade inválida')
                product_id = item['produto_id']
                quantities[product_id] = quantities.get(product_id, 0) + qty
            if not quantities:
                raise ValueError('Pedido vazio')
            ok = all(self.stock.get(p, 0) >= q for p, q in quantities.items())
            if ok:
                for p, q in quantities.items():
                    self.stock[p] -= q
                self.reservations[pid] = quantities
            self.responses[pid] = ('pedido.estoque_ok' if ok else 'estoque.indisponivel',
                {'pedido_id': pid, 'valor_total': data['valor_total']})
        kind, response = self.responses[pid]
        await self.bus.publish(kind, response)


class Pagamento(Service):
    def __init__(self, bus):
        super().__init__(bus)
        self.charges = {}
        self.lock = asyncio.Lock()
        self.create_charge = self.remote_charge

    async def remote_charge(self, payload):
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(config.MOCK_URL + '/api/cobrancas', json=payload,
                headers={'Authorization': 'Bearer ' + config.API_TOKEN})
            response.raise_for_status()
            return response.json()

    async def handle(self, event):
        data = event['data']
        async with self.lock:
            pid = data['pedido_id']
            if pid not in self.charges:
                token = secrets.token_urlsafe(32)
                charge = await self.create_charge({'pedido_id': pid, 'valor_total': data['valor_total'],
                    'webhook_url': config.WEBHOOK_URL, 'webhook_token': token})
                self.charges[pid] = {**charge, 'token': token, 'published': False}
            charge = self.charges[pid]
            await self.bus.publish('pagamento.criado', {'pedido_id': pid, 'cobranca_id': charge['id'],
                'valor_total': charge['valor_total'], 'checkout_url': charge['checkout_url']})

    async def webhook(self, data, token):
        async with self.lock:
            charge = self.charges.get(data['pedido_id'])
            if not charge or not secrets.compare_digest(charge['token'], token):
                raise PermissionError('Webhook não autorizado')
            if data['cobranca_id'] != charge['id'] or data['status'] not in ('APROVADO', 'RECUSADO'):
                raise ValueError('Resultado inválido')
            if charge['status'] not in ('PENDENTE', data['status']):
                raise ValueError('A cobrança já tem outro resultado')
            charge['status'] = data['status']
            if charge['published']:
                return
            kind = 'pagamento.aprovado' if data['status'] == 'APROVADO' else 'pagamento.recusado'
            await self.bus.publish(kind, {'pedido_id': data['pedido_id'],
                'valor_total': charge['valor_total'], 'motivo': 'pagamento_recusado' if kind.endswith('recusado') else ''})
            charge['published'] = True


class Entrega(Service):
    def __init__(self, bus):
        super().__init__(bus)
        self.shipments = {}

    async def handle(self, event):
        pid = event['data']['pedido_id']
        if pid not in self.shipments:
            await asyncio.sleep(config.DELIVERY_SECONDS)
            self.shipments[pid] = {'pedido_id': pid, 'numero_nota_fiscal': str(random.randint(100000, 999999)),
                'codigo_rastreio': 'BR' + secrets.token_hex(6).upper()}
        await self.bus.publish('pedido.enviado', self.shipments[pid])


class Promocoes(Service):
    def __init__(self, bus, path=None):
        super().__init__(bus)
        self.path = path or config.DATA / 'interesses.local.json'
        self.interests = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.sent = set()
        self.products = catalog()

    async def handle(self, event):
        data, kind = event['data'], event['event_type']
        if kind in ('interesse.promocao', 'cancelar.interesse'):
            email, category = data['email'], data['categoria']
            categories = set(self.interests.get(email, []))
            if category not in 'ABC' or len(category) != 1:
                raise ValueError('Categoria inválida')
            if kind == 'interesse.promocao':
                categories.add(category)
            else:
                categories.discard(category)
            if categories:
                self.interests[email] = sorted(categories)
            else:
                self.interests.pop(email, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.interests, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(self.path)
        else:
            for email, categories in list(self.interests.items()):
                key = (event['event_id'], email)
                if data['categoria'] in categories and key not in self.sent:
                    await self.email(email, data, event['event_id'])
                    self.sent.add(key)

    async def email(self, email, promotion, event_id):
        api_key = config.env('RESEND_API_KEY', '')
        if not api_key:
            log.info('E-mail simulado para %s: %s, R$ %.2f', email, promotion['nome'], promotion['preco_promocional'])
            return
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post('https://api.resend.com/emails',
                headers={'Authorization': 'Bearer ' + api_key, 'Idempotency-Key': event_id + '-' + email},
                json={'from': config.env('PROMOCOES_EMAIL_FROM', 'E-commerce <onboarding@resend.dev>'),
                      'to': [email], 'subject': 'Oferta: ' + promotion['nome'],
                      'text': f"{promotion['nome']}: {promotion['desconto']}% de desconto. R$ {promotion['preco_promocional']:.2f}"})
            response.raise_for_status()

    async def offer(self):
        product, discount = random.choice(self.products), random.randint(5, 50)
        data = {'produto_id': product['id'], 'nome': product['nome'], 'categoria': product['categoria'],
            'preco_original': product['preco'], 'desconto': discount,
            'preco_promocional': float((Decimal(str(product['preco'])) * (100 - discount) / 100).quantize(Decimal('.01')))}
        await self.bus.publish('promocao.categoria.' + product['categoria'], data)

    async def run(self):
        while True:
            await asyncio.sleep(config.PROMOTION_SECONDS)
            try:
                await self.offer()
            except Exception:
                log.exception('Falha ao publicar promoção')


class Consumidor(Service):
    async def handle(self, event):
        log.info('%s recebeu %s: %s', self.bus.name, event['event_type'], event['data'])
