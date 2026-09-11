ARG IMAGE_REGISTRY=docker.m.daocloud.io
FROM ${IMAGE_REGISTRY}/library/maven:3.9.11-eclipse-temurin-17-alpine AS build

WORKDIR /workspace

COPY pom.xml .
COPY maven-settings.xml .
COPY src src
RUN --mount=type=cache,target=/root/.m2 mvn -s maven-settings.xml -B -ntp -DskipTests package

FROM ${IMAGE_REGISTRY}/library/eclipse-temurin:17-jre-alpine

WORKDIR /app

RUN addgroup -S opspilot && adduser -S opspilot -G opspilot

COPY --from=build /workspace/target/business-service-0.1.0.jar app.jar

USER opspilot

EXPOSE 8081

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
