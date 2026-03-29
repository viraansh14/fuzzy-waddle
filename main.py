"""Entry point for the Polymarket trading bot."""

import logging
import sys

from polymarket_bot.bot import PolymarketBot
from polymarket_bot.config import Config


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bot.log", mode="a"),
        ],
    )
    # Quiet noisy libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)


def main():
    setup_logging()
    logger = logging.getLogger(__name__)

    config = Config()
    try:
        config.validate()
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        logger.error("Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)

    logger.info("Starting Polymarket Trading Bot...")
    bot = PolymarketBot(config)
    bot.run()


if __name__ == "__main__":
    main()
