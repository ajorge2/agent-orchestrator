"""Fictional billing example: no customer data or credentials."""
def paid_total(orders):
    return sum(order["amount"] for order in orders if order["status"] == "paid")
