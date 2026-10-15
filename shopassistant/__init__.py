from .assistant import ANSWER, CLARIFY, REFUSE, Response, Session, ShoppingAssistant
from .catalogue import Product, load_catalogue
from .index import CatalogueIndex
from .policy import Section, load_policy
from .sessions import SessionStore


def build_assistant(catalogue_path: str, policy_path: str, **kwargs) -> ShoppingAssistant:
    assistant = ShoppingAssistant(CatalogueIndex(load_catalogue(catalogue_path)), load_policy(policy_path), **kwargs)
    assistant.warm_up()
    return assistant


__all__ = ["ANSWER", "CLARIFY", "REFUSE", "Response", "Session", "ShoppingAssistant", "Product",
           "CatalogueIndex", "Section", "SessionStore", "load_catalogue", "load_policy", "build_assistant"]
