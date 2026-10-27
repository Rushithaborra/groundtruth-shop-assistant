"""Query understanding: turn a shopper's message into intents and a search spec."""
from .parsed import Parsed
from .parser import understand

__all__ = ["Parsed", "understand"]
