package com.opslens.demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

/** 고객사 서비스를 흉내 내는 앱. 스스로 트래픽을 만들고, 관리 API로 장애를 주입·해제한다. */
@SpringBootApplication
@EnableScheduling
public class DemoAppApplication {

    public static void main(String[] args) {
        SpringApplication.run(DemoAppApplication.class, args);
    }
}
