import asyncio
import json
import logging

import aio_pika

from . import config
from .events import QUEUES, Signer, canonical, exchange_for, matches

log = logging.getLogger(__name__)


class RabbitBus:
    def __init__(self, name):
        self.name = name
        self.signer = Signer(name, config.KEYS)
        self.seen = set()
        self.lock = asyncio.Lock()

    async def start(self, handler, consume=True):
        self.handler = handler
        self.connection = await aio_pika.connect_robust(config.RABBITMQ_URL, timeout=10)
        self.channel = await self.connection.channel(publisher_confirms=True)
        await self.channel.set_qos(prefetch_count=1)
        self.exchanges = {
            'eCommerce': await self.channel.declare_exchange('eCommerce', aio_pika.ExchangeType.DIRECT, durable=True),
            'Promocoes': await self.channel.declare_exchange('Promocoes', aio_pika.ExchangeType.TOPIC, durable=True),
        }
        # Declara toda a topologia antes de publicar, evitando perder eventos na partida.
        for consumer, bindings in QUEUES.items():
            queue = await self.channel.declare_queue('fila.python.' + consumer, durable=True)
            for exchange, pattern in bindings:
                await queue.bind(self.exchanges[exchange], routing_key=pattern)
            if consumer == self.name:
                self.queue = queue
        if consume:
            await self.queue.consume(self.receive)

    async def receive(self, message):
        async with self.lock:
            try:
                event = json.loads(message.body)
            except (ValueError, UnicodeDecodeError):
                await message.ack()
                return
            if not isinstance(event, dict) or not self.signer.verify(event, message.routing_key):
                log.warning('%s: assinatura inválida; evento descartado', self.name)
                await message.ack()
                return
            if event['event_id'] in self.seen:
                await message.ack()
                return
            try:
                await self.handler(event)
            except (ValueError, KeyError, TypeError):
                log.exception('Evento com dados inválidos descartado')
                await message.reject(requeue=False)
            except Exception:
                log.exception('Falha transitória; evento será entregue novamente')
                await asyncio.sleep(1)
                await message.nack(requeue=True)
            else:
                self.seen.add(event['event_id'])
                await message.ack()

    async def publish(self, event_type, data):
        event = self.signer.envelope(event_type, data)
        await self.exchanges[exchange_for(event_type)].publish(aio_pika.Message(
            canonical(event), content_type='application/json', message_id=event['event_id'],
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT), routing_key=event_type, mandatory=True)

    async def close(self):
        await self.connection.close()


class LocalBroker:
    """Transporte em memória, exclusivo da demonstração e dos testes."""
    def __init__(self):
        self.consumers = {}
        self.pending = asyncio.Queue()
        self.history = []

    async def drain(self):
        while not self.pending.empty():
            event = await self.pending.get()
            self.history.append(event)
            for name, (signer, handler) in list(self.consumers.items()):
                if any(ex == exchange_for(event['event_type']) and matches(pattern, event['event_type'])
                       for ex, pattern in QUEUES[name]):
                    if signer.verify(event, event['event_type']):
                        await handler(event)

    async def run(self):
        while True:
            await self.drain()
            await asyncio.sleep(.02)


class LocalBus:
    def __init__(self, name, broker, directory):
        self.name, self.broker = name, broker
        self.signer = Signer(name, directory)

    async def start(self, handler):
        self.broker.consumers[self.name] = (self.signer, handler)

    async def publish(self, event_type, data):
        await self.broker.pending.put(self.signer.envelope(event_type, data))

    async def close(self):
        self.broker.consumers.pop(self.name, None)
