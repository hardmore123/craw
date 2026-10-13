
# client-demo-origin

## 示例代码项目简介

client-demo-origin是一个基于Spring Boot的客户端应用程序，主要用于与CQC数字开放平台进行OAuth2认证并调用其API接口。该项目实现了完整的OAuth2授权流程，包括授权请求、令牌获取、令牌刷新和登出功能，并提供了简洁的前端页面展示。

## 技术栈

- **后端框架**：Spring Boot 2.7.17
- **构建工具**：Maven
- **认证方式**：OAuth2 授权码流程
- **HTTP客户端**：Spring RestTemplate
- **前端**：静态HTML页面
- **JDK版本**：Java 1.8

## 项目结构

```
src/main/java/com/example/
├── AppConfig.java              # 应用程序配置类
├── ClientOriginalApplication.java  # 应用程序启动类
├── GlobalExceptionHandler.java     # 全局异常处理器
├── controller/                 # 控制器层
│   ├── BusinessController.java     # 业务页面控制器
│   ├── LogoutController.java       # 登出控制器
│   ├── OAuth2Controller.java       # OAuth2认证控制器
│   └── UserDataController.java     # 用户数据控制器
├── domain/                     # 数据模型层
│   └── OAuth2TokenResponse.java    # OAuth2令牌响应模型
└── service/                    # 服务层
    ├── CqcApiService.java          # CQC API调用服务
    ├── OAuth2Exception.java        # OAuth2异常类
    └── OAuth2Service.java          # OAuth2认证服务

src/main/resources/
├── application.yaml            # 应用程序配置文件
└── static/                     # 静态资源
    ├── dashboard.html          # 仪表盘页面
    └── error.html              # 错误页面
```

## 主要功能模块

### 1. OAuth2认证模块

负责与CQC数字开放平台进行OAuth2认证交互，包括：
- 生成授权URL并发起授权请求
- 处理认证回调并获取访问令牌
- 存储令牌信息到会话中
- 检查和刷新过期令牌
- 提供令牌状态检查接口

### 2. API调用模块

封装了与CQC开放平台API的交互逻辑，包括：
- 自动处理访问令牌的获取和刷新
- 提供统一的API响应格式
- 处理各类HTTP错误和异常
- 转换异常信息为用户友好的错误消息

### 3. 页面路由模块

提供简单的页面路由功能，包括：
- 首页和仪表盘页面
- 错误页面，根据不同错误类型显示不同内容

### 4. 全局异常处理

统一处理应用程序中的各类异常，特别是OAuth2认证相关的异常，提供友好的错误信息。

## 配置说明

项目的主要配置位于`src/main/resources/application.yaml`文件中，需要配置以下内容：

```yaml
server:
  port: 8086  # 应用程序端口

spring:
  security:
    oauth2:
      client:
        registration:
          client_local: # 由开发者自定义在CQC开放平台的客户端名称，可修改，需与本示例代码java类的@Value注解保持一致
            client-id: # CQC开放平台分配的客户端ID
            client-secret: # 开发者设置的客户端密钥，可修改
            redirect-uri: # 由开发者在应用系统一侧自定义的回调URL，并配置在CQC开放平台的客户端重定向URI中，两者需保持一致
            authorization-grant-type: authorization_code
            client-authentication-method: client_secret_basic
        provider:
          client_local: # 由开发者自定义在CQC开放平台的客户端名称，可修改，需与本示例代码java类的@Value注解保持一致
            issuer-uri: https://open.cqccms.com.cn/open-auth

env:
  gateway: https://open.cqccms.com.cn/open-gateway  # CQC API网关地址
  logout: https://open.cqccms.com.cn/open-auth/logout?callback_type=json  # 登出URL
```

## API接口说明

### 1. OAuth2认证接口

- **GET /oauth2/authorize**：发起OAuth2授权请求，重定向到CQC开放平台的登录页面
- **GET /oauth2/callback**：处理OAuth2认证回调，获取访问令牌并存储到会话中
- **GET /api/token/status**：检查当前会话中的令牌状态
- **GET /api/token/refresh**：手动刷新访问令牌
- **GET /oauth2/logout**：清除会话中的令牌并跳转到CQC开放平台的登出页面

### 2. 用户数据接口

- **GET /cqc/api/{method}/{version}/{startDate}/{endDate}/{pageNum}**：调用CQC开放平台的API获取用户数据
  - `{method}`：API方法名称
  - `{version}`：API版本
  - `{startDate}`：开始日期
  - `{endDate}`：结束日期
  - `{pageNum}`：页码

### 3. 页面路由接口

- **GET /**：首页，重定向到仪表盘页面
- **GET /dashboard**：仪表盘页面，显示功能列表
- **GET /error**：错误页面，显示错误信息

## 启动方式

1. 确保已安装JDK 1.8和Maven
2. 在`application.yaml`中配置必要的参数
3. 使用Maven构建项目：`mvn clean package`
4. 运行生成的jar包：`java -jar target/client-demo-origin-1.0-SNAPSHOT.jar`
5. 访问`http://your-domain:8086/`即可进入应用程序

## 注意事项

1. 确保`application.yaml`中的配置与CQC数字开放平台的配置一致
2. 生产环境中应避免将访问令牌直接输出到前端
3. 项目使用Session存储令牌信息，在分布式环境中可能需要使用Redis等共享存储
4. 确保 /oauth2/callback 可以被CQC数字开放平台回调访问

## 错误处理

项目实现了全面的错误处理机制，包括：
- OAuth2认证错误
- HTTP请求错误
- 网络连接错误
- 系统内部错误

当发生错误时，系统会跳转到错误页面并显示相应的错误信息和建议的操作。

## License

该项目为示例代码，仅供参考。
        