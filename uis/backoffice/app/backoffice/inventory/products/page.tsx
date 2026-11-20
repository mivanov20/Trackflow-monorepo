"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError } from "../../../../lib/api-client";
import { getAccessToken } from "../../../../lib/auth";
import { inventoryApi, type SKU } from "../../../../lib/inventory";

function StockLevel({ stock }: { stock: number }) {
  // Se considera bajo el stock de 1 a 5 unidades; cero se muestra como agotado.
  const level = stock === 0 ? "out" : stock <= 5 ? "low" : "healthy";
  const label = level === "out" ? "Out of stock" : level === "low" ? "Low stock" : "Healthy";
  return <span className={`stock-indicator stock-${level}`}><span aria-hidden="true" />{label}</span>;
}

export default function ProductsPage() {
  const [products, setProducts] = useState<SKU[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = getAccessToken();
    if (!token) return;
    inventoryApi.listProducts(token)
      .then(setProducts)
      .catch((caught: unknown) => setError(caught instanceof ApiError ? caught.message : "Unable to load products."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <>
      <header className="inventory-page-header"><div><p className="inventory-eyebrow">WAREHOUSE OPERATIONS / INVENTORY</p><h1>Products</h1><p>SKU stock is shown for its assigned warehouse.</p></div><button className="inventory-secondary-button" type="button" onClick={() => window.location.reload()}>Refresh</button></header>
      {error && <p className="inventory-alert" role="alert">{error}</p>}
      <section className="inventory-table-section" aria-labelledby="products-heading">
        <h2 id="products-heading">SKU inventory</h2>
        {loading ? <p role="status">Loading products…</p> : products.length === 0 ? <p>No SKUs are registered yet.</p> : (
          <>
            <div className="inventory-table-scroll products-desktop-table"><table className="inventory-table"><thead><tr><th>Product</th><th>SKU</th><th>Client</th><th>Category</th><th>Warehouse</th><th>Current stock</th><th>Stock level</th><th>Actions</th></tr></thead>
              <tbody>{products.map((product) => <tr key={product.id}>
                <td>{product.name}</td><td className="inventory-code">{product.sku}</td><td>{product.client_name}</td><td>{product.category}</td><td>{product.warehouse === "LA" ? "Los Angeles (LA)" : "Zaragoza (ZGZ)"}</td>
                <td className="inventory-number">{product.current_stock}</td><td><StockLevel stock={product.current_stock} /></td>
                <td><div className="inventory-row-actions"><Link href={`/backoffice/inventory/orders/inbound?skuId=${product.id}`}>Receive stock</Link><Link href={`/backoffice/inventory/orders/outbound?skuId=${product.id}`}>Record exit</Link></div></td>
              </tr>)}</tbody>
            </table></div>
            <div className="inventory-product-cards">{products.map((product) => <article className="inventory-product-card" key={product.id}>
              <div className="inventory-product-card-heading"><div><h3>{product.name}</h3><p className="inventory-code">{product.sku}</p></div><StockLevel stock={product.current_stock} /></div>
              <div className="inventory-product-card-details"><span>Client <strong>{product.client_name}</strong></span><span>Category <strong>{product.category}</strong></span><span>Warehouse <strong>{product.warehouse === "LA" ? "Los Angeles (LA)" : "Zaragoza (ZGZ)"}</strong></span><span>Current stock <strong className="inventory-number">{product.current_stock}</strong></span></div>
              <div className="inventory-row-actions"><Link href={`/backoffice/inventory/orders/inbound?skuId=${product.id}`}>Receive stock</Link><Link href={`/backoffice/inventory/orders/outbound?skuId=${product.id}`}>Record exit</Link></div>
            </article>)}</div>
          </>
        )}
      </section>
    </>
  );
}