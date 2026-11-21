"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { GENERIC_ERROR_MESSAGE, messageFor } from "../../../../../lib/api-client";
import { getAccessToken } from "../../../../../lib/auth";
import { inventoryApi, type ExitType, type SKU } from "../../../../../lib/inventory";

export default function OutboundOrderPage() {
  const [products, setProducts] = useState<SKU[]>([]);
  const [productsLoading, setProductsLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [skuId, setSkuId] = useState("");
  const [selectedProduct, setSelectedProduct] = useState<SKU | null>(null);
  const [quantity, setQuantity] = useState("");
  const [exitType, setExitType] = useState<ExitType>("dispatch");
  const [trackingNumber, setTrackingNumber] = useState("");
  const [stockLoading, setStockLoading] = useState(false);
  const [stockError, setStockError] = useState("");
  const [stockAttempt, setStockAttempt] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const loadProducts = useCallback(async () => {
    const token = getAccessToken();
    if (!token) return;
    setProductsLoading(true);
    setLoadError("");
    try {
      const items = await inventoryApi.listProducts(token);
      setProducts(items);
      const requestedId = new URLSearchParams(window.location.search).get("skuId");
      if (requestedId && items.some((item) => item.id === Number(requestedId))) setSkuId(requestedId);
    } catch (caught) {
      setLoadError(`The product list could not be loaded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`);
    } finally {
      setProductsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProducts();
  }, [loadProducts]);

  useEffect(() => {
    const token = getAccessToken();
    const id = Number(skuId);
    setStockError("");
    if (!token || !skuId || !Number.isInteger(id)) {
      setSelectedProduct(null);
      setStockLoading(false);
      return;
    }
    let active = true;
    setStockLoading(true);
    setSelectedProduct(null);
    setQuantity("");
    inventoryApi.getProduct(token, id)
      .then((product) => { if (active) setSelectedProduct(product); })
      .catch((caught: unknown) => { if (active) setStockError(`Current stock could not be loaded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)}`); })
      .finally(() => { if (active) setStockLoading(false); });
    return () => { active = false; };
  }, [skuId, stockAttempt]);

  const quantityValue = Number(quantity);
  const exceedsStock = selectedProduct !== null && quantity !== "" && quantityValue > selectedProduct.current_stock;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    const token = getAccessToken();
    if (!token || !selectedProduct) return;
    setError("");
    setMessage("");
    setSubmitting(true);
    try {
      await inventoryApi.createOutboundOrder(token, {
        sku_id: selectedProduct.id,
        quantity: quantityValue,
        exit_type: exitType,
        tracking_number: exitType === "dispatch" ? trackingNumber.trim() : null,
        warehouse: selectedProduct.warehouse,
      });
      setMessage(`Stock exit recorded for ${selectedProduct.name}.`);
      setSkuId("");
      setSelectedProduct(null);
      setQuantity("");
      setTrackingNumber("");
    } catch (caught) {
      setError(`The stock exit was not recorded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)} Your answers are still here.`);
    } finally {
      setSubmitting(false);
    }
  }

  const stockLabel = stockLoading ? "Loading…" : selectedProduct ? `${selectedProduct.current_stock} units · ${selectedProduct.warehouse}` : stockError ? "Unavailable" : "Select a product";

  return (
    <>
      <header className="inventory-page-header"><div><p className="inventory-eyebrow">INVENTORY / STOCK EXIT</p><h1>Outbound order</h1><p>Record dispatches or confirmed stock losses.</p></div></header>
      <section className="inventory-form-section"><form className="inventory-form" onSubmit={submit} aria-busy={productsLoading || submitting}>
        {productsLoading && <p className="inventory-hint" role="status">Loading products…</p>}
        {loadError && (
          <p className="inventory-alert" role="alert">{loadError} <button className="inventory-secondary-button" type="button" onClick={() => void loadProducts()}>Try again</button></p>
        )}
        <label>Product / SKU<select required value={skuId} disabled={productsLoading || submitting || products.length === 0} onChange={(event) => { setSkuId(event.target.value); setError(""); setMessage(""); }}><option value="">Select a product</option>{products.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.sku} · {item.warehouse}</option>)}</select></label>
        <div className="available-stock" aria-live="polite"><span>Available stock</span><strong>{stockLabel}</strong></div>
        {stockError && (
          <p className="inventory-alert" role="alert">{stockError} <button className="inventory-secondary-button" type="button" onClick={() => setStockAttempt((attempt) => attempt + 1)}>Try again</button></p>
        )}
        <label>Quantity to exit<input type="number" min="1" step="1" required disabled={!selectedProduct || stockLoading || submitting} value={quantity} onChange={(event) => setQuantity(event.target.value)} aria-describedby={[error ? "outbound-error" : "", exceedsStock ? "stock-warning" : ""].filter(Boolean).join(" ") || undefined} /></label>
        {exceedsStock && <p className="inventory-warning" id="stock-warning" role="status">This quantity exceeds the {selectedProduct?.current_stock} units currently available. The API will reject the exit.</p>}
        {error && <p className="inventory-alert" id="outbound-error" role="alert">{error}</p>}
        <label>Exit type<select value={exitType} disabled={submitting} onChange={(event) => setExitType(event.target.value as ExitType)}><option value="dispatch">Customer dispatch</option><option value="loss">Confirmed loss</option></select></label>
        {exitType === "dispatch" && <label>Carrier tracking number<input required maxLength={120} disabled={submitting} value={trackingNumber} onChange={(event) => setTrackingNumber(event.target.value)} /></label>}
        {message && <p className="inventory-success" role="status">{message}</p>}
        <button className="inventory-primary-button" type="submit" disabled={!selectedProduct || stockLoading || submitting}>{submitting ? "Recording…" : "Record stock exit"}</button>
      </form></section>
    </>
  );
}
