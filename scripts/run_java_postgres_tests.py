"""在本机 PostgreSQL 独立 schema 中运行 Java 真事务测试，保持现有业务数据不变。"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit
import uuid

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from database import load_database_url  # noqa: E402


def run(maven: str) -> int:
    url = load_database_url()
    parsed = urlsplit(url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("此验收脚本只接受本机 PostgreSQL，不允许对外部数据库建测试 schema")
    database = unquote(parsed.path.lstrip("/"))
    if not re.fullmatch(r"[a-zA-Z0-9_]+", database):
        raise ValueError("不支持的数据库名称")
    schema = "opspilot_java_it_" + uuid.uuid4().hex
    environment = dict(os.environ)
    for name in list(environment):
        if name.upper().endswith(("_API_KEY", "_TOKEN", "_TOKENS")):
            environment.pop(name)
    environment.update({
        "RUN_POSTGRES_INTEGRATION": "true",
        "BUSINESS_DATABASE_URL": f"jdbc:postgresql://127.0.0.1:{parsed.port or 5432}/{database}?currentSchema={schema}",
        "POSTGRES_USER": unquote(parsed.username or ""),
        "POSTGRES_PASSWORD": unquote(parsed.password or ""),
    })
    # 显式给测试 Spring 上下注入隔离库；不依赖开发机资源扫描/外部配置的默认值。
    environment["SPRING_APPLICATION_JSON"] = json.dumps({"opspilot": {"return-store": {
        "jdbc-url": environment["BUSINESS_DATABASE_URL"],
        "username": environment["POSTGRES_USER"],
        "password": environment["POSTGRES_PASSWORD"],
    }}})
    log = ROOT / "output/java-postgres-integration.log"
    log.parent.mkdir(exist_ok=True)
    with psycopg.connect(url, connect_timeout=5) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with log.open("w", encoding="utf-8") as output:
            result = subprocess.run(
                [maven, "-o", "-Dmaven.repo.local=../.maven-cache", "-B", "-ntp",
                 "-Dtest=PersistentOrderWorkflowIntegrationTest,PostgresReturnDraftIntegrationTest", "test"],
                cwd=ROOT / "business-service", env=environment,
                stdout=output, stderr=subprocess.STDOUT, timeout=300,
            )
        print(log.read_text(encoding="utf-8", errors="replace")[-9000:])
        return result.returncode
    finally:
        if not re.fullmatch(r"opspilot_java_it_[a-f0-9]{32}", schema):
            raise RuntimeError("拒绝清理不属于本轮测试的 schema")
        with psycopg.connect(url, connect_timeout=5, options="-c lock_timeout=5000") as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--maven", default=shutil.which("mvn") or shutil.which("mvn.cmd"))
    args = parser.parse_args()
    if not args.maven:
        parser.error("未找到 Maven，请通过 --maven 指定已有可执行文件")
    raise SystemExit(run(args.maven))
