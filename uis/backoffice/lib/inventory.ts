import { ApiError, apiRequest } from "./api-client";
import { completeFlow } from "./telemetry-flows";
import { actorRole, clientIdOf, countryOf, productIdOf, warehouseName } from "./telemetry-domain";
import { newRequestId, track, withRequestId } from "./telemetry";

export type Warehouse = "LA" | "ZGZ";
export type ProductCategory = "fashion" | "electronics" | "cosmetics";
export type ExitType = "dispatch" | "loss";
export type DetectionSource = "cycle_count" | "full_audit" | "spot_check";

export interface SKU {
  id: number;
  name: string;
  sku: string;
  client_name: string;
  category: ProductCategory;
  warehouse: Warehouse;
  current_stock: number;
}

export interface InboundOrderInput {
  sku_id: number;
  quantity: number;
  reference: string;
  warehouse: Warehouse;
}

export interface OutboundOrderInput {
  sku_id: number;
  quantity: number;
  exit_type: ExitType;
  tracking_number: string | null;
  warehouse: Warehouse;
}

export interface InventoryOrder {
  id: number;
  sku_id: number;
  quantity: number;
  order_type: "inbound" | "outbound";
  reference: string | null;
  exit_type: ExitType | null;
  tracking_number: string | null;
  warehouse: Warehouse;
  created_at: string;
  user_uid: string;
  sku: SKU;
}

export interface StockCountInput {
  skuId: number;
  countedQuantity: number;
  detectionSource: DetectionSource;
}

export interface StockCountResult {
  countId: string;
  product: SKU;
  systemQuantity: number;
  countedQuantity: number;
  /** Counted minus system: negative is a shortage, positive a surplus, zero means the count matches. */
  difference: number;
}

/**
 * Stock below this many units is low (the products page shows 1 to 5 units as "Low stock").
 * Interim value for `stock_threshold_triggered` until the per-client, per-SKU minimum exists (plan, section 10).
 */
export const LOW_STOCK_THRESHOLD = 6;

/** Fields that would change stock without an order. Stock only moves through inbound and outbound orders. */
const STOCK_FIELDS = ["current_stock", "stock", "quantity_on_hand"] as const;
export const STOCK_READ_ONLY_MESSAGE = "Stock cannot be edited directly. Record an inbound or outbound order instead.";

/** Products seen in API answers, so a failed request can still be described by its client, SKU code and category. */
const knownProducts = new Map<number, SKU>();

function remember(products: SKU[]): void {
  for (const product of products) knownProducts.set(product.id, product);
}

type Dimensions = {
  warehouse: string | null;
  country: string | null;
  client_id: string | null;
  product_id: string | null;
  product_category: string | null;
};

/** The five dimensions every inventory event can be aggregated by: warehouse, country, client, product, category. */
function dimensionsOf(warehouse: string | null | undefined, sku: Pick<SKU, "client_name" | "sku" | "category"> | null | undefined): Dimensions {
  return {
    warehouse: warehouseName(warehouse),
    country: countryOf(warehouse),
    client_id: clientIdOf(sku?.client_name),
    product_id: productIdOf(sku?.sku),
    product_category: sku?.category ?? null,
  };
}

function nonNegativeInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

// --- events of a successful order ----------------------------------------------------------

/** Stock fell under the minimum (edge-triggered: only the order that crosses it) or reached zero. */
function trackThreshold(order: InventoryOrder): void {
  const after = order.sku.current_stock;
  const before = after + order.quantity;
  const crossed = after < LOW_STOCK_THRESHOLD && before >= LOW_STOCK_THRESHOLD;
  const emptied = after === 0 && before > 0;
  if (!crossed && !emptied) return;
  track("stock_threshold_triggered", {
    ...dimensionsOf(order.warehouse, order.sku),
    quantity: after,
    threshold: LOW_STOCK_THRESHOLD,
    severity: after === 0 ? "out_of_stock" : "below_minimum",
    order_id: String(order.id),
  });
}

