package dev.opspilot.business.orders;

import java.util.Map;
import org.springframework.dao.DataAccessException;
import org.springframework.http.ResponseEntity;
import org.springframework.transaction.TransactionException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/** 数据库故障不能伪装为订单不存在，也不能把 SQL、凭据或调用栈返回客户端。 */
@RestControllerAdvice(assignableTypes = {OrderController.class, ReturnDraftController.class})
class OrderStorageExceptionHandler {
    @ExceptionHandler({DataAccessException.class, TransactionException.class})
    ResponseEntity<?> unavailable(RuntimeException error) {
        return ResponseEntity.status(503).body(Map.of("code", "ORDER_STORE_UNAVAILABLE"));
    }
}
