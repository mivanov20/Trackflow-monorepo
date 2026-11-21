"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { GENERIC_ERROR_MESSAGE, messageFor } from "../../../../../lib/api-client";
import { getAccessToken } from "../../../../../lib/auth";
import { inventoryApi, type SKU } from "../../../../../lib/inventory";

export default function InboundOrderPage() {
  const [products, setProducts] = useState<SKU[]>([]);
  const [skuId, setSkuId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [reference, setReference] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const loadProducts = useCallback(async () => {
    const token = getAccessToken();
    if (!token) return;
    setLoading(true);
    setLoadError("");
    try {
      const items = await inventoryApi.listProducts(token);
      setProducts(items);
      const requestedId = new URLSearchParams(window.location.search).get("skuId");
      if (requestedId && items.some((item) => item.id === Number(requestedId))) setSkuId(requestedId);
    } catch (caught) {
      setLoadError(`The product list could not be loaded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProducts();
  }, [loadProducts]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    const token = getAccessToken();
    const product = products.find((item) => item.id === Number(skuId));
    if (!token || !product) return;
    setError("");
    setMessage("");
    setSubmitting(true);
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
      setError(`The stock entry was not recorded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)} Your answers are still here.`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <header className="inventory-page-header"><div><p className="inventory-eyebrow">INVENTORY / STOCK ENTRY</p><h1>Inbound order</h1><p>Record a goods receipt from a client brand.</p></div></header>
      <section className="inventory-form-section"><form className="inventory-form" onSubmit={submit} aria-busy={loading || submitting}>
        {loading && <p className="inventory-hint" role="status">Loading products…</p>}
        {loadError && (
          <p className="inventory-alert" role="alert">{loadError} <button className="inventory-secondary-button" type="button" onClick={() => void loadProducts()}>Try again</button></p>
        )}
        <label>Product / SKU<select required value={skuId} disabled={loading || submitting || products.length === 0} onChange={(event) => setSkuId(event.target.value)}><option value="">Select a product</option>{products.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.sku} · {item.warehouse}</option>)}</select></label>
        <label>Quantity received<input type="number" min="1" step="1" required disabled={submitting} value={quantity} onChange={(event) => setQuantity(event.target.value)} /></label>
        <label>Client reference<input maxLength={120} required disabled={submitting} value={reference} onChange={(event) => setReference(event.target.value)} placeholder="e.g. PO-2024-0098" /></label>
        {message && <p className="inventory-success" role="status">{message}</p>}
        {error && <p className="inventory-alert" role="alert">{error}</p>}
        {!loading && !loadError && products.length === 0 && <p className="inventory-hint">Register SKUs through the authenticated API before receiving stock.</p>}
        <button className="inventory-primary-button" type="submit" disabled={loading || submitting || !skuId}>{submitting ? "Recording…" : "Record receipt"}</button>
      </form></section>
    </>
  );
}
