package dev.opspilot.business.orders;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;

/** 可信业务记录中的商品事实和下单时承诺；不接受模型或 PDF 覆写。 */
public record OrderReturnPolicy(
        Category category, Condition condition, Boolean exclusionConfirmedAtPurchase,
        Boolean downloadedOrUnsealed, Boolean activated,
        Integer merchantNoReasonDays, String version) {
    public enum Category {
        STANDARD, CUSTOM_MADE, FRESH_PERISHABLE, DIGITAL, NEWSPAPER_PERIODICAL,
        SEALED_HEALTH, VALUE_SENSITIVE, DISCLOSED_DISCOUNT, UNKNOWN
    }
    public enum Condition { INTACT, REASONABLE_INSPECTION, NOT_INTACT, UNKNOWN }

    public static OrderReturnPolicy unknown() {
        return new OrderReturnPolicy(Category.UNKNOWN, Condition.UNKNOWN, null, null, null, null, "unknown");
    }
    public static OrderReturnPolicy standard() {
        return new OrderReturnPolicy(Category.STANDARD, Condition.INTACT, false, false, false, 0, "demo-v1");
    }
    public String fingerprint() {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(("CN-ONLINE-20261021|" + toString()).getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException impossible) {
            throw new IllegalStateException(impossible);
        }
    }
}
