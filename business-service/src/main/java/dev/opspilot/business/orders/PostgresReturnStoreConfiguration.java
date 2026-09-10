package dev.opspilot.business.orders;

import javax.sql.DataSource;
import org.flywaydb.core.Flyway;
import org.postgresql.ds.PGSimpleDataSource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.*;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

/** 仅在显式选择 postgres 后端时创建数据库连接和迁移器。 */
@Configuration(proxyBeanMethods = false)
@ConditionalOnProperty(name = "opspilot.return-store.backend", havingValue = "postgres")
class PostgresReturnStoreConfiguration {
    @Bean
    DataSource returnStoreDataSource(
            @Value("${opspilot.return-store.jdbc-url}") String jdbcUrl,
            @Value("${opspilot.return-store.username}") String username,
            @Value("${opspilot.return-store.password}") String password) {
        var dataSource = new PGSimpleDataSource();
        dataSource.setUrl(jdbcUrl);
        dataSource.setUser(username);
        dataSource.setPassword(password);
        return dataSource;
    }

    @Bean(initMethod = "migrate")
    Flyway returnStoreFlyway(DataSource returnStoreDataSource) {
        return Flyway.configure()
                .dataSource(returnStoreDataSource)
                // 兼容已由 db/init.sql 创建过业务表、但尚无 Flyway 历史表的本地数据库。
                .baselineOnMigrate(true)
                .baselineVersion("1")
                .locations("classpath:db/migration")
                .load();
    }

    @Bean
    JdbcTemplate returnStoreJdbcTemplate(DataSource returnStoreDataSource) {
        return new JdbcTemplate(returnStoreDataSource);
    }

    @Bean
    TransactionTemplate returnStoreTransactionTemplate(DataSource returnStoreDataSource) {
        return new TransactionTemplate(new DataSourceTransactionManager(returnStoreDataSource));
    }
}
