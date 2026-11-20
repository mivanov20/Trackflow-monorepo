import { apiRequest } from "./api-client";

export type Warehouse = "LA" | "ZGZ";
export type ProductCategory = "fashion" | "electronics" | "cosmetics";
export type ExitType = "dispatch" | "loss";

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
  user_uuid: string;
  sku: SKU;
}

export const inventoryApi = {
  listProducts(token: string): Promise<SKU[]> {
    return apiRequest<SKU[]>("/inventory/products", { token });
  },
  getProduct(token: string, id: number): Promise<SKU> {
    return apiRequest<SKU>(`/inventory/products/${id}`, { token });
  },
  createProduct(token: string, payload: Omit<SKU, "id" | "current_stock">): Promise<SKU> {
    return apiRequest<SKU>("/inventory/products", { method: "POST", token, body: payload });
  },
  createInboundOrder(token: string, payload: InboundOrderInput): Promise<InventoryOrder> {
    return apiRequest<InventoryOrder>("/inventory/orders/inbound", { method: "POST", token, body: payload });
  },
  createOutboundOrder(token: string, payload: OutboundOrderInput): Promise<InventoryOrder> {
    return apiRequest<InventoryOrder>("/inventory/orders/outbound", { method: "POST", token, body: payload });
  },
  listOrders(token: string): Promise<InventoryOrder[]> {
    return apiRequest<InventoryOrder[]>("/inventory/orders", { token });
  },
};