/** The system's own rules, checked on every answer: stock is never negative and a movement belongs to its SKU's warehouse. */
function checkOrderIntegrity(order: InventoryOrder): void {
  const base = { ...dimensionsOf(order.warehouse, order.sku), detection_point: "request_check", order_id: String(order.id) };
  if (order.sku.current_stock < 0) {
    track("stock_integrity_violation_detected", {
      ...base,
      violation_type: "negative_stock",
      quantity: Math.abs(order.sku.current_stock),
      expected_warehouse: null,
      observed_warehouse: null,
    });
  }
  if (order.warehouse !== order.sku.warehouse) {
    track("stock_integrity_violation_detected", {
      ...base,
      violation_type: "warehouse_mismatch",
      quantity: order.quantity,
      expected_warehouse: warehouseName(order.sku.warehouse),
      observed_warehouse: warehouseName(order.warehouse),
    });
  }
}

// --- events of a refused order -------------------------------------------------------------

function isClientRefusal(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 401 && error.status !== 403;
}

function trackInboundRejected(payload: InboundOrderInput, error: unknown): void {
  if (!isClientRefusal(error)) return;
  const sku = knownProducts.get(payload.sku_id);
  const reason = error.status === 404 ? "unknown_product" : error.fieldErrors.quantity ? "invalid_quantity" : error.fieldErrors.warehouse ? "invalid_warehouse" : "malformed_request";
  track("inbound_order_rejected", {
    ...dimensionsOf(payload.warehouse, sku),
    rejection_reason: reason,
    http_status: error.status,
    quantity: nonNegativeInteger(payload.quantity),
    requested_sku_id: nonNegativeInteger(payload.sku_id),
    actor_role: actorRole(),
  });
}

function outboundRejectionReason(payload: OutboundOrderInput, error: ApiError): string {
  if (error.status === 404) return "unknown_product";
  if (error.status === 400 && /insufficient stock/i.test(error.message)) return "insufficient_stock";
  if (payload.exit_type === "dispatch" && !payload.tracking_number) return "missing_tracking_number";
  if (payload.exit_type === "loss" && payload.tracking_number) return "tracking_number_not_allowed";
  if (error.fieldErrors.quantity) return "invalid_quantity";
  if (error.fieldErrors.exit_type) return "invalid_exit_type";
  if (error.fieldErrors.warehouse) return "invalid_warehouse";
  return "malformed_request";
}

function trackOutboundRejected(payload: OutboundOrderInput, error: unknown): void {
  if (!isClientRefusal(error)) return;
  const sku = knownProducts.get(payload.sku_id);
  const available = /Available: (\d+)/.exec(error.message);
  track("outbound_order_rejected", {
    ...dimensionsOf(payload.warehouse, sku),
    rejection_reason: outboundRejectionReason(payload, error),
    http_status: error.status,
    exit_type: payload.exit_type === "dispatch" || payload.exit_type === "loss" ? payload.exit_type : null,
    quantity: nonNegativeInteger(payload.quantity),
    available_quantity: available ? Number(available[1]) : null,
    actor_role: actorRole(),
  });
}

/**
 * Stock can only change through an order. A product payload that carries a stock value is refused here, before
 * any request, and the attempt is recorded. Only the number is kept, never the rest of the payload.
 */
function rejectDirectStockEdit(payload: Record<string, unknown>): ApiError | null {
  const field = STOCK_FIELDS.find((name) => name in payload);
  if (!field) return null;
  const attempted = Math.abs(Math.trunc(Number(payload[field])));
  track("direct_stock_edit_rejected", {
    ...dimensionsOf(typeof payload.warehouse === "string" ? payload.warehouse : null, {
      client_name: String(payload.client_name ?? ""),
      sku: String(payload.sku ?? ""),
      category: payload.category as ProductCategory,
    }),
    quantity: Number.isFinite(attempted) ? attempted : 0,
    attempted_operation: "set_stock",
    attempt_channel: "stock_field_in_payload",
    actor_role: actorRole(),
    http_method: "POST",
  });
  return new ApiError(STOCK_READ_ONLY_MESSAGE, 400);
}

// --- stock count ---------------------------------------------------------------------------

function newCountId(): string {
  const random = Array.from(crypto.getRandomValues(new Uint8Array(4)), (byte) => byte.toString(16).padStart(2, "0")).join("").toUpperCase();
  return `CNT-${new Date().getUTCFullYear()}-${random}`;
}

// --- API -----------------------------------------------------------------------------------

async function getProduct(token: string, id: number): Promise<SKU> {
  const product = await apiRequest<SKU>(`/inventory/products/${id}`, { token });
  remember([product]);
  return product;
}

