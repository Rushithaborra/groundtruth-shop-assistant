from .assistant import ANSWER, CLARIFY, REFUSE, Response, Session, ShoppingAssistant
from .catalogue import Product, load_catalogue
from .index import CatalogueIndex
from .policy import Section, load_policy


def build_assistant(catalogue_path: str, policy_path: str, **kwargs) -> ShoppingAssistant:
    return ShoppingAssistant(CatalogueIndex(load_catalogue(catalogue_path)), load_policy(policy_path), **kwargs)


__all__ = ["ANSWER", "CLARIFY", "REFUSE", "Response", "Session", "ShoppingAssistant", "Product",
           "CatalogueIndex", "Section", "load_catalogue", "load_policy", "build_assistant"]
