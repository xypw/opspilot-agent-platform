package dev.opspilot.business.orders;

/** 退货资格的三态结果，避免用一个 boolean 丢失第 8～15 天的业务语义。 */
public enum ReturnDecision {
    NO_REASON_ALLOWED,
    REASON_REQUIRED,
    NOT_ALLOWED
}
