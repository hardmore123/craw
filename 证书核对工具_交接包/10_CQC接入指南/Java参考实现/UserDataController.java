package com.example.controller;

import com.example.service.CqcApiService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import javax.annotation.Resource;
import javax.servlet.http.HttpSession;
import java.text.SimpleDateFormat;
import java.util.Date;

@RestController
@RequestMapping("/cqc")
public class UserDataController {

    @Value("${env.gateway}")
    private String gateway;

    @Value("${spring.security.oauth2.client.registration.client_local.client-id}")
    private String clientId;

    @Resource
    private CqcApiService apiService;

    @GetMapping("/api/{method}/{version}/{startDate}/{endDate}/{pageNum}")
    public CqcApiService.ApiResponse get(HttpSession session,
                                                   @PathVariable("method") String method,
                                                   @PathVariable("pageNum") int pageNum,
                                                   @PathVariable("startDate") String startDate,
                                                   @PathVariable("endDate") String endDate,
                                                   @PathVariable("version") String version) {

        String timestamp = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss").format(new Date());

        String urlGateway = gateway + "?client_id=" + clientId +
                "&method=" + method +
                "&version=" + version +
                "&startDate=" + startDate +
                "&endDate=" + endDate +
                "&timestamp=" + timestamp +
                "&pageNum=" + pageNum;

        // 调用CQC开放平台API获取用户数据
        // 如果token过期，会自动刷新
        return apiService.callCqcApi(urlGateway, session);
    }

    /*@GetMapping("/api/user-posts")
    public ResponseEntity<String> getUserPosts(HttpSession session) {
        // 调用CQC开放平台API获取用户帖子
        // 如果token过期，会自动刷新
        CQCApiService.ApiResponse response = apiService.callCqcApi(urlGateway, session);
    }*/
}
