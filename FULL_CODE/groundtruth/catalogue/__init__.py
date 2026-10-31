"""The product catalogue: tolerant loading and the search index built over it."""

from .index import GENDER_KEY, KIND_KEY, SIZE_KEY, CatalogueIndex, Spec, head_nouns
from .loader import Product, load_catalogue

__all__ = ["GENDER_KEY", "KIND_KEY", "SIZE_KEY", "CatalogueIndex", "Product", "Spec", "head_nouns", "load_catalogue"]
