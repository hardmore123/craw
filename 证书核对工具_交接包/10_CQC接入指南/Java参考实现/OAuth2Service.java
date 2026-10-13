package com.example.service;

import com.example.domain.OAuth2TokenResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.*;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestTemplate;

import javax.annotation.Resource;
import javax.servlet.http.HttpSession;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.Base64;

@Service
public class OAuth2Service {

    private static final Logger logger = LoggerFactory.getLogger(OAuth2Service.class);

    @Value("${spring.security.oauth2.client.registration.client_local.client-id}")
    private String clientId;
    @Value("${spring.security.oauth2.client.registration.client_local.client-secret}")
    private String clientSecret;
    @Value("${spring.security.oauth2.client.registration.client_local.redirect-uri}")
    private String redirectUri;
    @Value("${spring.security.oauth2.client.provider.client_local.issuer-uri}" + "/oauth2/authorize")
    private String authorizationEndpoint;
    @Value("${spring.security.oauth2.client.provider.client_local.issuer-uri}" + "/oauth2/token")
    private String tokenEndpoint;

    private static final String ACCESS_TOKEN_KEY = "cqc_api_access_token";
    private static final String REFRESH_TOKEN_KEY = "cqc_api_refresh_token";
    private static final String TOKEN_EXPIRY_KEY = "cqc_api_token_expiry";
    private static final String TOKEN_TYPE_KEY = "cqc_api_token_type";

    @Resource
    private RestTemplate restTemplate;

    /**
     * 使用授权码换取访问令牌
     */
    public OAuth2TokenResponse exchangeAuthorizationCode(String authorizationCode) throws OAuth2Exception {
        MultiValueMap<String, String> body = new LinkedMultiValueMap<>();
        body.add("grant_type", "authorization_code");
        body.add("code", authorizationCode);
        body.add("redirect_uri", redirectUri);

        return executeTokenRequest(body);
    }

    /**
     * 使用刷新令牌获取新的访问令牌
     */
    public OAuth2TokenResponse refreshAccessToken(String refreshToken) throws OAuth2Exception {
        MultiValueMap<String, String> body = new LinkedMultiValueMap<>();
        body.add("grant_type", "refresh_token");
        body.add("refresh_token", refreshToken);

        return executeTokenRequest(body);
    }

    /**
     * 执行令牌请求（公共方法）
     */
    private OAuth2TokenResponse executeTokenRequest(MultiValueMap<String, String> body) throws OAuth2Exception {

        String str = clientId + ":" + clientSecret;
        String encodeStr = Base64.getEncoder().encodeToString(str.getBytes(StandardCharsets.UTF_8));

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_FORM_URLENCODED);
        headers.add("Authorization", "Basic " + encodeStr);

        HttpEntity<MultiValueMap<String, String>> request = new HttpEntity<>(body, headers);

