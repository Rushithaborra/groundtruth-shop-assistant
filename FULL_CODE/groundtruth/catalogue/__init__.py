"""The product catalogue: tolerant loading and the search index built over it."""

from .index import GENDER_KEY, SIZE_KEY, CatalogueIndex, Spec
from .loader import Product, load_catalogue

__all__ = ["GENDER_KEY", "SIZE_KEY", "CatalogueIndex", "Product", "Spec", "load_catalogue"]
