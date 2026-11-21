"use client";

import { useCallback, useEffect, useState } from "react";
import { GENERIC_ERROR_MESSAGE, messageFor } from "../../../../lib/api-client";
import { getAccessToken } from "../../../../lib/auth";
import { inventoryApi, type InventoryOrder } from "../../../../lib/inventory";

export default function OrdersHistoryPage() {
  const [orders, setOrders] = useState<InventoryOrder[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const loadOrders = useCallback(async () => {
    const token = getAccessToken();
    if (!token) return;
    setLoading(true);
    setError("");
    try {
      setOrders(await inventoryApi.listOrders(token));
    } catch (caught) {
      setError(`The stock movements could not be loaded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadOrders();
  }, [loadOrders]);

  return (
    <>
      <header className="inventory-page-header"><div><p className="inventory-eyebrow">INVENTORY / STOCK MOVEMENTS</p><h1>Orders history</h1><p>Read-only history of stock entries and exits.</p></div></header>
      <section className="inventory-table-section" aria-labelledby="orders-heading" aria-busy={loading}>
        <h2 id="orders-heading">Stock movements</h2>
        {loading ? <p className="incident-state" role="status"><span className="incident-spinner incident-spinner-dark" aria-hidden="true" /> Loading stock movements…</p> : error ? (
          <p className="incident-state incident-state-error" role="alert"><span>{error}</span><button className="inventory-secondary-button" type="button" onClick={() => void loadOrders()}>Try again</button></p>
        ) : orders.length === 0 ? <p>No stock movements have been recorded.</p> : (
          <div className="inventory-table-scroll"><table className="inventory-table"><thead><tr><th>Type</th><th>Product</th><th>SKU</th><th>Quantity</th><th>Warehouse</th><th>Reference / tracking</th><th>Created</th><th>Operative UUID</th></tr></thead>
            <tbody>{orders.map((order) => <tr key={`${order.order_type}-${order.id}`}>
              <td><span className={`movement-type movement-${order.order_type}`}>{order.order_type === "inbound" ? "Stock entry" : `Stock exit · ${order.exit_type}`}</span></td>
              <td>{order.sku?.name ?? "Unknown product"}</td><td className="inventory-code">{order.sku?.sku ?? "—"}</td><td className="inventory-number">{order.quantity}</td>
              <td>{order.warehouse === "LA" ? "Los Angeles (LA)" : "Zaragoza (ZGZ)"}</td><td>{order.reference ?? order.tracking_number ?? "Loss"}</td>
              <td>{Number.isNaN(new Date(order.created_at).getTime()) ? "—" : new Date(order.created_at).toLocaleString()}</td><td className="inventory-code">{order.user_uuid}</td>
            </tr>)}</tbody>
          </table></div>
        )}
      </section>
    </>
  );
}