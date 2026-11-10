from faststream import FastStream
from faststream.rabbit import RabbitBroker

from payments.core.settings import Settings, load_settings


def create_app(settings: Settings) -> FastStream:
    broker = RabbitBroker(settings.rabbitmq_url)
    return FastStream(broker)


def app_factory() -> FastStream:
    return create_app(load_settings())
