package com.example.controller;

import com.example.domain.OAuth2TokenResponse;
import com.example.service.OAuth2Exception;
import com.example.service.OAuth2Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Controller;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.servlet.mvc.support.RedirectAttributes;

import javax.annotation.Resource;
import javax.servlet.http.HttpSession;
import java.util.HashMap;
import java.util.Map;

@Controller
public class OAuth2Controller {

    @Resource
    private OAuth2Service oauth2Service;

    @Value("${env.logout}")
    private String logoutUrl;

    /**
     * 发起OAuth2授权请求
     */
    @GetMapping("/oauth2/authorize")
    public String initiateOAuth2(HttpSession session) {
        // 生成并存储state参数（防止CSRF攻击）
        String state = oauth2Service.generateState();
        session.setAttribute("oauth2_state", state);

        // 存储当前URL，以便授权后重定向回来
        // 注意：在实际应用中，你可能需要从请求中获取referer或使用其他方式
        session.setAttribute("pre_oauth_url", "/dashboard");

        // 构建授权URL并重定向
        String authorizationUrl = oauth2Service.buildAuthorizationUrl(state);
        return "redirect:" + authorizationUrl;
    }

    /**
     * 处理OAuth2回调
     */
    @GetMapping("/oauth2/callback")
    public String handleOAuth2Callback(
            @RequestParam("code") String authorizationCode,
            @RequestParam(value = "state", required = false) String state,
            HttpSession session,
            RedirectAttributes redirectAttributes) {

        try {
            // 验证state参数（如果使用了state）
            if (state != null) {
                String savedState = (String) session.getAttribute("oauth2_state");
                if (!state.equals(savedState)) {
                    redirectAttributes.addFlashAttribute("error", "Invalid state parameter");
                    return "redirect:/error";
                }
                // 清除已使用的state
                session.removeAttribute("oauth2_state");
            }

            // 使用授权码换取访问令牌
            OAuth2TokenResponse tokenResponse = oauth2Service.exchangeAuthorizationCode(authorizationCode);

            // 将令牌信息存储到Session中
            oauth2Service.storeTokensInSession(tokenResponse, session);

            // 重定向到原始请求页面或默认页面
            String redirectUrl = (String) session.getAttribute("pre_oauth_url");
            if (redirectUrl != null) {
                session.removeAttribute("pre_oauth_url");
                return "redirect:" + redirectUrl;
            }

            return "redirect:/dashboard";

        } catch (OAuth2Exception e) {
            redirectAttributes.addFlashAttribute("error", "OAuth2 authentication failed: " + e.getMessage());
            return "redirect:/error";
        }
    }

    /**
     * 检查令牌状态（可用于前端检测）
     */
    @GetMapping("/api/token/status")
    public ResponseEntity<Map<String, Object>> checkTokenStatus(HttpSession session) {
        Map<String, Object> response = new HashMap<>();

        boolean isValid = oauth2Service.hasValidToken(session);
        response.put("hasValidToken", isValid);

        if (isValid) {
            // 提醒：生产环境应避免将accessToken输出到前端
            // response.put("accessToken", session.getAttribute("cqc_api_access_token"));
            response.put("expiry", session.getAttribute("cqc_api_token_expiry"));
        }

        return ResponseEntity.ok(response);
    }

    /**
     * 手动刷新令牌（可用于测试或特殊场景）
     */
    @GetMapping("/api/token/refresh")
    public ResponseEntity<Map<String, Object>> manuallyRefreshToken(HttpSession session) {
        Map<String, Object> response = new HashMap<>();

        try {
            String refreshToken = (String) session.getAttribute("cqc_api_refresh_token");
            if (refreshToken == null) {
                response.put("success", false);
                response.put("message", "No refresh token available");
                return ResponseEntity.badRequest().body(response);
            }

            OAuth2TokenResponse newTokens = oauth2Service.refreshAccessToken(refreshToken);
            oauth2Service.storeTokensInSession(newTokens, session);

            response.put("success", true);
            response.put("message", "Token refreshed successfully");
            return ResponseEntity.ok(response);
        } catch (OAuth2Exception e) {
            response.put("success", false);
            response.put("message", "Failed to refresh token: " + e.getMessage());
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(response);
        }
    }

}
