"use client";

import { useEffect, useState } from "react";
import { ApiError } from "../../../../lib/api-client";
import { getAccessToken } from "../../../../lib/auth";
import { inventoryApi, type InventoryOrder } from "../../../../lib/inventory";

export default function OrdersHistoryPage() {
  const [orders, setOrders] = useState<InventoryOrder[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = getAccessToken();
    if (!token) return;
    inventoryApi.listOrders(token)
      .then(setOrders)
      .catch((caught: unknown) => setError(caught instanceof ApiError ? caught.message : "Unable to load stock movements."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <>
      <header className="inventory-page-header"><div><p className="inventory-eyebrow">INVENTORY / STOCK MOVEMENTS</p><h1>Orders history</h1><p>Read-only history of stock entries and exits.</p></div></header>
      {error && <p className="inventory-alert" role="alert">{error}</p>}
      <section className="inventory-table-section" aria-labelledby="orders-heading">
        <h2 id="orders-heading">Stock movements</h2>
        {loading ? <p role="status">Loading stock movements…</p> : orders.length === 0 ? <p>No stock movements have been recorded.</p> : (
          <div className="inventory-table-scroll"><table className="inventory-table"><thead><tr><th>Type</th><th>Product</th><th>SKU</th><th>Quantity</th><th>Warehouse</th><th>Reference / tracking</th><th>Created</th><th>Operative UUID</th></tr></thead>
            <tbody>{orders.map((order) => <tr key={`${order.order_type}-${order.id}`}>
              <td><span className={`movement-type movement-${order.order_type}`}>{order.order_type === "inbound" ? "Stock entry" : `Stock exit · ${order.exit_type}`}</span></td>
              <td>{order.sku.name}</td><td className="inventory-code">{order.sku.sku}</td><td className="inventory-number">{order.quantity}</td>
              <td>{order.warehouse === "LA" ? "Los Angeles (LA)" : "Zaragoza (ZGZ)"}</td><td>{order.reference ?? order.tracking_number ?? "Loss"}</td>
              <td>{new Date(order.created_at).toLocaleString()}</td><td className="inventory-code">{order.user_uuid}</td>
            </tr>)}</tbody>
          </table></div>
        )}
      </section>
    </>
  );
}