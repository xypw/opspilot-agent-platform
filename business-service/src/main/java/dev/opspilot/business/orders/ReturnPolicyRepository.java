package dev.opspilot.business.orders;

/** 调用方先校验订单归属；缺少商品事实时进入人工核实，不能默认七天无理由。 */
public interface ReturnPolicyRepository {
    OrderReturnPolicy find(String orderId);
}
