"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { GENERIC_ERROR_MESSAGE, messageFor } from "../../../../lib/api-client";
import { getAccessToken } from "../../../../lib/auth";
import { inventoryApi, type DetectionSource, type SKU, type StockCountResult } from "../../../../lib/inventory";

const SOURCES: { value: DetectionSource; label: string }[] = [
  { value: "cycle_count", label: "Cycle count" },
  { value: "full_audit", label: "Full audit" },
  { value: "spot_check", label: "Spot check" },
];

function summary(result: StockCountResult): string {
  const { product, systemQuantity, countedQuantity, difference, countId } = result;
  if (difference === 0) return `Count ${countId}: ${countedQuantity} units counted for ${product.name}. The system stock matches.`;
  const direction = difference < 0 ? `${-difference} units missing, written off as a loss` : `${difference} extra units, received into stock`;
  return `Count ${countId}: ${countedQuantity} units counted for ${product.name}, the system showed ${systemQuantity}. ${direction}.`;
}

export default function StockCountPage() {
  const [products, setProducts] = useState<SKU[]>([]);
  const [skuId, setSkuId] = useState("");
  const [counted, setCounted] = useState("");
  const [source, setSource] = useState<DetectionSource>("cycle_count");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<StockCountResult | null>(null);

  const loadProducts = useCallback(async () => {
    const token = getAccessToken();
    if (!token) return;
    setLoading(true);
    setLoadError("");
    try {
      setProducts(await inventoryApi.listProducts(token));
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
    if (!token || !skuId) return;
    setError("");
    setResult(null);
    setSubmitting(true);
    try {
      const outcome = await inventoryApi.recordStockCount(token, { skuId: Number(skuId), countedQuantity: Number(counted), detectionSource: source });
      setResult(outcome);
      setSkuId("");
      setCounted("");
      void loadProducts();
    } catch (caught) {
      setError(`The count was not recorded. ${messageFor(caught, GENERIC_ERROR_MESSAGE)} Your answers are still here.`);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <header className="inventory-page-header"><div><p className="inventory-eyebrow">INVENTORY / STOCK COUNT</p><h1>Stock count</h1><p>Enter the units you counted on the shelf. The system stock is shown only after you save, and any difference is settled with an adjustment order, never by editing stock.</p></div></header>
      <section className="inventory-form-section"><form className="inventory-form" onSubmit={submit} aria-busy={loading || submitting}>
        {loading && <p className="inventory-hint" role="status">Loading products…</p>}
        {loadError && (
          <p className="inventory-alert" role="alert">{loadError} <button className="inventory-secondary-button" type="button" onClick={() => void loadProducts()}>Try again</button></p>
        )}
        <label>Product / SKU<select required value={skuId} disabled={loading || submitting || products.length === 0} onChange={(event) => { setSkuId(event.target.value); setError(""); setResult(null); }}><option value="">Select a product</option>{products.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.sku} · {item.warehouse}</option>)}</select></label>
        <label>Units counted<input type="number" min="0" step="1" required disabled={submitting} value={counted} onChange={(event) => setCounted(event.target.value)} /></label>
        <label>Kind of count<select value={source} disabled={submitting} onChange={(event) => setSource(event.target.value as DetectionSource)}>{SOURCES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
        {result && <p className={result.difference === 0 ? "inventory-success" : "inventory-warning"} role="status">{summary(result)}</p>}
        {error && <p className="inventory-alert" role="alert">{error}</p>}
        <button className="inventory-primary-button" type="submit" disabled={loading || submitting || !skuId || counted === ""}>{submitting ? "Saving…" : "Save count"}</button>
      </form></section>
    </>
  );
}
