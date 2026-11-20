"use client";

import { FormEvent, useEffect, useState } from "react";
import { ApiError } from "../../../../../lib/api-client";
import { getAccessToken } from "../../../../../lib/auth";
import { inventoryApi, type SKU } from "../../../../../lib/inventory";

export default function InboundOrderPage() {
  const [products, setProducts] = useState<SKU[]>([]);
  const [skuId, setSkuId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [reference, setReference] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = getAccessToken();
    if (!token) return;
    inventoryApi.listProducts(token)
      .then((items) => {
        setProducts(items);
        const requestedId = new URLSearchParams(window.location.search).get("skuId");
        if (requestedId && items.some((item) => item.id === Number(requestedId))) setSkuId(requestedId);
      })
      .catch((caught: unknown) => setError(caught instanceof ApiError ? caught.message : "Unable to load SKUs."))
      .finally(() => setLoading(false));
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = getAccessToken();
    const product = products.find((item) => item.id === Number(skuId));
    if (!token || !product) return;
    setError("");
    setMessage("");
    try {
      await inventoryApi.createInboundOrder(token, {
        sku_id: product.id,
        quantity: Number(quantity),
        reference: reference.trim(),
        warehouse: product.warehouse,
      });
      setMessage(`Receipt recorded for ${product.name}.`);
      setSkuId("");
      setQuantity("");
      setReference("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to record this stock entry.");
    }
  }

  return (
    <>
      <header className="inventory-page-header"><div><p className="inventory-eyebrow">INVENTORY / STOCK ENTRY</p><h1>Inbound order</h1><p>Record a goods receipt from a client brand.</p></div></header>
      <section className="inventory-form-section"><form className="inventory-form" onSubmit={submit}>
        <label>Product / SKU<select required value={skuId} disabled={loading || products.length === 0} onChange={(event) => setSkuId(event.target.value)}><option value="">Select a product</option>{products.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.sku} · {item.warehouse}</option>)}</select></label>
        <label>Quantity received<input type="number" min="1" step="1" required value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
        <label>Client reference<input maxLength={120} required value={reference} onChange={(event) => setReference(event.target.value)} placeholder="e.g. PO-2024-0098" /></label>
        {message && <p className="inventory-success" role="status">{message}</p>}
        {error && <p className="inventory-alert" role="alert">{error}</p>}
        {!loading && products.length === 0 && !error && <p className="inventory-hint">Register SKUs through the authenticated API before receiving stock.</p>}
        <button className="inventory-primary-button" type="submit" disabled={loading || !skuId}>Record receipt</button>
      </form></section>
    </>
  );
}