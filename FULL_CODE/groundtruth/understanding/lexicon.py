"""Words and phrases that signal what a shopper is asking: facts, policy, sorting, negation, greetings and the like.

English and common Hinglish spellings, matched against tokenised (lower-cased, stemmed) query words.
"""

from __future__ import annotations

import re

from ..text import STOPWORDS

PRICE_W = {"price", "kitna", "kitne", "kitni", "much", "entha", "enta", "dhara", "dara", "vela"}
STOCK_W = {
    "stock",
    "available",
    "availability",
    "milega",
    "milegi",
    "milenge",
    "instock",
    "left",
    "unda",
    "undha",
    "untunda",
    "dorukutunda",
    "dorukuthunda",
    "milta",
    "milti",
}
RATING_W = {"rating", "rated", "review", "star", "feedback"}
POLICY_W = {
    "return",
    "cash",
    "refund",
    "exchange",
    "replace",
    "replacement",
    "warranty",
    "guarantee",
    "shipping",
    "ship",
    "delivery",
    "deliver",
    "cod",
    "cancel",
    "cancellation",
    "wapas",
    "policy",
    "courier",
    "dispatch",
}
CHEAP_W = {
    "cheapest",
    "cheap",
    "cheaper",
    "sasta",
    "sasti",
    "lowest",
    "affordable",
    "takkuva",
    "thakkuva",
    "chavaka",
    "chowka",
    "saste",
}
EXPENSIVE_W = {
    "costliest",
    "costlier",
    "expensive",
    "priciest",
    "pricier",
    "mehnga",
    "mehnge",
    "mehenga",
    "mehenge",
    "mahanga",
    "mehngi",
    "mehengi",
    "premium",
    "ekkuva",
}
NEG_W = {"not", "except", "without", "excluding", "nahi", "no", "non"}
BEST_W = {
    "best",
    "top",
    "highest",
    "popular",
    "accha",
    "acchi",
    "achha",
    "achhi",
    "acha",
    "achi",
    "achchi",
    "achcha",
    "achchha",
    "badhiya",
    "badiya",
    "behtareen",
    "mast",
    "manchi",
    "manchidi",
}
# Indian shoppers' names for a backpack ("school bag"); a plain "bag" still searches the bags category
BACKPACK_PHRASE = re.compile(
    r"\b(?:(school|college|laptop|office|travel|tuition)\s*bags?|bags?\s+for\s+(college|office|travel|tuition))\b"
)
GREET_W = {"hi", "hello", "hey", "hii", "namaste", "helo"}
THANKS_W = {
    "thanks",
    "thank",
    "thankyou",
    "thx",
    "ty",
    "dhanyavad",
    "dhanyawad",
    "shukriya",
    "great",
    "cool",
    "ok",
    "okay",
    "done",
    "bye",
    "goodbye",
    "much",
    "lot",
    "alot",
    "very",
    "so",
}
# Words that only strengthen a thank-you ("thanks so much"); alone ("how much is it?") they thank no one
THANKS_INTENSIFIERS = {"much", "lot", "alot", "very", "so"}
# Asking to see what there is ("bedsheets em unnayi?", "do you have", "show me"), not asking a fact about it
BROWSE_W = {
    "unnaya",
    "unnayi",
    "unda",
    "undha",
    "have",
    "show",
    "dikhao",
    "chupinchu",
    "suggest",
    "need",
    "want",
    "chahiye",
    "kavali",
    "available",
    "option",
    "options",
    "any",
}
# "how can I return", "return kaise karu", "ela cheyali": asks for the steps, which a "How to ..." section gives
HOW_TO = re.compile(r"\bhow\s+(?:can|do|to|should|will)\b|\bkaise\b|\bela\b")
HELP_PHRASES = ("what can you do", "how does this work", "help me", "what do you do", "what can i ask", "how to use")
HELP_W = {"help", "madad"}
# Things outside the catalogue and policy: orders, accounts, payments, offers. Answered with one clear refusal.
ACCOUNT_W = {
    "track",
    "tracking",
    "coupon",
    "coupons",
    "discount",
    "discounts",
    "offer",
    "offers",
    "promo",
    "voucher",
    "login",
    "password",
    "account",
    "emi",
    "upi",
    "paytm",
    "gpay",
    "phonepe",
    "wallet balance",
    "cashback",
}
ACCOUNT_PHRASES = (
    "order status",
    "my order",
    "mera order",
    "mere order",
    "order kaha",
    "order kahan",
    "gift card",
    "store near",
    "store in",
    "shop near",
    "customer care",
    "phone number",
    "contact number",
)
WH_W = {"what", "whats", "which", "kya", "kaun", "kaunsa", "kaunsi", "kis"}
# Words that make a message a question about a product's details ("bluetooth hai?", "kitni moti hai")
QUESTION_W = {
    "hai",
    "hain",
    "kitna",
    "kitni",
    "kitne",
    "entha",
    "enta",
    "unda",
    "undha",
    "untunda",
}  # Hinglish and Telugu-English; English needs "?"
# Words that ask for the price itself; "kitna" alone may ask "how much" of something else ("kitni moti")
EXPLICIT_PRICE_W = {"price", "rate"}
# One product wanted, not a list: "sabse sasta", "cheapest", "the most expensive"
SUPERLATIVE_W = {"sabse", "subse", "sabsey", "cheapest", "lowest", "costliest", "priciest", "highest", "most", "least"}
# "tshirt or shirt, which is better": a choice between two kinds of product, not a product called "better"
CHOICE_W = {"or", "ya", "vs", "versus"}
# "grey or black", "lal ya kala", Telugu "leda": either value will do
OR_W = {"or", "ya", "leda"}
PREFERENCE_W = {"better", "behtar", "worse", "prefer", "suggest", "recommend"}
RESET_PHRASES = ("start over", "new search", "reset", "forget it", "clear all", "never mind", "nevermind")

IGNORE = (
    PRICE_W
    | STOCK_W
    | RATING_W
    | POLICY_W
    | CHEAP_W
    | EXPENSIVE_W
    | BEST_W
    | GREET_W
    | WH_W
    | STOPWORDS
    | PREFERENCE_W
    | {"size"}
)  # "what size" is a question about sizes, not a product word
