package com.example.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.*;
import org.springframework.stereotype.Service;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestClientException;
import org.springframework.web.client.RestTemplate;

import javax.servlet.http.HttpSession;
import java.util.HashMap;
import java.util.Map;

@Service
public class CqcApiService {

    private static final Logger logger = LoggerFactory.getLogger(CqcApiService.class);

    @Autowired
    private RestTemplate restTemplate;

    @Autowired
    private OAuth2Service oauth2Service;

    /**
     * 调用CQC开放平台API（自动处理令牌刷新）
     */
    public ApiResponse callCqcApi(String endpoint, HttpSession session) {

        try {
            // 获取有效的访问令牌（如果需要会自动刷新）
            String accessToken = oauth2Service.getValidAccessToken(session);

            // 设置请求头
            HttpHeaders headers = new HttpHeaders();
            headers.setBearerAuth(accessToken);
            headers.setContentType(MediaType.APPLICATION_JSON);

            HttpEntity<String> entity = new HttpEntity<>(headers);

            // 调用API
            ResponseEntity<String> response = restTemplate.exchange(
                    endpoint,
                    HttpMethod.GET,
                    entity,
                    String.class
            );

            return ApiResponse.success(response.getBody(), response.getStatusCode());

        } catch (OAuth2Exception e) {
            logger.warn("OAuth2 authentication failed for API call to {}: {}", endpoint, e.getMessage());

            // 根据异常类型提供更友好的错误信息
            String userFriendlyMessage = getFriendlyErrorMessage(e);

            return ApiResponse.error(
                    userFriendlyMessage,
                    "AUTHENTICATION_ERROR",
                    HttpStatus.UNAUTHORIZED
            );

        } catch (HttpClientErrorException e) {
            logger.warn("B system API returned error for {}: {}", endpoint, e.getStatusCode());

            // 处理B系统返回的HTTP错误
            String userFriendlyMessage = "The requested operation could not be completed.";

            // 根据不同的HTTP状态码提供更具体的错误信息
            if (e.getStatusCode() == HttpStatus.NOT_FOUND) {
                userFriendlyMessage = "The requested resource was not found.";
            } else if (e.getStatusCode() == HttpStatus.FORBIDDEN) {
                userFriendlyMessage = "You don't have permission to access this resource.";
            }

            return ApiResponse.error(
                    userFriendlyMessage,
                    "API_ERROR",
                    e.getStatusCode()
            );

        } catch (RestClientException e) {
            logger.error("Network error when calling B system API {}: {}", endpoint, e.getMessage());

            return ApiResponse.error(
                    "Unable to connect to the service. Please try again later.",
                    "NETWORK_ERROR",
                    HttpStatus.SERVICE_UNAVAILABLE
            );

        } catch (Exception e) {
            logger.error("Unexpected error when calling B system API {}: {}", endpoint, e.getMessage(), e);

            return ApiResponse.error(
                    "An unexpected error occurred. Please try again later.",
                    "UNEXPECTED_ERROR",
                    HttpStatus.INTERNAL_SERVER_ERROR
            );
        }
    }

