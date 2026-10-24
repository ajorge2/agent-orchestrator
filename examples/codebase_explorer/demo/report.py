from billing import paid_total

def revenue_report(orders):
    return {"paid_revenue": paid_total(orders), "order_count": len(orders)}
