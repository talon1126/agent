import { apiClient } from '@/services/apiClient'
import type {
  CreateWarehouseOrderRequest,
  DeliveryAddressResponse,
  WarehouseOrderResponse,
} from '@/types/checkout'

export async function fetchDeliveryAddresses(userId: number): Promise<DeliveryAddressResponse> {
  // 中文注释：结算页必须按用户查询收货地址，避免展示其他用户的地址。
  const response = await apiClient.get<DeliveryAddressResponse>('/delivery_addresses', {
    params: { user_id: userId },
  })

  return response.data
}

export async function createWarehouseOrder(
  payload: CreateWarehouseOrderRequest,
): Promise<WarehouseOrderResponse> {
  // 中文注释：购物车结算复用仓储订单接口，由后端完成选仓和库存扣减。
  const response = await apiClient.post<WarehouseOrderResponse>('/warehouse/orders', payload)

  return response.data
}
