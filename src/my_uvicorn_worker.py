from uvicorn_worker import UvicornWorker

logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "format": "%(asctime)s - %(message)s",
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "format": "%(asctime)s - %(message)s",
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "root": {"level": "INFO", "handlers": ["default"]},
        "uvicorn.error": {
            "level": "INFO",
            "handlers": ["default"],
            "propagate": False,
        },
        "uvicorn.access": {
            "level": "INFO",
            "handlers": ["access"],
            "propagate": False,
        },
    },
}


class MyUvicornWorker(UvicornWorker):
    CONFIG_KWARGS = {
        "loop": "asyncio",
        "http": "auto",
        "lifespan": "on",
        "log_config": logconfig_dict,
    }
