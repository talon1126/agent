import { apiClient } from '@/services/apiClient'
import { fetchDeliveryAddresses } from '@/services/checkoutApi'
import type {
  FlashSalePurchaseRequest,
  FlashSalePurchaseResponse,
  FlashSaleResponse,
} from '@/types/flashSale'

export async function fetchFlashSale(flashSaleId: number): Promise<FlashSaleResponse> {
  // 中文注释：后端当前只提供单活动查询，列表接口补齐前不在主页硬编码活动 ID。
  const response = await apiClient.get<FlashSaleResponse>(`/flash-sales/${flashSaleId}`)

  return response.data
}

export async function purchaseFlashSale(
  flashSaleId: number,
  payload: FlashSalePurchaseRequest,
): Promise<FlashSalePurchaseResponse> {
  // 中文注释：抢购是否成功以后端原子扣减结果为准，前端只负责提交用户和收货地址。
  const response = await apiClient.post<FlashSalePurchaseResponse>(
    `/flash-sales/${flashSaleId}/purchase`,
    payload,
  )

  return response.data
}

export async function purchaseFlashSaleWithDefaultAddress(
  flashSaleId: number,
  userId: number,
): Promise<FlashSalePurchaseResponse> {
  const addressResponse = await fetchDeliveryAddresses(userId)
  const defaultAddress =
    addressResponse.items.find((address) => Number(address.is_default) === 1) ??
    addressResponse.items[0]

  if (!defaultAddress?.address.trim()) {
    throw new Error('Default delivery address is required before flash sale purchase.')
  }

  // 中文注释：秒杀下单复用默认收货地址，保持和购物车结算一致的地址来源。
  return purchaseFlashSale(flashSaleId, {
    user_id: userId,
    shipping_address: defaultAddress.address.trim(),
    delivery_provider_id: 'sf',
  })
}
