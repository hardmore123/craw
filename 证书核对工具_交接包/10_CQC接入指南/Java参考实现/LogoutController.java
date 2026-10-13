package com.example.controller;

import com.example.service.OAuth2Service;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import javax.annotation.Resource;
import javax.servlet.http.HttpServletResponse;
import javax.servlet.http.HttpSession;
import java.io.IOException;

@RestController
public class LogoutController {

    @Resource
    private OAuth2Service oauth2Service;

    @Value("${env.logout}")
    private String logoutUrl;

    /**
     * OAuth2单独登出接口
     * <p>
     * 功能说明：用于在不终止业务系统用户会话的情况下，单独清除与CQC开放平台的OAuth2认证状态。
     * 典型场景：适用于需要在同一业务系统会话中切换不同开放平台用户身份的场景。
     * 使用建议：在实现业务系统完整登出流程时，建议集成此方法同时清除开放平台的认证状态。
     * </p>
     * 在业务系统用户登出时，建议使用方法内的代码同时登出开放平台的鉴权
     */
    @RequestMapping("/oauth2/logout")
    public void logout(HttpSession session, HttpServletResponse resp) throws IOException {

        oauth2Service.clearTokensFromSession(session);

        /*
         * callback_type包含json和redirect两种类型，
         * 如果不传callback_type默认跳转到授权服务的oauth2登陆页面；
         * 如果传callback_type=json，则登出成功返回json信息，示例：http://xxxxxx/open-auth/logout?callback_type=json；
         * 如果传callback_type=redirect&redirect_url=重定向地址，则登出成功后跳转至指定的redirect_url地址，示例：http://xxxxxx/open-auth/logout?callback_type=redirect&redirect_url=http://your-domain/url；
         */
        resp.sendRedirect(logoutUrl);
    }
}