async function createInboundOrder(token: string, payload: InboundOrderInput, options: { adjustment?: boolean } = {}): Promise<InventoryOrder> {
  const requestId = newRequestId();
  let order: InventoryOrder;
  try {
    order = await apiRequest<InventoryOrder>("/inventory/orders/inbound", { method: "POST", token, body: payload, requestId });
  } catch (caught) {
    withRequestId(requestId, () => trackInboundRejected(payload, caught));
    throw caught;
  }
  remember([order.sku]);
  withRequestId(requestId, () => {
    // A stock-count adjustment is not goods received, so it must not inflate the volume metric.
    if (!options.adjustment) {
      track("inbound_order_created", {
        ...dimensionsOf(order.warehouse, order.sku),
        order_id: String(order.id),
        quantity: order.quantity,
        stock_after: order.sku.current_stock,
        has_client_reference: Boolean(order.reference),
      });
    }
    checkOrderIntegrity(order);
  });
  return order;
}

async function createOutboundOrder(token: string, payload: OutboundOrderInput, options: { adjustment?: boolean } = {}): Promise<InventoryOrder> {
  const requestId = newRequestId();
  let order: InventoryOrder;
  try {
    order = await apiRequest<InventoryOrder>("/inventory/orders/outbound", { method: "POST", token, body: payload, requestId });
  } catch (caught) {
    withRequestId(requestId, () => trackOutboundRejected(payload, caught));
    throw caught;
  }
  remember([order.sku]);
  withRequestId(requestId, () => {
    // A stock-count adjustment is not a dispatch, so it must not count as outbound volume.
    if (!options.adjustment) {
      track("outbound_order_created", {
        ...dimensionsOf(order.warehouse, order.sku),
        order_id: String(order.id),
        exit_type: order.exit_type,
        quantity: order.quantity,
        stock_after: order.sku.current_stock,
      });
    }
    trackThreshold(order);
    checkOrderIntegrity(order);
  });
  return order;
}

async function createProduct(token: string, payload: Omit<SKU, "id" | "current_stock">): Promise<SKU> {
  const refusal = rejectDirectStockEdit(payload as unknown as Record<string, unknown>);
  if (refusal) throw refusal;
  const requestId = newRequestId();
  const product = await apiRequest<SKU>("/inventory/products", { method: "POST", token, body: payload, requestId });
  remember([product]);
  withRequestId(requestId, () => track("product_created", { ...dimensionsOf(product.warehouse, product), actor_role: actorRole() }));
  return product;
}

/**
 * Records a physical count. If it differs from the system stock, the difference is reconciled with an adjustment
 * order that references the count (a surplus is received, a shortage is written off as a loss), so stock still
 * changes only through orders. The discrepancy is reported once the adjustment is saved.
 */
async function recordStockCount(token: string, input: StockCountInput): Promise<StockCountResult> {
  const requestId = newRequestId();
  const product = await getProduct(token, input.skuId);
  const systemQuantity = product.current_stock;
  const difference = input.countedQuantity - systemQuantity;
  const countId = newCountId();
  if (difference === 0) {
    completeFlow("stock_count");
    return { countId, product, systemQuantity, countedQuantity: input.countedQuantity, difference };
  }
  if (difference > 0) {
    await createInboundOrder(token, { sku_id: product.id, quantity: difference, reference: countId, warehouse: product.warehouse }, { adjustment: true });
  } else {
    await createOutboundOrder(token, { sku_id: product.id, quantity: -difference, exit_type: "loss", tracking_number: null, warehouse: product.warehouse }, { adjustment: true });
  }
  withRequestId(requestId, () =>
    track("inventory_discrepancy_detected", {
      ...dimensionsOf(product.warehouse, product),
      quantity: Math.abs(difference),
      system_quantity: systemQuantity,
      counted_quantity: input.countedQuantity,
      direction: difference < 0 ? "shortage" : "surplus",
      count_id: countId,
      detection_source: input.detectionSource,
    }),
  );
  return { countId, product, systemQuantity, countedQuantity: input.countedQuantity, difference };
}

export const inventoryApi = {
  async listProducts(token: string): Promise<SKU[]> {
    const products = await apiRequest<SKU[]>("/inventory/products", { token });
    remember(products);
    return products;
  },
  getProduct,
  createProduct,
  createInboundOrder,
  createOutboundOrder,
  recordStockCount,
  listOrders(token: string): Promise<InventoryOrder[]> {
    return apiRequest<InventoryOrder[]>("/inventory/orders", { token });
  },
};
