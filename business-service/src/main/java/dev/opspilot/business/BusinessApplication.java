package dev.opspilot.business;

import java.time.Clock;
import java.time.ZoneId;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;

/** Java 业务服务入口；本课只启用一个只读订单接口。 */
// 数据源只在 postgres 后端启用；默认内存测试不会尝试连接本地数据库。
@SpringBootApplication(exclude = DataSourceAutoConfiguration.class)
public class BusinessApplication {
    public static void main(String[] args) {
        SpringApplication.run(BusinessApplication.class, args);
    }

    /** 统一业务日期来源，测试时可以替换为固定时钟，避免日期测试随时间失效。 */
    @Bean
    Clock businessClock() {
        return Clock.system(ZoneId.of("Asia/Shanghai"));
    }
}