    /**
     * 带请求体的API调用
     */
    public ApiResponse callCqcApi(String endpoint, Object requestBody, HttpSession session) {
        try {
            // 获取有效的访问令牌（如果需要会自动刷新）
            String accessToken = oauth2Service.getValidAccessToken(session);

            // 设置请求头
            HttpHeaders headers = new HttpHeaders();
            headers.setBearerAuth(accessToken);
            headers.setContentType(MediaType.APPLICATION_JSON);

            HttpEntity<Object> entity = new HttpEntity<>(requestBody, headers);

            // 调用API
            ResponseEntity<String> response = restTemplate.exchange(
                    endpoint,
                    HttpMethod.POST,
                    entity,
                    String.class
            );

            return ApiResponse.success(response.getBody(), response.getStatusCode());

        } catch (OAuth2Exception e) {
            logger.warn("OAuth2 authentication failed for API call to {}: {}", endpoint, e.getMessage());

            // 根据异常类型提供更友好的错误信息
            String userFriendlyMessage = getFriendlyErrorMessage(e);

            return ApiResponse.error(
                    userFriendlyMessage,
                    "AUTHENTICATION_ERROR",
                    HttpStatus.UNAUTHORIZED
            );

        } catch (HttpClientErrorException e) {
            logger.warn("B system API returned error for {}: {}", endpoint, e.getStatusCode());

            // 处理B系统返回的HTTP错误
            String userFriendlyMessage = "The requested operation could not be completed.";

            // 根据不同的HTTP状态码提供更具体的错误信息
            if (e.getStatusCode() == HttpStatus.NOT_FOUND) {
                userFriendlyMessage = "The requested resource was not found.";
            } else if (e.getStatusCode() == HttpStatus.FORBIDDEN) {
                userFriendlyMessage = "You don't have permission to access this resource.";
            }

            return ApiResponse.error(
                    userFriendlyMessage,
                    "API_ERROR",
                    e.getStatusCode()
            );

        } catch (RestClientException e) {
            logger.error("Network error when calling B system API {}: {}", endpoint, e.getMessage());

            return ApiResponse.error(
                    "Unable to connect to the service. Please try again later.",
                    "NETWORK_ERROR",
                    HttpStatus.SERVICE_UNAVAILABLE
            );

        } catch (Exception e) {
            logger.error("Unexpected error when calling B system API {}: {}", endpoint, e.getMessage(), e);

            return ApiResponse.error(
                    "An unexpected error occurred. Please try again later.",
                    "UNEXPECTED_ERROR",
                    HttpStatus.INTERNAL_SERVER_ERROR
            );
        }
    }


    /**
     * 将OAuth2异常转换为用户友好的错误消息
     */
    private String getFriendlyErrorMessage(OAuth2Exception e) {
        String message = e.getMessage();

        // 根据异常消息提供更友好的错误信息
        if (message != null) {
            if (message.contains("expired") || message.contains("invalid_token")) {
                return "Your session has expired. Please sign in again.";
            } else if (message.contains("invalid_grant") || message.contains("invalid_request")) {
                return "Authentication failed. Please try signing in again.";
            } else if (message.contains("access_denied")) {
                return "Access denied. You don't have permission to perform this action.";
            } else if (message.contains("server_error")) {
                return "The authentication service is temporarily unavailable. Please try again later.";
            }
        }

        // 默认错误消息
        return "Authentication is required to access this resource. Please sign in.";
    }


    /**
     * 统一的API响应类
     */
    public static class ApiResponse {
        private boolean success;
        private String data;
        private String message;
        private String errorCode;
        private HttpStatus status;

        // 成功响应
        public static ApiResponse success(String data, HttpStatus status) {
            ApiResponse response = new ApiResponse();
            response.success = true;
            response.data = data;
            response.status = status;
            return response;
        }

        // 错误响应
        public static ApiResponse error(String message, String errorCode, HttpStatus status) {
            ApiResponse response = new ApiResponse();
            response.success = false;
            response.message = message;
            response.errorCode = errorCode;
            response.status = status;
            return response;
        }

        // 转换为Map（用于JSON响应）
        public Map<String, Object> toMap() {
            Map<String, Object> result = new HashMap<>();
            result.put("success", success);

            if (success) {
                result.put("data", data);
            } else {
                result.put("message", message);
                result.put("errorCode", errorCode);
            }

            result.put("status", status.value());
            return result;
        }

        // Getter方法
        public boolean isSuccess() {
            return success;
        }

        public String getData() {
            return data;
        }

        public String getMessage() {
            return message;
        }

        public String getErrorCode() {
            return errorCode;
        }

        public HttpStatus getStatus() {
            return status;
        }
    }

}