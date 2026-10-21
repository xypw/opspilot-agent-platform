package dev.opspilot.business.orders;

/** 允许无理由、需要理由/人工核实、当前订单状态不支持申请。 */
public enum ReturnDecision {
    NO_REASON_ALLOWED,
    REASON_REQUIRED,
    NOT_ALLOWED
}
