"""Words and phrases that signal what a shopper is asking: facts, policy, sorting, negation, greetings and the like.

English and common Hinglish spellings, matched against tokenised (lower-cased, stemmed) query words.
"""
from __future__ import annotations

import re

from ..text import STOPWORDS

PRICE_W = {"price", "kitna", "kitne", "kitni", "much"}
STOCK_W = {"stock", "available", "availability", "milega", "milegi", "milenge", "instock", "left"}
RATING_W = {"rating", "rated", "review", "star", "feedback"}
POLICY_W = {"return", "cash", "refund", "exchange", "replace", "replacement", "warranty", "guarantee", "shipping", "ship",
            "delivery", "deliver", "cod", "cancel", "cancellation", "wapas", "policy", "courier", "dispatch"}
CHEAP_W = {"cheapest", "cheap", "cheaper", "sasta", "sasti", "lowest", "affordable"}
EXPENSIVE_W = {"costliest", "costlier", "expensive", "priciest", "pricier", "mehnga", "mehnge", "mehenga", "mehenge",
               "mahanga", "mehngi", "mehengi", "premium"}
NEG_W = {"not", "except", "without", "excluding", "nahi", "no", "non"}
BEST_W = {"best", "top", "highest", "popular", "accha", "acchi", "achha", "achhi", "acha", "achi", "badhiya", "badiya",
          "behtareen", "mast"}
# Indian shoppers' names for a backpack ("school bag"); a plain "bag" still searches the bags category
BACKPACK_PHRASE = re.compile(r"\b(school|college|laptop|office|travel|tuition)\s*bags?\b")
GREET_W = {"hi", "hello", "hey", "hii", "namaste", "helo"}
THANKS_W = {"thanks", "thank", "thankyou", "thx", "ty", "dhanyavad", "dhanyawad", "shukriya", "great", "cool", "ok",
            "okay", "done", "bye", "goodbye", "much", "lot", "alot", "very", "so"}
HELP_PHRASES = ("what can you do", "how does this work", "help me", "what do you do", "what can i ask", "how to use")
HELP_W = {"help", "madad"}
# Things outside the catalogue and policy: orders, accounts, payments, offers. Answered with one clear refusal.
ACCOUNT_W = {"track", "tracking", "coupon", "coupons", "discount", "discounts", "offer", "offers", "promo", "voucher",
             "login", "password", "account", "emi", "upi", "paytm", "gpay", "phonepe", "wallet balance", "cashback"}
ACCOUNT_PHRASES = ("order status", "my order", "mera order", "mere order", "order kaha", "order kahan", "gift card",
                   "store near", "store in", "shop near", "customer care", "phone number", "contact number")
WH_W = {"what", "whats", "which", "kya", "kaun", "kaunsa", "kaunsi", "kis"}
RESET_PHRASES = ("start over", "new search", "reset", "forget it", "clear all", "never mind", "nevermind")

IGNORE = (PRICE_W | STOCK_W | RATING_W | POLICY_W | CHEAP_W | EXPENSIVE_W | BEST_W | GREET_W | WH_W | STOPWORDS
          | {"size"})  # "what size" is a question about sizes, not a product word