        try {
            ResponseEntity<OAuth2TokenResponse> response = restTemplate.postForEntity(
                    tokenEndpoint, request, OAuth2TokenResponse.class);

            if (response.getStatusCode() == HttpStatus.OK && response.getBody() != null) {
                return response.getBody();
            } else {
                logger.error("Token request failed with status: {}", response.getStatusCode());

                // 尝试解析错误响应
                String errorCode = "token_request_failed";
                String errorDescription = "Failed to obtain token: HTTP " + response.getStatusCode();

                throw new OAuth2Exception(errorCode, errorDescription);
            }
        } catch (HttpClientErrorException e) {
            logger.error("HTTP error when requesting token: {}", e.getStatusCode());

            // 尝试从错误响应中提取OAuth2错误信息
            String errorCode = "http_error";
            String errorDescription = "HTTP error: " + e.getStatusCode();

            // 这里可以添加更多逻辑来解析OAuth2错误响应
            if (e.getStatusCode() == HttpStatus.BAD_REQUEST) {
                errorCode = "invalid_request";
                errorDescription = "The request was missing a required parameter or was otherwise malformed";
            } else if (e.getStatusCode() == HttpStatus.UNAUTHORIZED) {
                errorCode = "invalid_client";
                errorDescription = "Client authentication failed";
            }

            throw new OAuth2Exception(errorCode, errorDescription, e);
        } catch (Exception e) {
            logger.error("Error executing token request", e);
            throw new OAuth2Exception("request_failed", "Error executing token request: " + e.getMessage(), e);
        }
    }

    /**
     * 将令牌信息存储到Session中
     */
    public void storeTokensInSession(OAuth2TokenResponse tokenResponse, HttpSession session) {
        session.setAttribute(ACCESS_TOKEN_KEY, tokenResponse.getAccessToken());

        // 只有当刷新令牌存在时才存储（某些授权服务器可能不会在刷新请求中返回新的刷新令牌）
        if (tokenResponse.getRefreshToken() != null) {
            session.setAttribute(REFRESH_TOKEN_KEY, tokenResponse.getRefreshToken());
        }

        // 计算并存储令牌过期时间
        long expiryTimestamp = Instant.now().getEpochSecond() + tokenResponse.getExpiresIn();
        session.setAttribute(TOKEN_EXPIRY_KEY, expiryTimestamp);

        // 存储令牌类型
        if (tokenResponse.getTokenType() != null) {
            session.setAttribute(TOKEN_TYPE_KEY, tokenResponse.getTokenType());
        }

        logger.info("Tokens stored in session for user");
    }

    /**
     * 检查令牌是否有效，必要时自动刷新
     */
    public boolean ensureValidToken(HttpSession session) {
        String accessToken = (String) session.getAttribute(ACCESS_TOKEN_KEY);
        Long expiryTime = (Long) session.getAttribute(TOKEN_EXPIRY_KEY);
        String refreshToken = (String) session.getAttribute(REFRESH_TOKEN_KEY);

        // 如果没有访问令牌，无法继续
        if (accessToken == null) {
            logger.warn("No access token found in session");
            return false;
        }

        // 如果令牌没有过期，直接返回有效
        if (expiryTime != null && Instant.now().getEpochSecond() < expiryTime) {
            return true;
        }

        // 如果没有刷新令牌，无法刷新
        if (refreshToken == null) {
            logger.warn("Access token expired but no refresh token available");
            return false;
        }

        // 尝试刷新令牌
        try {
            logger.info("Access token expired, attempting refresh...");
            OAuth2TokenResponse newTokens = refreshAccessToken(refreshToken);
            storeTokensInSession(newTokens, session);
            logger.info("Access token refreshed successfully");
            return true;
        } catch (OAuth2Exception e) {
            logger.error("Failed to refresh access token", e);
            // 刷新失败，清除会话中的令牌
            clearTokensFromSession(session);
            return false;
        }
    }

    /**
     * 从Session中获取有效的访问令牌（必要时自动刷新）
     */
    public String getValidAccessToken(HttpSession session) throws OAuth2Exception {
        if (ensureValidToken(session)) {
            return (String) session.getAttribute(ACCESS_TOKEN_KEY);
        } else {
            throw new OAuth2Exception("No valid access token available");
        }
    }

    /**
     * 从Session中清除所有令牌信息
     */
    public void clearTokensFromSession(HttpSession session) {
        session.removeAttribute(ACCESS_TOKEN_KEY);
        session.removeAttribute(REFRESH_TOKEN_KEY);
        session.removeAttribute(TOKEN_EXPIRY_KEY);
        session.removeAttribute(TOKEN_TYPE_KEY);
        logger.info("Tokens cleared from session");
    }

    /**
     * 检查Session中是否有有效的令牌
     */
    public boolean hasValidToken(HttpSession session) {
        try {
            return ensureValidToken(session);
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * 生成随机的state参数
     */
    public String generateState() {
        SecureRandom secureRandom = new SecureRandom();
        byte[] randomBytes = new byte[16];
        secureRandom.nextBytes(randomBytes);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(randomBytes);
    }


    /**
     * 构建授权URL
     */
    public String buildAuthorizationUrl(String state) {
        return authorizationEndpoint + "?response_type=code" +
                "&client_id=" + clientId +
                "&redirect_uri=" + redirectUri +
                "&state=" + state;
    }
}

