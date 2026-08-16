__all__ = ["logger"]

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
